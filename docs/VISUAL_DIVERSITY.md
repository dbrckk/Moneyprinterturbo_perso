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
