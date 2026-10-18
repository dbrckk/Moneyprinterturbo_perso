# Visual diversity: perceptual hints, not verified semantic similarity

Wikimedia Commons images are selected only after the existing CC0/public-domain
metadata check, scene keyword relevance and FFprobe image validation. The
renderer previously considered two photos distinct whenever their Commons URLs
differed, even if they depicted the same image.

## What changed

- For downloaded thumbnails, a 64-bit horizontal *difference hash* (dHash)
  samples an 9x8 grayscale FFmpeg decode. No internet calls, paid models or
  Python image dependencies are introduced.
- Candidates are preferred when both their Commons URL and image structure
  differ from earlier scenes. Only very similar hashes (at most five different
  bits) are flagged. A missing or unreliable hash is **not** treated as a
  duplicate, and a valid licensed image is never discarded if no better choice
  exists.
- Nearly flat images are not hashed: their signatures are unstable.
- The global max of eight Commons searches, three workers and four additional
  image downloads for repeated topics stays unchanged.
- `commons_fetch` gains three metrics: `scenes_with_perceptual_signature`,
  `distinct_visual_patterns_estimated`, `near_duplicate_candidates_avoided`.
  `fingerprint_seconds_sum` shows the extra processing time.
- Fingerprints stay in memory. They are not exported in credit files or JSON.

## Limitations and quality safeguards

dHash compares broad image structure, **not subject semantics**. A different
crop or a substantial perspective change may look dissimilar; separate natural
scenes with similar horizons may collide. The engine still prefers provenance,
legal license, meaningful scene matches and visual-media suitability. No
image is presented as being verified by computer vision.

Unit tests exercise actual FFmpeg JPEG quality changes and controlled candidate
rotation. For a complete live test, use Commons imagery in the Android studio
and inspect the storyboard: a real human review remains essential before
publishing generated videos.


## French/English visual keywords and scene review

The Commons candidate scorer can now match a deliberately small set of
French/English **visual nouns** (for example forêt/forest, montagne/mountain,
pont/bridge and château/castle) without a model download, network translation,
paid key or fuzzy matching of names and claims. This improves ranking **only
among images already returned by Wikimedia's search API**; it cannot guarantee
that Commons finds all translations or that the pixels depict the topic.
Title matches still count more than description matches, and the existing
landscape/person disambiguation and CC0/public-domain filter are retained.

For new primary-search Commons assets, the scene manifest now includes
`query_term_count` and `query_term_coverage` (0–1) based on textual filename
and Commons-description evidence. Two or more requested visual terms with
fewer than half matched produce a **non-blocking** publication-review warning
in `video.quality.json`. Locally supplied files, images returned by broadened secondary searches and
older manifests without comparable measurements are not assigned fabricated scores.

The video QA pass also samples the **rendered MP4**, not Wikimedia thumbnails:
at the midpoint of each scene, FFmpeg extracts 16×16 grayscale pixels.
`visual_review` in the quality JSON reports median luma, proportion of very
dark pixels, near-uniform pixel proportion, and an explicit unavailable count.
Mostly dark and near-uniform midpoint frames are flagged for human review
of `video.storyboard.png`, not treated as encoding failures. This is a
bounded heuristic; bright captions, legitimate dark scenes, animation,
relevance and visual aesthetics cannot be certified by these measurements.

No user images or model prompts are uploaded for analysis; the additional
image checks run locally on the rendered video with the existing FFmpeg tool.

## Multi-candidate recovery after decoding or perceptual deduplication

When the optional CLIP comparison or repeat-scene selector requests at least
two Commons thumbnails, the first API search can return distinct file titles
that do not produce distinct *usable photographs*. The selector now completes
the primary ranked download pass before deciding whether another search is
necessary. Only supported, unambiguous scenic topics may use a second
lexically conservative search, **once per query at most**.

The same original-query relevance, allowed CC0/public-domain licences,
trusted Wikimedia hosts and FFprobe decoding apply to these late candidates.
Photographs rejected as near-duplicates are removed from the temporary
download folder. The candidate selector records
`near_duplicate_downloads_skipped` and `fallback_after_verification` in
its per-search statistics. A default request for only one image does **not**
trigger a second API search because a download failed. Tests check corrupted
images, visually identical uploads, and secondary results with unsuitable
licences or off-topic titles.

This can improve the supply of distinct licensed images, but it does not
prove that the selected image depicts the narrated subject. Humans should
review the exported contact sheet before publication.
