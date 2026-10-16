#!/usr/bin/env python3
"""Live, observable Wikimedia Commons probe with no paid APIs.

A failed network request is recorded as a fallback, never counted as a real
image download. This job exercises the production security/metadata filters.
"""
from __future__ import annotations
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from media_assets import commons_scene_assets


def main() -> int:
    work = ROOT / "outputs" / "commons-network-probe"
    work.mkdir(parents=True, exist_ok=True)
    queries = ["montagne", "forêt", "rivière", "océan",
               "montagne panorama", "forêt paysage", "rivière paysage", "océan paysage"]
    stats = {}
    started = time.perf_counter()
    selected, warnings = commons_scene_assets(queries, work,
                                                 max_searches=8, max_workers=3,
                                                 stats=stats)
    images = [
        {"scene": index + 1, "query": queries[index], "title": asset.title,
         "source": asset.source_url, "license": asset.license,
         "author": asset.author, "bytes": asset.file.stat().st_size}
        for index, asset in sorted(selected.items())
    ]
    report = {
        "mode": "live-network",
        "network_accessible_and_licensed_image_found": bool(images),
        "status": "licensed-media-fetched" if images else "graphic-fallback-required",
        "duration_seconds": round(time.perf_counter() - started, 3),
        "query_count": len(queries),
        "single_word_match_count": sum(i < 4 for i in selected),
        "contextual_match_count": sum(i >= 4 for i in selected),
        "licensed_images": images,
        "network_stats": stats,
        "warnings": warnings,
        "disclaimer": "A successful workflow without images validates fallback only, not network image availability."
    }
    path = work / "probe.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    # The fallback is an intentional supported behavior, not a CI failure.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
