# MyCrobo — Product Requirements Document

| | |
|---|---|
| **Product** | MyCrobo — a code-hygiene and security review agent |
| **Context** | Hackathon submission (deliverable: public repo link) |
| **Status** | Draft v0.1 — defaults chosen, open questions listed in §14 |
| **Date** | 2026-11-17 |

---

## 1. Summary

MyCrobo is a review agent that works on any codebase. It finds three kinds of problems that ordinary linters miss or fail to connect:

1. **Security risks**: hard-coded secrets, injection paths, unsafe configs, dependencies that don't exist or look suspicious.
2. **Slop**: low-quality code, often AI-generated, such as made-up APIs, placeholder logic, padded comments and near-duplicate blocks.
3. **Coding gaps and loose ends**: TODO/FIXME notes, stub functions, mock data on production paths, swallowed errors, missing validation, untested critical paths.

It works with any language, framework or architecture. It doesn't need per-language plugins or framework conventions. Instead it combines **language-neutral checks** with **LLM reasoning grounded in the repo's own patterns**.

It ships as one engine with two entry points:
- **CLI**: `mycrobo scan <path>` runs locally on any repo.
- **GitHub Action**: runs on every pull request and posts review comments plus a summary.

## 2. Problem

- Teams now ship large amounts of AI-assisted code. That code *looks* finished but often contains stubs, invented package names, swallowed exceptions and placeholder secrets.
- Existing tools are split up. Linters check one language at a time, secret scanners only look for secrets, and SAST tools are heavy and noisy. None of them asks whether a piece of code is actually finished.
- Reviewers miss loose ends because they only see the diff, not the repo-wide pattern the diff breaks.
- Most tools need setup for each stack. Polyglot repos and monorepos end up with patchy coverage.

## 3. Goals and non-goals

### Goals (hackathon scope)
- G1. Run on **any repo with no configuration** and return useful findings in under 2 minutes for a medium repo (~50k LOC, diff mode much faster).
- G2. Detect the four finding categories in §6 with **one severity scale and one evidence format**.
- G3. Every finding is **actionable**: file, line, why it matters, evidence, and a suggested fix (patch where feasible).
- G4. **Low noise**: deterministic checks first, LLM only for triage and reasoning, and a verification pass that drops findings it can't back up.
- G5. A **demo that convinces in 2 minutes**: a PR on a seeded sample repo gets a clear MyCrobo review.

### Non-goals (for the hackathon)
- Replacing full SAST/DAST suites or pen-testing.
- Applying fixes or merging on its own (it only suggests).
- A hosted SaaS dashboard, user accounts or billing.
- IDE plugins (post-hackathon).
- Proving vulnerabilities by running exploits.

## 4. Users

| Persona | Need | How MyCrobo helps |
|---|---|---|
| **Solo / indie developer** using AI coding tools | "Is this AI-written code actually done and safe?" | `mycrobo scan .` before shipping |
| **Team reviewer / tech lead** | Catch loose ends in PRs without reading every line | PR comments and a merge-readiness summary |
| **Security-minded engineer** | Fast first pass on an unfamiliar or legacy repo | Full-repo scan with SARIF output for the GitHub Security tab |

Primary persona for the demo: **team reviewer on a PR**.

## 5. User stories

1. As a developer, I run `mycrobo scan .` and get a ranked list of findings in my terminal within a couple of minutes.
2. As a reviewer, I open a PR and see MyCrobo comments on the exact lines that introduced loose ends or risks, each with a suggested fix.
3. As a lead, I see a **PR summary** with counts by category and severity and a single **Slop Score** (0–100) so I can decide whether it's ready.
4. As a security engineer, I export findings as **SARIF** and see them in GitHub's code-scanning UI.
5. As any user, I can suppress a finding (`# mycrobo-ignore <rule-id>` inline, or in `.mycrobo.yml`) so it never comes back.
6. As any user, I can choose `--mode diff` (changed lines plus their context) or `--mode full` (whole repo).
7. As a cost-conscious user, I can run `--offline`, which uses only the deterministic checks with no LLM calls.

## 6. What it detects

Every finding has: `id`, `category`, `rule`, `severity` (critical / high / medium / low / info), `confidence` (high / medium / low), `file`, `line_range`, `evidence`, `explanation`, `suggested_fix`.

### 6.1 Security
| Rule (examples) | How it's detected |
|---|---|
| Hard-coded secrets / API keys / private keys | Entropy and pattern checks (language-neutral), confirmed by the LLM (real key vs. placeholder) |
| Injection sinks fed by user input (SQL, shell, eval, path traversal, SSRF) | Pattern finds the sink; LLM traces the data flow within the file and its call sites |
| Insecure config (debug on, permissive CORS, `0.0.0.0` admin, TLS verification off, wildcard IAM) | Patterns over code, YAML, JSON, TOML, Dockerfile and Terraform |
| **Hallucinated / typo-squatted dependencies** | Dependency manifests checked against the package registry (exists? suspiciously new? name close to a popular package?) |
| Committed `.env` files / credentials in CI YAML | File discovery plus patterns |

### 6.2 Slop (low-quality, often AI-generated code)
| Rule | How it's detected |
|---|---|
| Calls to APIs or methods that don't exist in the imported library or the repo | Symbol index of the repo plus LLM check against the declared dependency |
| Placeholder logic (`return true`, `pass`, `// implement later`, hard-coded sample responses) | Patterns plus LLM |
| Padded or obvious comments that restate the code | LLM, limited to the files that changed |
| Near-duplicate blocks (copy-paste with small edits) | Token-shingle similarity, language-neutral |
| Code that breaks the repo's own conventions (error style, logging, naming) | LLM given a "repo conventions" summary (§8.3) |

### 6.3 Loose ends
| Rule | How it's detected |
|---|---|
| TODO / FIXME / HACK / XXX, especially in new code | Patterns |
| Stub functions (`throw NotImplemented`, empty bodies, `...`) | Patterns plus a lightweight parse where available |
| Mock or fake data on production paths | Pattern on names (mock, fake, dummy, lorem) plus path heuristics (not under `test/`) |
| Swallowed errors (empty catch/except, `_ = err`) | Patterns plus LLM |
| Leftover debug code (`console.log`, `print`, `debugger`, `breakpoint()`) | Patterns |
| Unused new code (functions or exports that nothing references) | Repo symbol index |

### 6.4 Coding gaps
| Rule | How it's detected |
|---|---|
| New or changed public functions with no tests | Map source files to test files by name and path; LLM confirms it matters |
| Missing input validation on entry points (handlers, CLI args, message consumers) | LLM, given entry points found by heuristics |
| Missing error handling on I/O, network or DB calls | Patterns plus LLM |
| Half-finished features (flag added but never read, route declared but no handler) | Symbol index plus LLM |

## 7. Functional requirements

### 7.1 CLI
- `mycrobo scan [path] [--mode diff|full] [--base <ref>] [--format text|md|json|sarif] [--out <file>] [--offline] [--severity <min>] [--max-cost <usd>]`
- Exit code `1` if any finding at or above `--fail-on` severity (default: `high`), otherwise `0`, so CI can gate on it.
- Readable terminal output, grouped by category and sorted by severity.

### 7.2 GitHub Action
- `uses: <org>/mycrobo@v1` with inputs mirroring the CLI flags.
- Posts:
  - **inline review comments** on changed lines (deduplicated, max N per PR, configurable);
  - **one summary comment** with Slop Score, counts by category and severity, and the top 5 issues;
  - optional **SARIF upload** to code scanning.
- Uses `GITHUB_TOKEN` and an LLM API key from repo secrets. Never logs secrets.

### 7.3 Configuration (`.mycrobo.yml`, optional)
- Include/exclude globs, rules to turn off, severity overrides, custom patterns (regex plus message), max findings, LLM model choice, cost cap.
- Inline suppression: `mycrobo-ignore <rule-id> [reason]`.

### 7.4 Outputs
- **text** (terminal), **md** (report file / PR comment), **json** (machine-readable, stable schema), **sarif** 2.1.0.
- **Slop Score**: 0 (clean) to 100, a weighted, size-normalised sum of findings. The formula is documented in the README so judges can check it.

### 7.5 Suggested fixes
- For high-confidence findings, a unified-diff patch the user can apply (`mycrobo fix --apply <id>` is a stretch goal).
- Otherwise a short natural-language fix.

## 8. How it works (architecture)

The engine doesn't depend on any language or architecture. Pipeline:

```
 ┌───────────┐   ┌──────────────┐   ┌───────────────┐   ┌──────────────┐   ┌────────────┐   ┌──────────┐
 │ 1.Discover│──▶│ 2.Signals    │──▶│ 3.Context     │──▶│ 4.LLM Reason │──▶│ 5.Verify & │──▶│ 6.Report │
 │ files,    │   │ (deterministic│  │ builder       │   │ (triage,     │   │ dedupe     │   │ text/md/ │
 │ langs,    │   │ checks: regex,│  │ (snippets,    │   │ explain,     │   │ (drop      │   │ json/    │
 │ manifests,│   │ entropy, dup, │  │ call sites,   │   │ new findings,│   │ unbacked,  │   │ sarif/PR │
 │ diff      │   │ deps, symbols)│  │ conventions)  │   │ fixes)       │   │ score)     │   │ comments │
 └───────────┘   └──────────────┘   └───────────────┘   └──────────────┘   └────────────┘   └──────────┘
```

### 8.1 Discover
- Walk the repo while respecting `.gitignore`. Identify file types by extension and shebang. Skip vendored, generated and binary files.
- Find manifests (`package.json`, `requirements.txt`, `pyproject.toml`, `go.mod`, `pom.xml`, `Cargo.toml`, `Gemfile`, …) and infrastructure files (Dockerfile, `*.tf`, CI YAML, k8s).
- In diff mode, compute changed hunks against `--base`.

### 8.2 Signals (deterministic, no LLM, works offline)
- A rule pack of language-neutral regexes and heuristics (secrets, TODOs, debug calls, empty catch blocks across common syntaxes, placeholder returns).
- Entropy checks for secrets.
- Token-shingle duplicate detection.
- A **lightweight symbol index**: definitions and references via tree-sitter where a grammar exists, falling back to regex. This keeps the engine language-neutral while still allowing "unused" and "doesn't exist" checks.
- Dependency existence and typosquat check against public registries (cached; skipped when `--offline`).

### 8.3 Context builder
- For each candidate: the surrounding code, the definitions it references and 1–2 call sites.
- A **repo conventions profile**, built once per run from a sample of files: error-handling style, logging approach, test layout, naming. This is how MyCrobo checks code against *this* repo's architecture without hard-coding any architecture.

### 8.4 LLM reasoning
- Batches candidates per file to cut cost.
- Tasks: (a) confirm or reject each deterministic signal, (b) find gap and slop issues the patterns can't express, (c) write the explanation and fix.
- Structured JSON output that is checked against the finding schema.
- Model: a fast model for triage and a stronger model for deep reasoning on high-severity candidates (configurable; defaults in §11).

### 8.5 Verify and dedupe
- Drop findings whose cited evidence line doesn't exist or doesn't contain the claimed code. This is the main guard against the LLM making things up.
- Merge duplicates across rules. Apply suppressions and severity overrides. Compute the Slop Score.

### 8.6 Report
- Renderers for each output format, plus the GitHub adapter (review comments, summary, SARIF upload).

## 9. Non-functional requirements

| Area | Requirement |
|---|---|
| **Speed** | Diff mode < 60 s for a typical PR; full mode < 2 min for ~50k LOC (signals run in parallel, LLM calls batched) |
| **Cost** | Hard `--max-cost` cap; offline mode costs $0; LLM only sees candidates and context, never the whole repo |
| **Precision** | Target ≥ 80 % of reported findings judged valid on the demo repo; low-confidence findings hidden by default |
| **Privacy** | Only snippets are sent to the LLM; secrets are **redacted** in prompts and output (show only the first and last 4 characters) |
| **Determinism** | Same input gives the same deterministic findings; LLM run at temperature 0; results cached by content hash |
| **Portability** | Runs on macOS/Linux/Windows and in a GitHub Actions runner; single install command |
| **Extensibility** | Rules are data (YAML), so you add a rule without touching engine code |

## 10. Demo plan (hackathon)

1. **Slop zoo repo** (`examples/slop-zoo/`): a small polyglot app (e.g. a TS API, a Python worker and a Terraform file) seeded with about 20 known issues across all four categories. Its answer key (`expected.json`) lets us **measure precision and recall** live.
2. **Live flow**: open a PR that adds an "AI-written" feature, then the MyCrobo Action runs and posts inline comments and a summary with the Slop Score. Apply one suggested fix and show the score drop.
3. **Agnostic proof**: run the CLI on 2–3 well-known public repos in different languages, with no config.
4. **Numbers slide**: precision and recall on the slop zoo, run time, cost per scan, and offline vs LLM comparison.

## 11. Technology choices (recommended defaults; yours to change)

| Concern | Default | Why |
|---|---|---|
| Language | **Python 3.11+** (alternative: TypeScript/Node) | Fast to build, good tree-sitter bindings, easy to package as an Action |
| Parsing | tree-sitter (optional grammars) + regex fallback | Keeps the engine language-neutral |
| LLM | Claude API: `claude-sonnet-5-5` for triage, `claude-opus-5-5` for deep checks | Strong code reasoning, structured output; model is configurable |
| Packaging | `pip install mycrobo` / `pipx`; Docker image for the Action | One command install |
| Tests | pytest + slop zoo answer key as a regression benchmark | Shows quality to judges |

## 12. Success metrics

- **Precision** ≥ 80 % and **recall** ≥ 70 % on the slop zoo answer key.
- Runs with **zero config** on at least 3 repos in different languages.
- PR review posted end to end in under 60 s.
- Judges understand the value within the first 30 s of the demo.

## 13. Milestones (time-boxed by hackathon length)

| Phase | Deliverable |
|---|---|
| **M0 — Skeleton** | Repo setup, CLI scaffold, finding schema, text/JSON output |
| **M1 — Deterministic core** | Discovery, rule pack (secrets, TODOs, stubs, debug code, empty catch, placeholders), duplicate detection, offline mode working |
| **M2 — LLM layer** | Context builder, conventions profile, triage and gap detection, verification pass, suggested fixes |
| **M3 — Integrations** | GitHub Action (inline comments + summary), SARIF, Markdown report, Slop Score |
| **M4 — Demo polish** | Slop zoo with answer key, benchmark script, README with GIF/video, architecture diagram |
| **Stretch** | Dependency typosquat check, `fix --apply`, unused-code detection via symbol index, IaC rules |

**Cut line if time runs out:** M0–M2 plus Markdown output and the slop zoo demo. The CLI alone is still a complete submission.

## 14. Risks and mitigations

| Risk | Mitigation |
|---|---|
| LLM invents findings | Evidence-line verification (§8.5); only high-confidence findings shown by default |
| Too noisy, so demo looks bad | Severity threshold, max findings per PR, seeded demo repo tuned ahead of time |
| API cost / rate limits during demo | Caching by content hash, `--max-cost`, pre-recorded backup run, offline mode fallback |
| "Agnostic" claim challenged | Run live on repos in multiple languages; regex fallback when no grammar exists |
| Secrets leak through prompts or logs | Redaction before prompt construction and in all output |
| Scope creep | Cut line in §13 |

## 15. Open questions (answers will refine this PRD)

1. Hackathon **theme and judging criteria**, required or sponsored tech, duration, team size, and what else must be submitted (video, slides, live URL).
2. Confirm the **priority order** of categories. Current default: Security > Loose ends > Slop > Coding gaps.
3. Confirm **Python vs TypeScript** for the implementation.
4. **LLM budget** and provider constraints. Is an offline-only fallback required?
5. Should **IaC and CI config** scanning be in the core scope or a stretch goal?
6. Is there a competitor you want to beat directly (CodeRabbit, SonarQube, Snyk, Semgrep)? This affects the positioning section.
7. Does the name **MyCrobo** carry a meaning or brand to reflect in the README and pitch?

## 16. Decision log

| # | Decision | Chosen default | Alternatives |
|---|---|---|---|
| D1 | Form factor | CLI + GitHub Action | Hosted SaaS, IDE plugin |
| D2 | Action level | Report + suggested fixes | Auto-fix PRs |
| D3 | Detection approach | Deterministic signals → LLM triage → verification | Pure LLM; pure static tools (Semgrep/gitleaks wrapped) |
| D4 | Language-agnostic strategy | Regex + tree-sitter where available + repo conventions profile | Per-language plugins |
| D5 | Scan scope | Both diff and full modes; diff is the default in CI | Full only |
| D6 | LLM default (decided during M2) | **Opt-in `--ai`**; rules-only is the default, so a scan never spends money unasked. Replaces the `--offline` flag in §5 and §7.1 | LLM on by default with `--offline` to disable |
| D7 | Model output trust (decided during M2) | Model cannot drop security findings; new issues need verbatim evidence; secrets redacted before sending | Trust model verdicts as-is |
