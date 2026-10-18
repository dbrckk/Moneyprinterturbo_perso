#!/usr/bin/env python3
"""Human-labeled CLIP vs lexical-image selection quality evaluation.

Labels (0=wrong, 1=partial, 2=clear scene match) must be assigned *before*
running CLIP. A model fallback produces no comparative quality measurement.
All image files and licensing remain the responsibility of the dataset author.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
from time import perf_counter

# Allow the documented direct CLI invocation from any working directory.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from media_assets import MediaAsset
from semantic_rank import english_visual_prompt, rerank

MAX_CASES = 16
MAX_CANDIDATES = 32
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE_BYTES = 18 * 1024 * 1024


def evaluate(data: dict, directory: Path) -> dict:
    """Measure *human* relevance of actually selected CLIP and baseline media.

    CLIP must run successfully on every provided image; otherwise comparison
    scores are null, never a fabricated tie or a model-quality measurement.
    """
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object with benchmark cases")
    cases = data.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise ValueError("Expected between 1 and 16 labeled visual cases")

    groups = {}
    truth = {}
    candidates_seen = 0
    query_names: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Invalid benchmark case object")
        query, items = case.get("query"), case.get("candidates")
        if (not isinstance(query, str) or not 1 <= len(query) <= 110
                or query != query.strip() or english_visual_prompt(query) is None):
            raise ValueError("Query must be a supported visual concept")
        if query.casefold() in query_names:
            raise ValueError("Each benchmark query must be unique")
        query_names.add(query.casefold())
        if not isinstance(items, list) or not 2 <= len(items) <= 18:
            raise ValueError("Each case must contain 2 to 18 labeled images")
        candidates_seen += len(items)
        if candidates_seen > MAX_CANDIDATES:
            raise ValueError("At most 32 images per local CPU benchmark")

        assets = []
        labels = {}
        for index, item in enumerate(items):
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise ValueError("Invalid candidate file path")
            path_value = item["path"]
            if not path_value or len(path_value) > 2048:
                raise ValueError("Invalid candidate file path")
            path = Path(path_value).expanduser().resolve()
            label = item.get("relevance")
            if (type(label) is not int or label not in (0, 1, 2)
                    or not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS
                    or not 0 < path.stat().st_size <= MAX_IMAGE_BYTES
                    or str(path) in labels):
                raise ValueError("Invalid, duplicate or unlabeled benchmark image")
            # Never counterfeit a Commons URL, CC0 licence or verified source
            # for user-supplied evaluation files. The CLIP ranker only reads
            # local image paths and metadata scores, not asserted rights.
            assets.append(MediaAsset(
                path, "image", "local-benchmark", "unverified",
                "dataset-author", f"candidate-{index}",
                query_term_count=1, query_term_coverage=1.0,
            ))
            labels[str(path)] = label
        groups[query] = (assets, [], {})
        truth[query] = labels

    work_parent = Path(directory).expanduser()
    work_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work_parent) as temporary:
        started = perf_counter()
        ranked, stats, warnings = rerank(groups, Path(temporary))
        elapsed = perf_counter() - started

    stats = stats if isinstance(stats, dict) else {}
    status = str(stats.get("semantic_status", "unknown"))
    valid = status == "ok" and isinstance(ranked, dict)
    if "semantic_candidate_count" in stats:
        valid = valid and stats["semantic_candidate_count"] == candidates_seen
    if valid:
        # Check all groups at once before reporting any CLIP relevance scores.
        # No partial/malformed response is misrepresented as a paired result.
        for query, (original, _, _) in groups.items():
            result = ranked.get(query)
            if (not isinstance(result, tuple) or len(result) != 3
                    or not isinstance(result[0], list)
                    or len(result[0]) != len(original)
                    or {str(a.file) for a in result[0]} != set(truth[query])):
                valid = False
                break

    rows = []
    for query, (original, _, _) in groups.items():
        baseline = original[0]
        selection = ranked[query][0][0] if valid else None
        rows.append({
            "query": query,
            "baseline_relevance": truth[query][str(baseline.file)],
            "clip_relevance": truth[query][str(selection.file)] if valid else None,
            "changed": (str(selection.file) != str(baseline.file)) if valid else None,
            "semantic_score": selection.semantic_score if valid else None,
        })

    if not valid:
        warnings = list(warnings or []) + [
            "CLIP was unavailable or did not evaluate all candidates; "
            "comparison scores withheld."
        ]
    return {
        "cases": rows,
        "case_count": len(rows),
        "candidate_count": candidates_seen,
        "comparison_valid": valid,
        "comparison_status": status if valid or status != "ok" else "invalid_result",
        "baseline_mean_relevance": round(
            statistics.mean(row["baseline_relevance"] for row in rows), 3),
        "clip_mean_relevance": round(
            statistics.mean(row["clip_relevance"] for row in rows), 3) if valid else None,
        "improved": sum(row["clip_relevance"] > row["baseline_relevance"] for row in rows)
                    if valid else None,
        "worsened": sum(row["clip_relevance"] < row["baseline_relevance"] for row in rows)
                    if valid else None,
        "unchanged": sum(row["clip_relevance"] == row["baseline_relevance"] for row in rows)
                     if valid else None,
        "elapsed_seconds": round(elapsed, 3),
        "inference": stats,
        "warnings": list(warnings or []),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    destination = args.output.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = evaluate(dataset, destination.parent)
    destination.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "cases"},
                     ensure_ascii=False, indent=2))
    # A model fallback is a useful diagnostic report, but NOT a successful
    # quality benchmark for automation or future default-on decisions.
    return 0 if result["comparison_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
