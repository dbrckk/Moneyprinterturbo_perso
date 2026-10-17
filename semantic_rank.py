"""Optional, bounded CLIP reranking for already-verified Commons thumbnails.

This module never fetches media, modifies licensing checks or requires an ML
dependency in the default renderer. The optional Transformers.js worker runs
locally and returns *relative label scores*, not factual correctness.
"""
from __future__ import annotations

from dataclasses import replace
import json
import math
from pathlib import Path
import re
import subprocess
import time
import unicodedata


# Explicitly known visual nouns. Unknown names/abstract narration must retain
# the existing text ranking, rather than passing bad non-English CLIP prompts.
VISUAL_PROMPTS = {
    "montagne": "mountain", "montagnes": "mountain", "mountain": "mountain", "mountains": "mountain",
    "foret": "forest", "forets": "forest", "forest": "forest", "forests": "forest",
    "riviere": "river", "rivieres": "river", "river": "river", "rivers": "river",
    "lac": "lake", "lacs": "lake", "lake": "lake", "lakes": "lake",
    "plage": "beach", "plages": "beach", "beach": "beach", "beaches": "beach",
    "cascade": "waterfall", "cascades": "waterfall", "waterfall": "waterfall",
    "neige": "snow", "snow": "snow", "pont": "bridge", "ponts": "bridge",
    "bridge": "bridge", "bridges": "bridge",
    "chateau": "castle", "chateaux": "castle", "castle": "castle",
    "jardin": "garden", "jardins": "garden", "garden": "garden",
    "fleur": "flower", "fleurs": "flower", "flower": "flower",
    "arbre": "tree", "arbres": "tree", "tree": "tree", "trees": "tree",
    "oiseau": "bird", "oiseaux": "bird", "bird": "bird", "birds": "bird",
    "rue": "street", "rues": "street", "street": "street",
    "solaire": "solar panel", "solar": "solar panel",
}
MODEL_NAME = "Xenova/clip-vit-base-patch32"


def english_visual_prompt(query: str) -> str | None:
    if not isinstance(query, str):
        return None
    normalized = unicodedata.normalize("NFKD", query.lower())
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    words = re.findall(r"[a-z]{3,}", normalized[:110])
    nouns = list(dict.fromkeys(VISUAL_PROMPTS[w] for w in words if w in VISUAL_PROMPTS))
    prompt = " ".join(nouns[:3])
    return prompt if len(prompt) >= 3 else None


def choose_with_clip(assets: list, scores: list[float | None]) -> tuple[list, bool]:
    """Prefer confidently better vision evidence among lexically similar media.

    Never override a candidate with substantially stronger Commons text support.
    Ambiguous/low-confidence scores leave existing order intact.
    """
    if len(assets) != len(scores) or not assets:
        raise ValueError("Invalid CLIP candidate scores")
    enriched = [
        replace(asset, semantic_score=round(score, 5) if score is not None else None)
        for asset, score in zip(assets, scores, strict=True)
    ]
    initial = scores[0]
    if initial is None:
        return enriched, False
    top_coverage = assets[0].query_term_coverage
    best_index = 0
    for i, (asset, score) in enumerate(zip(assets, scores, strict=True)):
        if score is None or score < 0.45 or score - initial < 0.12:
            continue
        if (top_coverage is not None and asset.query_term_coverage is not None
                and asset.query_term_coverage < top_coverage - 0.34):
            continue
        if score > (scores[best_index] or 0.0):
            best_index = i
    if best_index == 0:
        return enriched, False
    return [enriched[best_index]] + enriched[:best_index] + enriched[best_index+1:], True


def rerank(results: dict, work: Path, *, timeout: int = 360
           ) -> tuple[dict, dict[str, float | int | str], list[str]]:
    """Single opt-in CPU subprocess for all eligible Commons candidate groups."""
    began = time.perf_counter()
    stats: dict[str, float | int | str] = {
        "semantic_model": MODEL_NAME,
        "semantic_candidate_count": 0,
        "semantic_reordered_queries": 0,
        "semantic_status": "skipped",
        "semantic_seconds": 0.0,
    }
    candidates: list[dict] = []
    mappings: dict[int, tuple[str, int]] = {}
    for query, (assets, _, _) in results.items():
        prompt = english_visual_prompt(query)
        if prompt is None or len(assets) < 2:
            continue
        for i, asset in enumerate(assets):
            if asset.kind != "image" or not asset.file.is_file():
                continue
            item_id = len(candidates)
            candidates.append({"id": item_id, "prompt": prompt,
                               "path": str(asset.file.resolve())})
            mappings[item_id] = (query, i)
    stats["semantic_candidate_count"] = len(candidates)
    if len(candidates) < 2:
        return results, stats, []
    request_path = work / "clip_input.json"
    output_path = work / "clip_result.json"
    notices: list[str] = []
    try:
        request_path.write_text(json.dumps({"candidates": candidates}), encoding="utf-8")
        script = Path(__file__).resolve().parent / "semantic" / "clip_rank.mjs"
        process = subprocess.run(
            ["node", str(script), str(request_path), str(output_path)],
            capture_output=True, text=True, timeout=timeout, check=False,
            cwd=script.parent.parent,
        )
        if process.returncode or not output_path.is_file() or output_path.stat().st_size > 128_000:
            raise RuntimeError("CLIP worker did not return validated scores")
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        if payload.get("model") != MODEL_NAME or not isinstance(payload.get("scores"), list):
            raise ValueError("Unexpected CLIP model output")
        scores_by_id: dict[int, float] = {}
        for item in payload["scores"]:
            if not isinstance(item, dict):
                raise ValueError("Malformed CLIP score")
            id_, score = item.get("id"), item.get("score")
            if (not isinstance(id_, int) or isinstance(id_, bool) or id_ not in mappings
                    or id_ in scores_by_id or not isinstance(score, (float, int))
                    or isinstance(score, bool) or not math.isfinite(score)
                    or not 0 <= score <= 1):
                raise ValueError("Invalid CLIP score")
            scores_by_id[id_] = float(score)
        if len(scores_by_id) != len(candidates):
            raise ValueError("Incomplete CLIP response")
        scored: dict[str, dict[int, float]] = {}
        for id_, (query, index) in mappings.items():
            scored.setdefault(query, {})[index] = scores_by_id[id_]
        updated = dict(results)
        for query, values in scored.items():
            assets, warnings, timing = results[query]
            if len(values) != len(assets):
                continue
            reordered, changed = choose_with_clip(
                assets, [values.get(i) for i in range(len(assets))])
            updated[query] = (reordered, warnings, timing)
            stats["semantic_reordered_queries"] += int(changed)
        stats["semantic_status"] = "ok"
        return updated, stats, []
    except (OSError, RuntimeError, ValueError, KeyError, TypeError,
            subprocess.SubprocessError, json.JSONDecodeError):
        stats["semantic_status"] = "fallback"
        notices.append("Optional CLIP vision ranking unavailable; licensed Wikimedia text ranking retained")
        return results, stats, notices
    finally:
        stats["semantic_seconds"] = round(time.perf_counter() - began, 3)
        request_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
