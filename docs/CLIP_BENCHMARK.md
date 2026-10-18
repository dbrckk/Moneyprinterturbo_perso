# CLIP quality benchmark protocol

A working CLIP inference is **not evidence** that it improves scene relevance.
Use `semantic/benchmark.py` to compare the original Commons order with the
optional CLIP order against **independent, pre-recorded human relevance labels**.

## Data preparation

For each distinct, supported visual scene query, collect at least two **already
license-verified** images. Supported names include forest, mountain, river,
beach, waterfall, castle and related concrete nouns (abstract subjects and
named people are deliberately rejected). Label every image **before seeing
CLIP scores**:
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

The first image is the current baseline winner. Keep query names unique,
including case differences, and include landscapes, objects, close calls and
difficult negative images in the **candidate sets**. The explicit query itself
must remain a supported visual concept. Per-run limits are 16 query cases,
18 candidates per case, and **32 image files across all cases**. This upper
bound keeps the local quantized CPU worker resource use controlled while still
supporting 18 scenes plus four alternative thumbnails from the standard
storyboard. Do not put unlicensed third-party photos into a public repository.
The evaluator treats submitted files as **unverified local files**, never
fabricates Commons URLs or asserts CC0 rights on the user's behalf.

Run `npm install --prefix semantic --no-audit --no-fund`, then
`python semantic/benchmark.py /path/to/labeled.json --output /tmp/clip-evaluation.json`.

The report gives per-case before/after human relevance, improvements,
regressions, mean relevance, inference status and elapsed seconds. The
benchmark does **not** infer human labels using CLIP itself.

A successful, complete local model evaluation has
`comparison_valid: true` and exits with code 0. If model files are missing,
the optional worker times out, a case has no visual query mapping, or any
result is incomplete, the diagnostic JSON is still written but
`comparison_valid: false`, comparative relevance, improvements and
regressions are `null`, and the command exits with code **2**. The baseline
mean remains visible. Never interpret a fallback preserving the original
order as an AI quality tie. The output directory is created automatically.

## Decision gate

Keep CLIP opt-in unless a representative, independently annotated set
demonstrates higher relevance with no serious regressions and acceptable
cold/warm runtime. A tiny synthetic PNG smoke test checks only runtime
health, **not visual quality**. Failure to load the model is a fallback,
not a successful quality benchmark.
