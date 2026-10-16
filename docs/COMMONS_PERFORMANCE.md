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
