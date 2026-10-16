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

## Production MP4 réelle confirmée (16 octobre 2026)

Le workflow [Wikimedia Commons Real Video Smoke](https://github.com/dbrckk/Moneyprinterturbo_perso/actions/runs/38916712377) a généré sur un runner GitHub Actions une vraie vidéo H.264/AAC avec **deux scènes sur deux** alimentées par des images Wikimedia vérifiées, en partant des requêtes simples `montagne` et `rivière`. Les deux recherches ont été contextualisées automatiquement ; temps réseau mesuré **1,3228 s** pour l'ensemble de l'étape. Le contrôle `video_qa.py` a réussi (durée, codec, synchronisation, sous-titres et storyboard). L'archive `verified-commons-video-sample` contient le MP4, ses crédits, les fichiers de sous-titres, la planche de contrôle et le JSON de provenance, conservés 3 jours.

**Limite :** deux scènes à 360p et 12 images/s ne représentent ni une charge de production universelle ni une garantie que des médias seront trouvés pour n'importe quel sujet. Les œuvres d'art du domaine public peuvent également être retenues si leur contenu correspond au sujet. Les crédits et la licence sont fournis pour chaque image.


## Photo-first ranking without false photo claims

Candidates still require independently supported query relevance, a verified CC0/public-domain license and valid dimensions. When equally relevant choices are available, descriptive metadata identifying a **photograph** is preferred over an unclassified image and over metadata indicating a painting, engraving or drawing. If only a licensed artwork is suitable, it remains usable but a warning is attached.

Each selected asset now includes `visual_medium`: `photograph-likely`, `artwork-likely` or `unspecified`. These are **lexical metadata hints**, not a computer-vision assertion of the true visual medium. The `commons_fetch` summary counts how many scenes fall into each category; the credits retain the same information. A title saying "panorama" alone is insufficient to label something a photograph. This optimization reduces misleading stock-like choices where descriptions are informative, but it cannot guarantee documentary photography without inspecting each image.


### Stronger Wikimedian category signals

The Commons API now requests **non-hidden file categories** together with licensed image metadata (one query, not an extra HTTP round trip). Tags such as `Category:Paintings of rivers` and `Category:Photographs of rivers` are useful evidence to distinguish artwork from documentary photography when filenames are opaque. The same conservative ranker applies; both media and license are still independently checked. Categories are incomplete and may mix terms, so `visual_medium` remains a *hint* rather than a verified claim.


## Visual variety for repeated scene subjects

When several storyboard sentences use the same search query, the engine now
requests up to **three ranked licensed images** from **one Commons API search**
instead of showing exactly the same picture throughout the video.
An additional global allowance of **four** image candidates across repeated
queries prevents unlimited extra downloads: at most one candidate per unique
search plus four extra images. Queries with only one qualifying licensed image
will still reuse it; no unrelated content is substituted.

The assignment is deterministic in narration order, independent of thread
completion. It also avoids reusing the same Commons source across distinct
queries when an appropriate alternative is available. New `commons_fetch`
fields: `image_variants_budget`, `image_variants_fetched`,
`unique_images_selected`, `scenes_reusing_image`. These are observed
counts, not promises that the photo candidates will be visually distinct:
Commons can contain near-duplicate photographs under different filenames.
All existing per-image license/metadata validation and the graphic fallback
remain in effect.


## Evidence-based second search for unusually repetitive scenery

The live comparison [GitHub run 38934234087](https://github.com/dbrckk/Moneyprinterturbo_perso/actions/runs/38934234087)
retrieved only **one** verified public-domain/CC0 image for `montagne panorama`,
but **three** for `montagne paysage` and **three additional** results for
`mountain landscape` (a source comparison, not a guarantee of photos).

If a nature topic appears in **at least three scenes**, has fewer licensed
candidates than needed, and the video has remaining API search budget, the
engine can issue **one additional, deliberately contextualized search** to
fill the available scene variants. At most two secondary searches are planned
for the entire video; the **same global `max_searches` limit** counts original
and secondary Wikimedia API requests. The existing limit of four extra
image downloads across repeated topics still applies to the total of successful
variants. Mixed results continue to respect the same license, host, file
dimension and content scoring checks.

The report records `secondary_queries_planned`,
`secondary_queries_attempted`, and `total_commons_api_queries` so operators
can distinguish network overhead from useful variety. Any failure leaves
the original valid scene photo or the animated fallback in place.


## Crop-aware mobile-first candidate selection

For rendered 9:16, 1:1 or 16:9 videos, the Commons selector now reuses
the **source width/height already present in Wikimedia `imageinfo`**.
When license, likely media type and lexical relevance are equivalent, an
image whose aspect is closer to the output is selected first. This
reduces excessive center cropping of wide photos for vertical mobile video.
The estimated kept-area fraction is
`min(source_aspect/target_aspect, target_aspect/source_aspect)`.

The estimate is about rectangular image area only; it does not detect
where people, faces, landmarks or other salient subjects appear, and
does not guarantee a better shot. Missing/invalid source dimensions are
left unclassified and still allowed. Per-scene `crop_retention_estimate`,
`commons_fetch.mean_crop_retention_estimate` and
`commons_fetch.scenes_with_crop_estimate` make the choice auditable.
No additional API call, image package or paid vision model is required.

## Visual QA warnings

The separate `video_qa.py` quality report now surfaces **reviewable visual risks** instead of hiding them in project metadata: likely illustrated/artwork media, source imagery projected to lose more than two thirds of its area when center-cropped, and repeated Commons image sources across scenes. These are warnings, not automatic rejection or proof of substandard aesthetics; the GIF/MP4 contact sheet remains the ultimate review surface. The provenance and aspect values are textual/geometry heuristics, never certified computer vision.


## Extreme aspect ratio: preserve the full picture

The real 9:16 Commons smoke test surfaced a valid but **extremely wide image** whose original center crop would preserve only **12.27%** of its source area. For Commons images with `crop_retention_estimate < 0.34`, the renderer now uses **a composed soft matte**: the entire source image is centered crisply over a softly defocused and color-matched extension of that same image. Motion is restrained to a 4% frame zoom. This occurs in both one-pass and legacy transition engines, requires only FFmpeg, and does not trigger additional downloads, CPU vision models or paid APIs. `media[].render_framing` records `soft-matte-full-image` or `cover-center-crop`.

The blur is applied to a 1/16-resolution intermediate for CPU efficiency. It is a design fallback for severely incompatible aspect ratios, not AI inpainting: background edges are derived from source pixels and are intentionally blurred. The video quality audit no longer issues a severe-crop warning for a successfully preserved image. Pixel-based FFmpeg regressions check that both horizontal extremes are retained after encoding in the one-pass and legacy renderers.
