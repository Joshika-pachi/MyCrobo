# MyCrobo

An architecture-agnostic review agent that finds **security risks**, **AI slop**, **coding gaps** and **loose ends** in any codebase — any language, framework or layout, zero config.

> Status: **M4 — demo-ready.** Nine language-agnostic rules (offline, free), an optional Claude review (`--ai`, or `--ai-export`/`--ai-import` with no API key), a Slop Score, SARIF / Markdown output, PR diff mode and a GitHub Action.
>
> **New here?** Follow [GETTING_STARTED.md](GETTING_STARTED.md): install, run and the exact output to expect, on Mac, Windows or Linux.
>
> **Try it in 30 seconds:** `uv sync && uv run mycrobo scan examples/slop-zoo`. Then see [docs/DEMO.md](docs/DEMO.md) (2-minute demo), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/PRD.md](docs/PRD.md).

## Benchmark

[`examples/slop-zoo`](examples/slop-zoo) is a TypeScript + Python + Terraform app with 25 planted issues and a set of traps, scored against an answer key:

| Mode | Precision | Recall |
|---|---|---|
| Rules only (offline, free) | 100% | 20/20 rule-detectable issues, 80% overall |
| Rules + AI review ([recorded](examples/slop-zoo-review)) | ≥ 96% | **25/25** |

```bash
uv run python scripts/benchmark.py [--ai-import examples/slop-zoo-review | --ai]
```

We wrote both the zoo and the rules, so read this as "works as designed". The independent check is pytest's own source, where the security, placeholder, debug and mock rules make no false calls. See the [zoo README](examples/slop-zoo/README.md) for the honesty notes.

## What it finds

| Rule | Category | Catches |
|---|---|---|
| `security/hardcoded-secret` | Security | AWS/GitHub/Anthropic/OpenAI/Stripe/Slack/Google keys, private keys, JWTs, passwords in URLs, high-entropy `secret = "..."` assignments (values redacted in output) |
| `security/committed-env-file` | Security | `.env` files with real values that are tracked or not git-ignored |
| `slop/placeholder-comment` | Slop | `// ... rest of the code`, `in a real app you would…`, `for now, just return…` |
| `slop/duplicate-block` | Slop | Copy-pasted blocks of 6+ meaningful lines, within or across files |
| `loose-ends/marker-comment` | Loose ends | TODO / FIXME / HACK / XXX in real comments |
| `loose-ends/stub-implementation` | Loose ends | `-> User` functions whose body is `pass`, `throw new Error("Not implemented")`, `todo!()`, `NotImplementedException` |
| `loose-ends/swallowed-error` | Loose ends | `except: pass`, empty `catch {}`, `.catch(() => {})`, empty `if err != nil {}`, `rescue nil` |
| `loose-ends/debug-statement` | Loose ends | `console.log`, `debugger`, `breakpoint()`, `pdb.set_trace()`, `dbg!`, `binding.pry`, `var_dump` |
| `loose-ends/mock-data` | Loose ends | `mockUsers = [...]`, `@example.com` addresses, lorem ipsum in production paths |

Run `mycrobo rules` to list them. Rules skip test files where a finding there would be noise (debug prints, mocks, generic secrets); provider-format keys are flagged everywhere.

**Precision first.** Comments are found using the file's own comment syntax, string literals (including multi-line docstrings and template literals) are never mistaken for code, explained handlers (`except X:  # reason`) are respected, and abstract methods / no-op overrides are not called stubs. Tuned against real-world code (pytest's own source): 0 false positives in the security, placeholder, debug and mock rules.

## AI review (`--ai`)

Off by default. Rules alone are free and offline; `--ai` sends each selected file to Claude (`claude-opus-5-5` by default) once.

```bash
export ANTHROPIC_API_KEY=...          # or log in with `ant auth login`
uv run mycrobo scan . --ai                       # triage + gap finding, capped at $1.00
uv run mycrobo scan . --ai --max-cost 0.25 --max-files 10 --effort low
```

What it does per file:
1. **Triage**: confirms, dismisses or marks uncertain each rule finding, and rewrites the explanation and fix for this repo.
2. **Gap finding**: up to 5 issues rules can't express: missing input validation, unhandled I/O errors, injection paths, calls to APIs that don't exist, code contradicting its own comments. These show up as `ai/*` rules, tagged `AI`.

Which files: those with rule findings (most severe first), then files that look like they handle input or I/O (handlers, routes, uploads, `request.*`, `open(`, `subprocess`), up to `--max-files`.

What it trusts and what it doesn't:
- **Secrets are redacted before anything is sent.** The model sees `<REDACTED>`, never the key.
- **The model cannot hide a security finding.** A security finding it calls a false positive is kept at low confidence.
- **Every AI issue must quote real code.** Quotes that don't exist in the file are discarded (reported as "unverifiable claims"); wrong line numbers are corrected to where the quote actually is.
- **File content is treated as untrusted data** in the prompt, so instructions planted in code are ignored.
- **The budget is enforced before each call** (`--max-cost`, estimated up front, metered from real token usage). Over budget, refused or failed files keep their rule-only results, and the summary says so.
- **Unchanged files are free on re-runs.** Reviews are cached in `~/.cache/mycrobo/ai` (`--no-cache` to skip).

### No API key? Let any model answer (e.g. Claude Code)

```bash
uv run mycrobo scan . --ai-export review-box/   # writes one <key>.request.json per file
# ...a model answers each request with <key>.response.json (Claude Code can do this directly)...
uv run mycrobo scan . --ai-import review-box/   # verifies and applies the answers
```

Each request contains the system prompt, the file prompt and the JSON schema to answer with. The full pipeline runs on import: answers are validated against the schema, new issues must quote real code, and security findings can't be dismissed. Request keys hash the code, so an answer is ignored if the file changed after export.

Repo conventions (languages, logging, error-handling style, validation libraries, test layout) are detected locally and given to the model, so it judges code against *this* repo rather than a framework it assumes.

## Install (development)

```bash
uv sync
```

## Usage

```bash
uv run mycrobo scan .                          # human-readable report
uv run mycrobo scan path/to/repo --format json --out report.json
uv run mycrobo scan . --severity medium        # hide low/info findings
uv run mycrobo scan . --fail-on medium         # CI gate: exit 1 on medium+
uv run mycrobo scan . --diff origin/main        # only lines changed on this branch
uv run mycrobo scan . --format sarif --out mycrobo.sarif   # GitHub code scanning / IDEs
uv run mycrobo scan . --format md              # Markdown report (what the PR summary shows)
uv run mycrobo rules                           # list rules
```

### Slop Score

One 0-100 number: severity points weighted by confidence, per 1,000 lines of code, `100 * (1 - e^(-density/20))`. Any credible critical finding floors it at 75, so a leaked key can't hide in a big repo. In diff mode it scores the change itself (findings on changed lines / lines added). Labels: Clean < 10 ≤ Minor < 25 ≤ Sloppy < 50 ≤ Risky < 75 ≤ Critical. Formula and weights: [src/mycrobo/score.py](src/mycrobo/score.py).

## GitHub Action

[`action.yml`](action.yml) runs MyCrobo on every pull request:

- scans **only the lines the PR changes** (files are still read in full for context);
- posts **inline review comments** on those lines, with explanation and fix; reruns skip findings already commented on;
- keeps **one summary comment** (Slop Score, counts, top findings) and edits it on every push;
- uploads **SARIF** to code scanning, even when the check fails;
- fails the check at `fail-on` severity (default `high`).

Copy [examples/github-workflow.yml](examples/github-workflow.yml) to `.github/workflows/` in the repo to review. Inputs: `fail-on`, `severity`, `comment`, `max-comments`, `upload-sarif`, `ai`, `anthropic-api-key`, `model`, `max-cost`, `path`.

Hardening: inputs reach the shell only through environment variables (a test fails if `${{ }}` ever appears inside a `run:` block), the workflow requests only the permissions it uses, and fork PRs with a read-only token still get SARIF and the step summary.

The same flow runs locally without touching GitHub:

```bash
uv run mycrobo ci --base origin/main --dry-run requests.json   # writes the API calls it would make
```

| Exit code | Meaning |
|---|---|
| 0 | No finding at or above `--fail-on` (default `high`) |
| 1 | Gate failed |
| 2 | Usage or runtime error |

### Suppressing a finding

Add `mycrobo-ignore` on the line or the line above. Name rule ids to limit it:

```python
# TODO: tracked in #42  mycrobo-ignore loose-ends/marker-comment
```

## How it works

```
discover files ─▶ run rules ─▶ apply suppressions ─▶ filter & sort ─▶ report (text / JSON)
```

- **Discovery** respects `.gitignore` (via `git ls-files`), skips vendored, generated and binary files, and tags each file with a language guessed from extension, filename or shebang.
- **Rules** are small language-agnostic classes (`src/mycrobo/rules/`) that yield `Finding`s.
- **Findings** share one schema: rule, category, severity, confidence, location, evidence, explanation, suggested fix, plus a content-hash `id` that stays stable when lines move.

## Project layout

```
src/mycrobo/
  cli.py        argparse entry point (`mycrobo scan`)
  engine.py     scan pipeline + inline suppressions
  discover.py   file discovery & language detection
  models.py     Finding / Severity / Category / ScanResult
  report.py     text and JSON renderers
  text.py       comment/string/code splitting for any language
  score.py      Slop Score
  diff.py       changed lines vs a git base (diff mode)
  github.py     PR review comments + summary comment (stdlib HTTP)
  rules/        rule interface + one module per rule family
  ai/           optional Claude review: context, prompts, client, verification
tests/          pytest suite and fixtures (incl. benchmark regression)
scripts/        benchmark.py (precision / recall on the slop zoo)
examples/       slop-zoo, recorded AI review, live-check sample, example workflow
docs/           PRD, architecture, demo script
action.yml      GitHub Action
```

## Tests

```bash
uv run pytest
```

## Troubleshooting

**`ModuleNotFoundError: No module named 'mycrobo'` on macOS.** If the repo lives in an iCloud-synced folder (e.g. `~/Documents`), macOS can mark files in `.venv` as hidden, and recent Python versions skip hidden `.pth` files, which breaks editable installs. Use a regular install instead (sources are rebuilt automatically on change):

```bash
uv run --no-editable mycrobo scan .
```
