# AI log

How AI was used to build MyCrobo, what the humans decided, and where the AI got things wrong.

## Tools

| | |
|---|---|
| Assistant | Claude Code (desktop app, Code tab) |
| Model | Claude Opus 5.5 (`claude-opus-5-5`) |
| Period | 2026-11-17 to 2026-11-18, one continuous session |
| What the AI wrote | All source code, tests, docs, the PRD, the slop-zoo demo app and its answer key, and the recorded AI reviews in `examples/` |
| What the humans did | Set the goal and constraints, made the product decisions below, ran the project on their own machine, and tested a downloaded zip end to end |

## Human decisions

| Decision | Made by |
|---|---|
| Build "a simple, architecture-agnostic agent for security, slop, coding gaps and loose ends" for a hackathon | Team (initial brief) |
| Let the AI choose defaults and start building ("take accordingly and start working") | Team |
| Python as the implementation language | Team (chose "Start M0 in Python") |
| Keep everything local: no GitHub repos, pushes or PRs by the AI | Team |
| Commit only on explicit approval | Team (each commit was approved) |
| No API key available, so find another way to run the AI review | Team |
| Must work on Windows, from a zip, for a teammate | Team |
| Test on real, unfamiliar code ("architecture agnostic, live AI review on an unfamiliar project, real-world detection accuracy") | Team |

Decisions the AI proposed and the team accepted: CLI plus GitHub Action form; report plus suggested fixes (no auto-fix); opt-in `--ai` so a scan never spends money unasked; the trust rules for model output (secrets redacted before sending, security findings can't be dismissed, AI claims must quote real code).

## Timeline

| Phase | What the AI did | Human checkpoint |
|---|---|---|
| PRD | Wrote [docs/PRD.md](docs/PRD.md) with defaults, milestones M0 to M4, open questions | Team asked to proceed with defaults |
| M0 | CLI, finding schema, file discovery, text/JSON output, first rule | "Start M0 in Python" |
| M1 | 9 language-agnostic rules; tuned against its own source and pytest's | "Start M1" |
| M2 | Claude review layer: structured output, refusal fallbacks, cost cap, cache, secret redaction, evidence verification | "Start M2" |
| No-key path | `--ai-export` / `--ai-import`, so any model (here Claude Code itself) can answer review requests | "I can't do with the key, try doing without the key" |
| M3 | Slop Score, SARIF and Markdown output, PR diff mode, `mycrobo ci`, hardened GitHub Action, all tested against a fake GitHub only | "M3: SARIF + Action" |
| M4 | Slop zoo (25 planted issues plus traps), answer key, benchmark, recorded review, demo script, architecture doc | "Yes, build M4 demo" |
| Windows | Fixed output-encoding crash, console colors, git-output decoding; simulated CRLF and no-git checkouts | Team asked if it works on Windows |
| Submission format | Added `requirements.txt`, a unittest entry point that runs the pytest suite, and `app.py` (web UI + JSON API on port 8000) so the hackathon's `arena.json` commands work; ran all three in a clean copy with a fresh Python 3.13 environment | Team supplied the `arena.json` format |
| Real-world evaluation | Scanned 930k lines of npm, pydantic, Anthropic SDK, Ruby and Perl; labelled precision samples; seeded recall; independent AI review of Ruby's WEBrick ([docs/REALWORLD.md](docs/REALWORLD.md)) | Team ran the zip and asked for this missing step |

## Mistakes the AI made (and how they were caught)

Listed because they're part of an honest record, and most were caught by MyCrobo or its tests.

| Mistake | Caught by | Fix |
|---|---|---|
| Rules flagged their own regex and docstrings (secret in a regex, `C#` read as a comment) | MyCrobo scanning itself | Multi-line string awareness, value filters |
| Wrote an unused `import re` with a fake "keep import stable" line: slop | AI review of its own diff | Removed |
| Live-check sample's docstring listed every planted bug, leaking answers to the reviewer | AI, before answering | Moved the list to a README |
| `action.yml` was invalid YAML (an unquoted `: ` in a description), so GitHub would reject the whole Action | Parsing with a real YAML parser | Quoted; YAML parse added to the tests |
| Three test bugs (miscounted fixture, `per_page` read as the page number, regex matching a comment) | Failing tests | Tests fixed; product code was right |
| Windows: redirected output crashed on `✓`/emoji; `git diff` decoded in the legacy code page | Simulating Windows' cp1252 encoding | UTF-8 output and decoding |
| Folders ignored by a parent git repo scanned **0 files** and reported "Clean" | Real-world evaluation | Fallback to walking the folder, plus a warning |
| Perl unrecognised; unknown files scored 100/100 Critical | Real-world evaluation | Language mapping, line counting |
| Several rules very noisy on mature human code (0 actionable in the first sample for secrets, placeholders, stubs) | Labelling a random sample | Tuned, then re-measured on fresh samples |
| Tuning introduced new false positives (`# ...` in doc examples, `# Other … methods` headers) and one recall hole | Fresh validation samples; seeded recall | Fixed; regression tests added |
| `python -m unittest discover` (the arena.json test command) ran **0 tests**, because `tests/` wasn't a package | Running the command in a clean copy | Added `tests/__init__.py`; confirmed the entry point fails when a test fails |
| Quoted "down from 1,148" in the evaluation report; the real number was 66 | AI recomputed from the saved scan before committing | Corrected |

## AI-generated evidence (disclosed)

- **The slop zoo and its answer key were written by the AI that wrote the rules.** Its 100% scores show the rules work as designed; they're not an independent accuracy claim.
- **Both recorded AI reviews were written by Claude in this session, not by a live API call.**
  - `examples/slop-zoo-review/`: the reviewer had also written the zoo.
  - `examples/realworld/webrick-review/`: the reviewer had never seen that code. 4 of its 8 findings were reproduced by running code.
- **Precision labels in `docs/REALWORLD.md` are one reviewer's judgement (the AI).** The samples are seeded so others can relabel them.

## Not verified

- A live `--ai` run against the Claude API (no API key was available). The request shape is covered by tests.
- The GitHub Action on GitHub itself (kept local by decision). It was tested with recorded fake GitHub requests.
- A real Windows machine. Windows conditions were simulated; the team unzipped the project and ran every getting-started command themselves.
