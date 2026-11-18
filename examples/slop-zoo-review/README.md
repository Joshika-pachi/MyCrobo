# Recorded AI review of the slop zoo

These files let you see MyCrobo's AI layer without an API key:

```bash
uv run mycrobo scan examples/slop-zoo --ai-import examples/slop-zoo-review
```

- `*.request.json`: what MyCrobo sends for each file (system prompt, file prompt with secrets redacted, JSON schema). Created with `--ai-export`.
- `*.response.json`: the answers, written by Claude (Opus 5.5) in a Claude Code session, no API key involved.

On import MyCrobo treats these exactly like a live model reply: schema validation, evidence verification, no dismissing security findings. The file names are hashes of the requests, so an answer is ignored if the zoo or MyCrobo's prompts change.

The reviewing session had also written the zoo, so this demonstrates the pipeline end to end, not independent review accuracy. For that, run `uv run mycrobo scan examples/slop-zoo --ai` with `ANTHROPIC_API_KEY` set.
