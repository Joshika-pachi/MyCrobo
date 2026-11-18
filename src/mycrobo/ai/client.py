"""Model access: one structured-output call per file, with cost metering and a disk cache.

`Reviewer` is a small protocol so tests (and future providers) can swap the model out.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mycrobo.ai.schema import FileReview

DEFAULT_MODEL = "claude-opus-5-5"
EXTERNAL_MODEL = "external"  # answers supplied through --ai-export / --ai-import
DEFAULT_EFFORT = "medium"
PROMPT_VERSION = "1"  # bump when prompts or schema change, to invalidate the cache

# USD per million tokens (input, output). Cache writes bill 1.25x input, cache reads 0.1x.
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
# Models that accept server-side refusal fallbacks. Code under review can look like
# exploit code; a fallback keeps a classifier decline from silently skipping a file.
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class ReviewResponse:
    review: FileReview | None  # None when declined (refused) or not answered yet (deferred)
    cost_usd: float
    model: str
    refused: bool = False
    cached: bool = False


class ReviewerUnavailable(Exception):
    """The model can't be used at all (no or invalid credentials): stop the AI stage."""


class Reviewer(Protocol):
    model: str

    def review(self, system: str, prompt: str) -> ReviewResponse: ...


def estimate_cost(model: str, input_chars: int, output_tokens: int = 4000) -> float:
    """Upper-bound estimate used to stay under --max-cost before a call is made."""
    if model == EXTERNAL_MODEL:
        return 0.0
    price_in, price_out = PRICES.get(model, PRICES[DEFAULT_MODEL])
    input_tokens = input_chars / 3  # code tokenises denser than prose
    return (input_tokens * price_in * 1.25 + output_tokens * price_out) / 1_000_000


def _usage_cost(model: str, usage) -> float:
    price_in, price_out = PRICES.get(model, PRICES[DEFAULT_MODEL])
    fresh = getattr(usage, "input_tokens", 0) or 0
    written = getattr(usage, "cache_creation_input_tokens", 0) or 0
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    return (fresh * price_in + written * price_in * 1.25 + read * price_in * 0.1 + out * price_out) / 1_000_000


class AnthropicReviewer:
    def __init__(self, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT):
        import anthropic  # imported lazily so offline scans never need credentials

        self.model = model
        self.effort = effort
        self._anthropic = anthropic
        try:
            self.client = anthropic.Anthropic(max_retries=3)
        except anthropic.AnthropicError as exc:
            raise ReviewerUnavailable(f"Claude API client could not start: {exc}") from exc

    def review(self, system: str, prompt: str) -> ReviewResponse:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": 16000,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": prompt}],
            "output_format": FileReview,
            "output_config": {"effort": self.effort},
        }
        if self.model in _FALLBACK_MODELS:
            kwargs["betas"] = [_FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        try:
            response = self.client.beta.messages.parse(**kwargs)
        except (self._anthropic.AuthenticationError, self._anthropic.PermissionDeniedError) as exc:
            raise ReviewerUnavailable(f"Claude API rejected the credentials: {exc.message}") from exc
        except TypeError as exc:
            if "authentication" not in str(exc).lower():
                raise
            # The SDK found no API key, auth token or login profile.
            raise ReviewerUnavailable(
                "no Claude API credentials found (set ANTHROPIC_API_KEY, or log in with `ant auth login`)"
            ) from exc
        served_by = getattr(response, "model", None) or self.model
        cost = _usage_cost(served_by, response.usage)
        if response.stop_reason == "refusal":
            return ReviewResponse(None, cost, served_by, refused=True)
        return ReviewResponse(response.parsed_output, cost, served_by)


class CachedReviewer:
    """Wraps a reviewer; identical (model, effort, prompt) requests are answered from disk for free."""

    def __init__(self, inner: Reviewer, effort: str, directory: Path | None = None):
        self.inner = inner
        self.model = inner.model
        self.effort = effort
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
        self.directory = directory or Path(base) / "mycrobo" / "ai"

    def _path(self, system: str, prompt: str) -> Path:
        key = json.dumps([PROMPT_VERSION, self.model, self.effort, system, prompt])
        return self.directory / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    def review(self, system: str, prompt: str) -> ReviewResponse:
        path = self._path(system, prompt)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ReviewResponse(FileReview.model_validate(data["review"]), 0.0, data["model"], cached=True)
        except (OSError, ValueError, KeyError):
            pass  # cache miss or unreadable entry: make a real call
        result = self.inner.review(system, prompt)
        if result.review is not None:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"model": result.model, "review": result.review.model_dump()}), encoding="utf-8")
            except OSError:
                pass  # caching is best-effort
        return result


class ExchangeReviewer:
    """Review without an API key: hand requests to any model through files.

    export: write `<key>.request.json` (system prompt, user prompt, JSON schema) per file.
    import: read `<key>.response.json` (a FileReview) written by any reviewer, e.g. Claude
    Code. The key hashes the request, so answers only apply to the code they were written for.
    """

    def __init__(self, directory: Path, mode: str):
        if mode not in {"export", "import"}:
            raise ValueError(mode)
        self.directory = directory
        self.mode = mode
        self.model = EXTERNAL_MODEL

    @staticmethod
    def key(system: str, prompt: str) -> str:
        return hashlib.sha256(json.dumps([PROMPT_VERSION, system, prompt]).encode()).hexdigest()[:16]

    def review(self, system: str, prompt: str) -> ReviewResponse:
        key = self.key(system, prompt)
        if self.mode == "export":
            self.directory.mkdir(parents=True, exist_ok=True)
            file = prompt.splitlines()[0].removeprefix("File: ")
            request = {
                "file": file,
                "respond_with": f"{key}.response.json",
                "instructions": "Answer as the system prompt says. Write ONLY a JSON object matching `schema` "
                                f"to {key}.response.json in this directory.",
                "system": system,
                "prompt": prompt,
                "schema": FileReview.model_json_schema(),
            }
            (self.directory / f"{key}.request.json").write_text(json.dumps(request, indent=2), encoding="utf-8")
            return ReviewResponse(None, 0.0, self.model)
        path = self.directory / f"{key}.response.json"
        if not path.exists():
            return ReviewResponse(None, 0.0, self.model)  # not answered (or code changed since export)
        return ReviewResponse(FileReview.model_validate_json(path.read_text(encoding="utf-8")), 0.0, self.model)
