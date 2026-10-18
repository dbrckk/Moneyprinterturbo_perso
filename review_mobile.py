"""Standalone, offline, blinded Commons-vs-CLIP review for mobile devices.

Embeds small FFmpeg-generated JPEG previews of *already licensed* benchmark
assets, never remote media URLs or unreviewed external HTML. The exported CSV
matches visual_benchmark.score_human_reviews() exactly.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import subprocess


MAX_PREVIEW_BYTES = 500_000
MAX_DOCUMENT_BYTES = 8_000_000


def _preview(path: Path) -> str:
    """Bounded JPEG preview; don't embed multi-megabyte source image files."""
    if not path.is_file() or path.stat().st_size > 18 * 1024 * 1024:
        raise ValueError("Invalid or oversized review image")
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
         "-threads", "1", "-i", str(path),
         "-vf", "scale=560:420:force_original_aspect_ratio=decrease",
         "-frames:v", "1", "-q:v", "5", "-f", "image2pipe",
         "-vcodec", "mjpeg", "pipe:1"],
        capture_output=True, timeout=30, check=False,
    )
    if result.returncode or not 0 < len(result.stdout) <= MAX_PREVIEW_BYTES:
        raise RuntimeError("Could not generate bounded JPEG review thumbnail")
    if not result.stdout.startswith(b"\xff\xd8\xff"):
        raise ValueError("Review thumbnail is not JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(result.stdout).decode("ascii")


def build_mobile_review(summary: dict, images: Path, destination: Path) -> int:
    """Return number of embedded review pairs, or raise on invalid input.

    The left/right assignments are deterministic but opaque to reviewers.
    No algorithm labels or CLIP confidence appear in the visual review.
    """
    cases = []
    for case in summary["cases"]:
        if case.get("status") != "evaluated":
            continue
        query = case["query"]
        left_original = case["baseline"]
        right_original = case["clip"]
        if not isinstance(query, str) or len(query) > 110:
            raise ValueError("Invalid benchmark review query")
        thumbs = {}
        for name, item in (("baseline", left_original), ("clip", right_original)):
            image = item["image"]
            if (not isinstance(image, str) or not image.startswith("images/")
                    or "/" in image[7:] or "\\" in image or ".." in image
                    or not Path(image).name.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))):
                raise ValueError("Unsafe benchmark image path")
            thumbs[name] = _preview(images / image[7:])
        seed = (query + "\0" + left_original["source"] + "\0" +
                right_original["source"]).encode("utf-8")
        reverse = bool(hashlib.sha256(seed).digest()[0] & 1)
        sides = ("clip", "baseline") if reverse else ("baseline", "clip")
        cases.append({
            "query": query,
            "left": thumbs[sides[0]],
            "right": thumbs[sides[1]],
            "left_algorithm": sides[0],
            "baseline_url": left_original["source"],
            "clip_url": right_original["source"],
            "changed": bool(case["changed"]),
        })
    if not cases:
        return 0
    data = json.dumps(cases, ensure_ascii=True, separators=(",", ":"))
    # Prevent premature script termination even with hostile Commons metadata.
    data = (data.replace("<", r"\u003c").replace(">", r"\u003e")
            .replace("&", r"\u0026"))
    js = Path(__file__).with_name("review_mobile.js").read_text(encoding="utf-8")
    if js.count("__REVIEW_DATA__") != 1:
        raise RuntimeError("Invalid offline review script")
    js = js.replace("__REVIEW_DATA__", data)
    page = _HTML.replace("/* EMBEDDED_REVIEW_SCRIPT */", js)
    if len(page.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ValueError("Mobile review exceeded size limit")
    destination.write_text(page, encoding="utf-8")
    return len(cases)


_HTML = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; form-action 'none'; object-src 'none'; base-uri 'none'">
<title>MoneyPrinter · Revue visuelle indépendante</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}
body{background:#0d1422;color:#f2f5fb;font:16px/1.5 system-ui,sans-serif;margin:0 auto;max-width:1000px;padding:18px}
h1{font-size:1.55rem;line-height:1.2}h2{font-size:1.12rem;margin-bottom:12px}
p,.muted{color:#c8d5e6}section{border:1px solid #34465c;border-radius:14px;padding:16px;margin:18px 0}
.pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
figure{margin:0;background:#18263a;border-radius:10px;overflow:hidden}
img{width:100%;height:260px;object-fit:contain;display:block}figcaption{padding:10px;font-weight:700}
fieldset{border:0;padding:8px 0;margin:0;display:flex;flex-wrap:wrap;gap:8px}
label.choice{border:1px solid #627790;border-radius:8px;padding:10px;min-height:44px;display:flex;gap:6px;align-items:center}
label.choice:has(input:checked){border-color:#95c7ff;background:#223955}
.notes{display:block;width:100%;padding:12px;background:#142235;color:#fff;border:1px solid #6e8199;border-radius:8px;font:inherit}
button{min-height:48px;border:0;border-radius:10px;padding:12px 18px;background:#a7d1ff;color:#0c2036;font-weight:bold;font:inherit;cursor:pointer}
.actions{position:sticky;bottom:0;background:#0d1422;padding:12px 0;border-top:1px solid #34465c}
@media(max-width:520px){img{height:175px}body{padding:12px}.pair{gap:8px}section{padding:12px}}
</style></head><body>
<header><h1>Évaluation visuelle à l’aveugle</h1>
<p>Choisissez uniquement l’image qui illustre le mieux le sujet. La méthode de sélection est masquée et l’ordre gauche/droite est mélangé de façon reproductible. Les images proviennent de Wikimedia Commons après vérification de leur licence.</p>
<p><strong>Fonctionne hors connexion.</strong> Aucune photo, note ou donnée personnelle n’est envoyée à un serveur. Le CSV est téléchargé sur votre appareil.</p></header>
<main id="review-cases"></main>
<div class="actions"><p id="progress" role="status" aria-live="polite"></p>
<button id="export" type="button">Exporter mes évaluations · CSV</button></div>
<script>
/* EMBEDDED_REVIEW_SCRIPT */
</script></body></html>
"""
