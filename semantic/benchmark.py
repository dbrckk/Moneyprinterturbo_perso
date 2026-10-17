#!/usr/bin/env python3
"""Reproducible paired image-ranking benchmark, with independent human labels.

Input JSON: {"cases":[{"query":"forest","candidates":[{"path":"absolute image path",
"relevance":0|1|2}, ...]}]}. Human relevance labels must be recorded BEFORE
running the comparison. Results contain both selection rates and timing.
"""
import argparse
import json
from pathlib import Path
import statistics
import tempfile
from time import perf_counter
from media_assets import MediaAsset
from semantic_rank import rerank

def evaluate(data, directory):
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Nonempty cases required")
    groups = {}
    truth = {}
    for idx, case in enumerate(cases):
        query = case.get("query")
        items = case.get("candidates")
        if not isinstance(query, str) or not isinstance(items, list) or len(items) < 2:
            raise ValueError("Invalid benchmark case")
        if len(items) > 18:
            raise ValueError("Too many images in benchmark case")
        assets = []
        labels = {}
        for n, item in enumerate(items):
            path = Path(item["path"]).resolve()
            label = item["relevance"]
            if not path.is_file() or label not in (0, 1, 2) or isinstance(label, bool):
                raise ValueError("Invalid image or human relevance label")
            assets.append(MediaAsset(path, "image",
                f"https://commons.wikimedia.org/wiki/File:benchmark_{idx}_{n}.jpg",
                "CC0", "Benchmark", f"candidate-{n}",
                query_term_count=1, query_term_coverage=1.0))
            labels[str(path)] = label
        key = f"{query} {idx}" if query in groups else query
        # CLIP requires a recognizable visual concept; duplicate queries get
        # unique keys while preserving the actual query for the model.
        if key != query:
            raise ValueError("Use unique queries per benchmark")
        groups[key] = (assets, [], {})
        truth[key] = labels
    with tempfile.TemporaryDirectory(dir=directory) as work:
        start = perf_counter()
        ranked, stats, warnings = rerank(groups, Path(work))
        seconds = perf_counter() - start
    rows = []
    for query, (original, _, _) in groups.items():
        selected = ranked[query][0][0]
        baseline = original[0]
        rows.append({
            "query": query,
            "baseline_relevance": truth[query][str(baseline.file)],
            "clip_relevance": truth[query][str(selected.file)],
            "changed": str(selected.file) != str(baseline.file),
            "semantic_score": selected.semantic_score,
        })
    return {
        "cases": rows,
        "case_count": len(rows),
        "baseline_mean_relevance": round(statistics.mean(r["baseline_relevance"] for r in rows), 3),
        "clip_mean_relevance": round(statistics.mean(r["clip_relevance"] for r in rows), 3),
        "improved": sum(r["clip_relevance"] > r["baseline_relevance"] for r in rows),
        "worsened": sum(r["clip_relevance"] < r["baseline_relevance"] for r in rows),
        "unchanged": sum(r["clip_relevance"] == r["baseline_relevance"] for r in rows),
        "elapsed_seconds": round(seconds, 2),
        "inference": stats,
        "warnings": warnings,
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.dataset.read_text(encoding="utf-8"))
    result = evaluate(data, args.output.resolve().parent)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k != "cases"}, indent=2))

if __name__ == "__main__":
    main()
