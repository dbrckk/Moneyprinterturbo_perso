# CLIP quality benchmark protocol

A working CLIP inference is **not evidence** that it improves scene relevance.
Use `semantic/benchmark.py` to compare the original Commons order with the
optional CLIP order against **independent, pre-recorded human relevance labels**.

## Data preparation

For each distinct supported scene query, collect at least two **already
license-verified** images. Label each image *before seeing CLIP scores*:
0 = wrong topic, 1 = partial or ambiguous, 2 = clearly depicts the scene.
Save a JSON file:

```json
{
  "cases": [
    {
      "query": "forest",
      "candidates": [
        {"path": "/absolute/path/forest-a.jpg", "relevance": 1},
        {"path": "/absolute/path/forest-b.jpg", "relevance": 2}
      ]
    }
  ]
}
```

The first image is the current baseline winner. Keep queries unique and
include portraits, landscapes, objects, close calls, and difficult negatives.
Do not put unlicensed third-party photos into a public repository.

Run `npm install --prefix semantic --no-audit --no-fund`, then
`python semantic/benchmark.py /path/to/labeled.json --output /tmp/clip-evaluation.json`.

The report gives per-case before/after human relevance, improvements,
regressions, mean relevance, inference status and elapsed seconds. The
benchmark does **not** infer human labels using CLIP itself.

## Decision gate

Keep CLIP opt-in unless a representative, independently annotated set
demonstrates higher relevance with no serious regressions and acceptable
cold/warm runtime. A tiny synthetic PNG smoke test checks only runtime
health, **not visual quality**. Failure to load the model is a fallback,
not a successful quality benchmark.
