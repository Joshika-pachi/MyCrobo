# 2-minute demo script

Setup once: `uv sync`. Every command below works offline, with no API key.

## 0:00 The problem (15 s)
> "AI writes code that looks finished: stubs, `// ... rest of the code`, swallowed errors, hard-coded keys, copy-paste that drifts. Linters see one language at a time; nobody checks whether the code is *done*. MyCrobo does, on any codebase."

## 0:15 Scan the zoo, rules only (30 s)
```bash
uv run mycrobo scan examples/slop-zoo
```
Point at:
- one tool, four languages: TypeScript, Python, Terraform, `.env`
- `// ... rest of the user handlers` flagged **high**: code that an AI edit deleted
- `orderTotal` copied from `cartTotal` with the **tax rate already drifted**
- secrets shown **redacted**
- **Slop Score** at the bottom

## 0:45 Add the AI layer (30 s)
```bash
uv run mycrobo scan examples/slop-zoo --ai-import examples/slop-zoo-review --severity high
```
> "Rules can't see a SQL injection or a missing auth check. The AI review can: 5 new issues, each one **verified against real code**. If a model invents a line, MyCrobo throws it away, and it can never hide a leaked key."

(Recorded review; live: `--ai` with `ANTHROPIC_API_KEY`. No key? `--ai-export`, and Claude Code answers it.)

## 1:15 The numbers (20 s)
```bash
uv run python scripts/benchmark.py --ai-import examples/slop-zoo-review
```
> "25 planted issues, 25 found. Traps like test mocks, abstract methods and explained handlers aren't flagged. On pytest's real source, the rules make zero false calls on secrets, placeholders, debug code and mock data."

## 1:35 In a pull request (25 s)
Show `action.yml` and `examples/github-workflow.yml`:
- only the **lines the PR changed**
- inline comments with **why** and a **fix**, never duplicated on re-push
- one summary comment with the Slop Score, and SARIF in GitHub code scanning

```bash
uv run pytest -q   # 51 tests: rules, AI trust rules, diff mode, GitHub flow, action hardening, benchmark
```

## Likely questions
- **"Is the zoo rigged?"** We wrote it, so its recall shows the design works. Independent checks: traps plus pytest's source. `--ai` gives a fresh review.
- **"What does the AI cost?"** Capped per run (`--max-cost`, default $1); unchanged files are cached; the rules are free.
- **"Which languages?"** Anything with `#`, `//`, `--` or `<!--` comments: rules key off comment syntax, not parsers.
