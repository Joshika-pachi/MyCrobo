# Getting started

How to run MyCrobo from a downloaded zip, on Mac, Windows or Linux, and what you should see.

## 1. Unzip

Unzip the file. You'll get a folder called `mycrobo` (or `mycrobo-main` if it came from GitHub).

## 2. Install the tools it needs (one time)

- **Python 3.11 or newer.** Check with `python3 --version` (Windows: `python --version`). If it's missing or older, install it from python.org.
- **uv**, a Python package manager:
  - Mac / Linux:
    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```
  - Windows (PowerShell):
    ```powershell
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    ```
  - Close and reopen the terminal afterwards.
- **git** (only needed to run the test suite): already on most Macs; on Windows, install Git for Windows from git-scm.com.

## 3. Open a terminal in the project folder

```bash
cd path/to/mycrobo
```

On a Mac you can type `cd ` and drag the folder into the Terminal window.

## 4. Install the project

```bash
uv sync
```

This needs internet the first time and may take a minute.

## 5. Run it

| Step | Command | What it does |
|---|---|---|
| 1 | `uv run mycrobo scan examples/slop-zoo` | Scans the demo app full of planted problems |
| 2 | `uv run mycrobo scan examples/slop-zoo --ai-import examples/slop-zoo-review` | Adds the pre-recorded AI review (no API key needed) |
| 3 | `uv run python scripts/benchmark.py --ai-import examples/slop-zoo-review` | Shows accuracy against the answer key |
| 4 | `uv run mycrobo rules` | Lists everything it checks for |
| 5 | `uv run pytest` | Runs the test suite |

To scan any other project: `uv run mycrobo scan path/to/project`

## 6. What you should see

The folder path in the first line will be yours; everything else should match.

**Step 1: scan**

```
MyCrobo 0.1.0  scanned 20 files in …/examples/slop-zoo

Security (5)
  HIGH     .env:1  Hard-coded secret assigned to `DB_PASSWORD`  [security/hardcoded-secret] 27507ee089bc
           DB_PASSWORD=********
  ...
20 findings (6 high, 10 medium, 4 low)
Slop Score: 99/100 (Critical)  over 136 lines of code
```

Check: **20 findings**, **Slop Score 99**, secrets shown as `********`.

**Step 2: with the AI review**

```
  CRITICAL AI api/src/routes/orders.ts:13  SQL injection: request parameter interpolated into SQL
  CRITICAL AI api/src/routes/users.ts:14  Any caller can delete any user
  CRITICAL AI infra/main.tf:9  SSH (port 22) open to the entire internet
  CRITICAL AI worker/jobs/report.py:8  Shell command injection through the report filename
  ...
26 findings (7 critical, 12 high, 4 medium, 3 low)
Slop Score: 100/100 (Critical)  over 136 lines of code
AI review (external): 9 files reviewed, 19 confirmed, 0 dismissed as false positives, 6 new issues (0 unverifiable claims discarded), cost $0.000
```

Check: **26 findings**, including **4 critical findings marked `AI`**, and **cost $0.000**.

**Step 3: benchmark**

```
  ✓ Z01 .env:1    database password in a committed .env    security/hardcoded-secret
  ... (25 lines, all ✓)
  ? not in answer key: worker/jobs/report.py:9  ai/coding-gaps  All reports share one fixed /tmp path

Precision: 96% (25/26 findings in the answer key; ...)
Recall:    rules-detectable 20/20 · AI-only 5/5 · overall 25/25 (100%)
```

Check: **25 ✓, no ✗**, **Recall 25/25**.

**Step 4: rules.** 9 lines, from `security/hardcoded-secret` to `loose-ends/mock-data`.

**Step 5: tests.** Ends with **`51 passed`**.

### Not errors

- **Steps 1 and 2 end with exit code 1 on purpose.** That is the CI gate reporting high-severity issues, and the demo app is full of them. A real failure would show a Python traceback.
- **On Windows, the first line shows the path with `\`.** Finding locations always use `/`.
- **The first `uv sync` downloads packages**, so it needs internet.

## 7. Optional: live AI review

This only works with an Anthropic API key. It costs a few cents, capped at $0.25 here.

- Mac / Linux:
  ```bash
  export ANTHROPIC_API_KEY=your-key-here
  ```
- Windows (PowerShell):
  ```powershell
  $env:ANTHROPIC_API_KEY="your-key-here"
  ```

Then:

```bash
uv run mycrobo scan examples/slop-zoo --ai --max-cost 0.25
```

## If something goes wrong

- **`uv: command not found`:** close and reopen the terminal after installing uv.
- **`No module named 'mycrobo'` on a Mac:** the folder is probably inside an iCloud-synced `Documents` or `Desktop`. Add `--no-editable` after `uv run` (for example `uv run --no-editable mycrobo scan examples/slop-zoo`), or move the folder to your home folder.
- **More:** a 2-minute guided tour is in [docs/DEMO.md](docs/DEMO.md), and how it works is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
