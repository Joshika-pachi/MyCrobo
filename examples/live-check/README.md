# Live check sample

`upload_handler.py` is test input for MyCrobo's AI review. Its problems are planted on purpose; don't fix them.
The list lives here, not in the file, so a reviewing model can't read the answers.

| Problem | Line | Found by |
|---|---|---|
| Path traversal: `filename` from the request joined into a filesystem path (save and delete) | 13, 34 | AI only |
| No upload validation: size and content type never checked | 11 | AI only (rules see the TODO) |
| `except Exception: pass` hides read/parse failures | 23 | rules |
| `scan_for_viruses() -> bool` has body `pass` (returns None, i.e. falsy) | 27 | rules |
| `breakpoint()` left in a request handler | 33 | rules |
| `# TODO: limit upload size` | 12 | rules |
