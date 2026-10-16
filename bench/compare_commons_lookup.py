#!/usr/bin/env python3
"""Offline Wikimedia lookup latency exercise; no network or external services.

Simulated latency only. This benchmark is never presented as actual network speed.
"""
import json
from pathlib import Path
import statistics
import tempfile
import time
from unittest.mock import patch

import media_assets


def simulate(query, directory, *, limit, timings):
    time.sleep(0.18)
    timings.update({"api_seconds": 0.12, "download_seconds": 0.06,
                    "verification_seconds": 0.01, "download_bytes": 12000,
                    "download_attempts": 1})
    asset = media_assets.MediaAsset(directory / "test.jpg", "image",
        "https://commons.wikimedia.org/wiki/File:example.jpg", "CC0", "tester", query)
    return [asset], []


def main():
    data = {}
    with tempfile.TemporaryDirectory() as tmp, patch.object(
            media_assets, "commons_assets", side_effect=simulate):
        for workers in (1, 3):
            results = []
            for trial in range(3):
                output = {}
                start = time.perf_counter()
                selected, warnings = media_assets.commons_scene_assets(
                    ["forest", "river", "ocean", "mountain", "sunrise", "lake"],
                    Path(tmp), max_workers=workers, stats=output)
                elapsed = time.perf_counter() - start
                if len(selected) != 6 or warnings:
                    raise AssertionError("Unexpected test scene failure")
                results.append(round(elapsed, 3))
            data[str(workers)] = {"trials_seconds": results,
                                  "median_seconds": round(statistics.median(results), 3)}
    data["description"] = "Six stubbed independent Commons queries, each sleeping 180ms; not a live HTTP benchmark"
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
