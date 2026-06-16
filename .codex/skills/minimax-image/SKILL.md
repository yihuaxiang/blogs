---
name: minimax-image
description: Generate images with the MiniMax image-01 model via the MiniMax API. Use when a new bitmap illustration/cover is needed for a blog post.
---

# MiniMax Image

Zero-dependency Python script (stdlib only). Requires `MINIMAX_API_KEY` in the environment.

```bash
python3 .codex/skills/minimax-image/scripts/minimax_image.py \
  --aspect-ratio "16:9" \
  --out source/images/<slug>/cover.jpeg \
  --prompt "Flat modern isometric vector illustration, clean style, no text, no letters"
```

Key flags:

- `--aspect-ratio` one of `1:1 16:9 9:16 4:3 3:4 3:2 2:3` (default `1:1`).
- `--out <path>` output jpeg (parent dirs auto-created).
- `--force` overwrite existing file.
- `--dry-run` print payload without calling the API.

Prompt tips: English, flat/isometric vector style, always include `no text, no letters`
(AI-rendered text comes out garbled). One image per call is most predictable.
Verify the output file exists and is a valid JPEG; on failure, tweak the prompt and retry.
