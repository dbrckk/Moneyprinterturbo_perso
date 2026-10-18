# Optional local CLIP visual comparison (experimental)

This is an **optional** extra ranking signal for some already-downloaded,
already-licensed Wikimedia Commons thumbnails. It is not an image-generation
model and it is not a proof of semantic correctness. The default Free Studio
renderer still runs without Node packages, neural models or a paid service.

## Enable

- **Android Remote Studio:** choose *Analyse visuelle IA → CLIP* and
  *Images libres (Wikimedia Commons)*. This changes the GitHub Actions input
  `visual_ai` to `clip`. The ordinary **Rapide** mode leaves it at `off`.
- **GitHub Actions:** `Générer une vidéo gratuite` → `visual_ai: clip`,
  `visuals: commons`.
- **Local Linux:** `npm install --prefix semantic --no-audit --no-fund`
  followed by
  `python3 free_studio.py --topic "Montagnes" --script "La montagne est enneigée." --commons --visual-ai clip --output outputs/mountains.mp4`.

It runs a quantized CLIP ViT-B/32 model from
[`Xenova/clip-vit-base-patch32`](https://huggingface.co/Xenova/clip-vit-base-patch32)
via [Transformers.js](https://huggingface.co/docs/transformers.js/).
The model revision is pinned to
`6ef1ebc8b0766a7a8d11b146462c99cdf74dd22d`, loaded on CPU.
The `@huggingface/transformers` package version is pinned in
`semantic/package.json`. GitHub Actions caches downloaded model weights.
The first render needs to download them and will take longer.
The app does not ship a full neural model to Android: inference happens
on the GitHub Actions runner.

## Privacy and trust boundaries

- Wikimedia Commons assets are filtered by the existing CC0/public-domain
  metadata policy, trusted-host checks, FFprobe validation and upload-size cap
  **before** CLIP is allowed to inspect their local files.
- There is no third-party **image inference API**: image bytes are decoded
  by Node.js locally. The initial model download and npm installation require
  external network connectivity. Neither the GitHub token nor user text is
  supplied to a hosted inference API.
- The existing limit of **four additional Commons image variants** is retained.
  The extra images are fetched only for supported visual keywords; repeated
  scene diversity keeps priority. The option never alters default fetch counts.
- Only a small explicit list of visual terms (forest, mountain, river, snow,
  etc.) produces an English model prompt. Unsupported, name-like or ambiguous
  scene searches keep the original textual ranking.
- CLIP compares one scene label with the same fixed negative labels for each
  candidate. Its score is a **relative label softmax**, not a calibrated
  probability that an image is correct. The winner changes only if its target
  score is at least 0.45, improves over the original by at least 0.12, and
  its Wikimedia textual evidence is comparable. Secondary Commons results
  without comparable original-query coverage cannot displace a better-
  documented primary result.
- Model installation failures, unreachable Hugging Face, invalid or partial
  scores, unavailable Node.js, and model timeouts leave the original candidate
  order intact. The fallback is visible in `commons_fetch.semantic_status`
  and the video `warnings`; failures are never disguised as an AI ranking.
- Successful model comparisons add `semantic_score` to the selected scene's
  `media` metadata and `semantic_candidate_count`,
  `semantic_reordered_queries`, `semantic_seconds`,
  `semantic_model`, `semantic_status` to `commons_fetch`.

## Regression and real inference

`python -m unittest tests.test_semantic_rank -v` exercises all fail-safes
with a mocked subprocess. `node --test semantic/test_clip_rank.mjs` tests
the input contract with no model download. The
`CLIP Local CPU Inference Smoke` GitHub Actions workflow installs real
dependencies, downloads/loads the pinned quantized model and performs
real inference on two generated sample PNG files.

A human still needs to inspect the rendered scene contact sheet: CLIP can
confuse near-duplicate landscapes, styles, art, names, and obscure French
concepts. Turn this off if speed is more important than experimentation.

## Licensed-image discovery and coverage

Commons full-text results often contain files licensed under CC BY or other
terms that the free production mode deliberately excludes. The search now
checks up to **45** results per request (rather than 30), below MediaWiki's
50 scaled-image limit, and asks for only the extended metadata it actually
uses. If a scene needs multiple candidates and its first search does not
yield enough license-verified, lexically relevant images, one alternative
search is allowed **only** for a tightly recognized visual noun
(e.g. `forest landscape` → `forest`, `waterfall` → `cascade`).
People's names, multiple visual concepts and unknown modifiers do not
trigger this broadening. The original scene query still determines relevance;
a more permissive search never weakens the CC0/public-domain filter,
host allowlist, download-size limit, duplicate suppression or FFprobe check.

No alternative request is made when one image is requested or when enough
candidates were already eligible. Requests are bounded to at most two
Commons searches per eligible multi-image request, not unlimited retries.
Stats include API query count, examined pages, number of relevant
licensed candidates and number added by fallback.

The A/B workflow records **licensed pair coverage**, not correctness.
More selectable images may improve availability but does not establish
higher perceptual quality. The five-topic workflow can change with
Commons' live inventory and license metadata.

## A/B measurement on real Wikimedia images

The additional [`visual_benchmark.py`](../visual_benchmark.py) and
[`CLIP A/B Visual Benchmark`](../.github/workflows/clip-ab-benchmark.yml)
workflow run a bounded, on-demand comparison on **real** Commons media.
Five fixed scene topics are searched with the same CC0/public-domain policy
as production; two verified thumbnails per topic are needed to form a pair.
At most three searches run concurrently and the model is initialized once.
The workflow exports:

- **Direct `contact-sheet.png` for Android:** left column is the existing
  Commons textual selection and right column is optional CLIP selection
  (rows correspond to the query order shown in the workflow summary).
- **Direct `metrics.json`:** number of eligible pairs, whether CLIP changed
  the selected source, individual provenance and timings; no claim of
  objective accuracy.
- **Direct `review-mobile.html` for Android, without ZIP:** independent blind
  A/B comparison. Both candidates appear side by side with algorithm identities
  concealed and reproducibly shuffled. Tap left/right/tie/both unsuitable,
  optionally add notes, then reveal the preference counts **only after all
  scenes have been rated**, without Python on Android. The page reports CLIP,
  classical, tie, and both-unsuitable votes while explicitly avoiding any
  automatic quality claim or default-mode switch. Export the compatible CSV
  locally for independently reproducible scoring. Thumbnail previews are embedded in the single HTML file:
  opening it requires no service or extra assets, and browser network access
  is disabled by its content security policy.
- **`clip-ab-human-review` archive:** additional detailed HTML provenance
  report with original images, authors, licences and `human-review.csv` template.

The first actual workflow run on **17 October 2026** evaluated two of five
topics and CLIP changed **zero** selected source images. That is a
*coverage/decision observation*, **not evidence of a visual-quality gain**.
Other topics had fewer than two verified licensed candidate files, so they
are not scored. The default therefore remains **off**.

For a local comparison after `npm install --prefix semantic`, FFmpeg and
FFprobe are installed:

```bash
python visual_benchmark.py --output outputs/clip-ab-benchmark
```

On Android, open the directly downloadable `review-mobile.html` from a
benchmark workflow run, rate each image pair, reveal the summary only after
all comparisons have been rated, and export the CSV on the device. Copy that CSV next to the benchmark metrics before scoring. An
unchanged pair only accepts `tie` or `both_bad`, and exporting incomplete
ratings is allowed (unrated rows stay blank). Preview thumbnails are limited
in size; a preview failure is reported without inventing human votes.

Alternatively, open the detailed HTML and PNG review artifacts. In the CSV template, fill the
`human_choice_baseline_clip_tie_both_bad` column with one of `baseline`,
`clip`, `tie`, or `both_bad`; leave undecided cases blank. Then aggregate
only actual human votes (no download or model is needed):

```bash
python visual_benchmark.py --output outputs/clip-ab-benchmark \
  --score-reviews outputs/clip-ab-benchmark/human-review.csv
```

The output `human-score.json` counts decisive preferences on changed
source images. Choosing an algorithm as winner when both variants show the
*same source image* is explicitly rejected. Sample sizes remain small and
voluntary; the scoring tool **does not automatically change the default**.
