# The slop zoo

A small polyglot "shop" (TypeScript API, Python worker, Terraform, a `.env` file) with **25 planted issues** and a few **traps**. It is MyCrobo's benchmark: every run is scored against [`expected.json`](expected.json).

> Don't fix anything here: the problems are the point.

## Run it

```bash
uv run python scripts/benchmark.py                                         # rules only, offline, free
uv run python scripts/benchmark.py --ai-import examples/slop-zoo-review    # + the recorded AI review
uv run python scripts/benchmark.py --ai                                    # + a live Claude review (needs a key)
uv run mycrobo scan examples/slop-zoo                                      # the normal report
```

| Mode | Precision | Recall |
|---|---|---|
| Rules only | 100% (20/20) | 20/20 rule-detectable · 0/5 AI-only · 80% overall |
| Rules + recorded AI review | ≥ 96% (25/26)* | 20/20 · 5/5 · **100% overall** |

\* The one finding not in the key is real: every report is written to the same fixed `/tmp` path. It is reported as "not in answer key" rather than added to the key afterwards.

## What's planted

| Kind | Examples | Found by |
|---|---|---|
| Secrets | DB password in a URL, JWT secret, API token, committed `.env` | rules |
| AI slop | `// ... rest of the user handlers`, "For now, just return…", "In a real app you would…", a copy-pasted function whose tax rate already drifted | rules |
| Loose ends | `throw new Error("Not implemented")`, `-> list[Item]` with body `pass`, `.catch(() => {})`, `except Exception: pass`, `breakpoint()`, `console.log(req.body)`, mock orders, lorem ipsum, TODO/FIXME | rules |
| Security the rules can't see | SQL injection, missing auth on `DELETE /users/:id`, `shell=True` command injection, SSH open to `0.0.0.0/0` | AI review |
| Coding gaps | `items` unbound after a swallowed exception | AI review |

**Traps (must not be flagged):** mock data and `console.log` inside `api/tests/`, a login form's password placeholder, an abstract method raising `NotImplementedError`, a no-op `on_success` hook, an explained `except KeyError: pass`, a `PASSWORD_FIELD` form-field name. None are flagged.

No real-format provider keys (AWS, Stripe, GitHub…) are used, so GitHub push protection won't block pushing this repo.

## Honesty notes

- The same team wrote the zoo and the rules, so 100% rules recall here shows the rules work as designed. It is not an independent accuracy claim. The traps and the real-world check against pytest's source (see the main README) cover precision on code we didn't write.
- [`../slop-zoo-review/`](../slop-zoo-review/) is a **recorded** AI review. It was produced with `--ai-export`, answered by Claude (Opus 5.5) in a Claude Code session, and is applied with `--ai-import`. The reviewing session had also written the zoo, so treat it as a pipeline demonstration. Run `--ai` with a key for an independent review.
- The recorded answers are tied to the exact prompts (the code plus MyCrobo's prompt version). If the zoo or the prompts change, the import ignores them; regenerate with `--ai-export`.
