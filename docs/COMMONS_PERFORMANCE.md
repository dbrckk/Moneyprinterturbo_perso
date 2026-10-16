# Wikimedia Commons: bounded parallel lookups and timing

## Changes

- No paid service and no extra Python dependencies. Each distinct scene query is fetched once, even if its text repeats in multiple scenes.
- At most **three** concurrent Wikimedia API/image tasks, with at most **eight** unique scene searches per storyboard. Scenes beyond the budget use generated animated backgrounds, not a misleading unrelated photo.
- Workers each write to a separate temporary scene folder. Results and warnings are reassembled in **original scene order**, regardless of completion order.
- License filtering is unchanged: only verified public-domain or CC0 media; filenames/descriptions must share meaningful keywords with the scene. URL host restrictions, download limits, verification by FFprobe and provenance credits remain active.
- Individual Wikimedia lookup/download failures do not block other scenes. The renderer falls back to its animation and records the warning.

## Media telemetry

Each new `video.json` includes `commons_fetch` for Commons-enabled runs:

- `scenes_requested`, `scenes_matched`, `unique_queries`, `workers_used`.
- `wall_seconds` measures elapsed wall-clock time for the parallel Commons stage.
- `api_seconds_sum`, `download_seconds_sum`, and `verification_seconds_sum` are summed timings over individual queries. **Their sum may exceed wall-clock time because searches overlap.**
- `download_bytes` includes actual downloaded bytes even if local decode validation rejects an image; `download_attempts` counts image requests.

The same stage contributes `commons_wall_seconds`, `commons_api_seconds_sum`, `commons_download_seconds_sum` to `render_performance`. The existing `media_selection_seconds` includes all media processing and remains comparable with past versions.

## How to measure

A deterministic offline exercise is provided:

```bash
python bench/compare_commons_lookup.py
```

It uses six mocked Commons queries (180 ms latency each), and compares sequential with three-worker scheduling over three trials. These figures measure concurrency behavior only, **not** live Wikimedia throughput.

For actual throughput, select Commons in the Android studio, generate a narration with several distinct scenes, and inspect `video.json`. Compare different runs and the percentage of scenes matched; network conditions, Wikimedia limits, license availability, search relevance, encoding complexity, and GitHub runner load all affect outcomes. Zero matched scenes is explicitly a fallback, never claimed as a successful Commons retrieval.

## Avoid false landscape matches

The live network smoke test of 16 October 2026 exposed several cases where single-word searches for `montagne` or `rivière` selected public-domain portraits of people with those surnames. Legal status alone is not visual relevance. The renderer now applies `safe_scenic_score` for **ambiguous single-word nature requests**: it rejects explicit biographies, portraits and paintings and requires independent landscape context in the Commons title or image description. A two-word query for a *person* (e.g. Henri Rivière) remains eligible. This is a conservative lexical heuristic, not computer vision: it can reject valid nature images, so the result count may decrease. The stable fallback is an original animated background rather than an unverified claim about scene relevance.

## Contextual search expansion — verified on live Commons

A live controlled comparison on 16 October 2026 (run 38916345614) used four ambiguous French one-word nature queries and four matching contextual variants. Under the strict portrait/biography filter, the one-word group found **0/4** images; contextual expressions (`montagne panorama`, `forêt paysage`, `rivière paysage`, `océan paysage`) found **3/4**, each recorded with public-domain provenance. The forest and river results were **artworks**, not documentary photographs; the search is visual assistance, not an assertion that the depicted scene is a photo.

For single-term natural-scene queries, the Commons **API search** now adds a modest contextual qualifier automatically. The original user keywords still govern relevance filtering, attribution and exported search-query metadata. An explicit two-word name such as `Henri Rivière` is never rewritten. The `commons_fetch.contextual_expansions` count records how many queries were expanded. This evidence is a small live sample, not a general retrieval guarantee; graphics remain the fallback.
