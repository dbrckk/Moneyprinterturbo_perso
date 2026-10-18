#!/usr/bin/env python3
"""Reproducible, human-reviewable A/B of lexical versus optional CLIP ranking.

Collects REAL Commons thumbnails with the existing CC0/public-domain checks.
The report measures decisions, latency and provenance, NOT visual correctness:
no unreviewed automatic metric is called an accuracy improvement.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import html
import json
import math
from pathlib import Path
import shutil
import subprocess
import time

from media_assets import ALLOWED_LICENSES, commons_assets
from review_mobile import build_mobile_review
from semantic_rank import MODEL_NAME, rerank

# Fixed, bounded visual topics. Keep searches representative yet generic:
# ambiguous people, exact locations and abstract narration are out of scope.
BENCHMARK_QUERIES = (
    "mountain landscape",
    "forest landscape",
    "river landscape",
    "castle",
    "waterfall",
)
MAX_CASES = len(BENCHMARK_QUERIES)


def source_record(asset, images: Path, stem: str) -> dict[str, object]:
    """Only copy thumbnails the production license checks have accepted."""
    if asset.license.lower() not in ALLOWED_LICENSES:
        raise ValueError("Benchmark asset failed Commons license allowlist")
    if not asset.source_url.startswith("https://commons.wikimedia.org/wiki/"):
        raise ValueError("Benchmark source must be a Wikimedia Commons file page")
    if not asset.file.is_file() or not 0 < asset.file.stat().st_size <= 18 * 1024 * 1024:
        raise ValueError("Benchmark image missing or oversized")
    suffix = asset.file.suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
        raise ValueError("Benchmark input must be a supported thumbnail")
    destination = images / f"{stem}{suffix}"
    shutil.copyfile(asset.file, destination)
    return {
        "image": f"images/{destination.name}",
        "title": asset.title,
        "source": asset.source_url,
        "license": asset.license,
        "author": asset.author,
        "lexical_coverage": asset.query_term_coverage,
        "clip_score": asset.semantic_score,
        "medium_hint": asset.visual_medium,
    }


def _safe_text(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def render_report(summary: dict) -> str:
    """Offline-friendly review page; strings are escaped, no scripts or CDNs."""
    blocks = []
    for case in summary["cases"]:
        query = _safe_text(case["query"])
        if case["status"] != "evaluated":
            blocks.append(f'<section><h2>{query}</h2><p>Non évalué : '
                          f'{_safe_text(case["reason"])}</p></section>')
            continue
        cards = []
        for key, heading in (("baseline", "Classement initial"),
                             ("clip", "Classement après CLIP")):
            item = case[key]
            link = _safe_text(item["source"])
            cards.append(
                f'<figure><img src="{_safe_text(item["image"])}" '
                f'alt="{_safe_text(item["title"])}" loading="lazy">'
                f'<figcaption><b>{heading}</b>'
                f'<span>{_safe_text(item["title"])}</span>'
                f'<span>Licence : {_safe_text(item["license"])}</span>'
                f'<span>Auteur : {_safe_text(item["author"])}</span>'
                f'<span>Couverture texte : {_safe_text(item["lexical_coverage"])}</span>'
                f'<span>Score CLIP relatif : {_safe_text(item["clip_score"])}</span>'
                f'<a href="{link}" target="_blank" rel="noopener noreferrer">Source Wikimedia Commons</a>'
                f'</figcaption></figure>'
            )
        blocks.append(f'<section><h2>{query} — '
                      f'{"Choix modifié" if case["changed"] else "Choix conservé"}</h2>'
                      f'<div class="pair">{"".join(cards)}</div></section>')
    style = """
    body{font:16px system-ui,sans-serif;line-height:1.45;background:#101725;color:#f5f7fc;
         margin:0 auto;max-width:1050px;padding:24px}
    h1{font-size:1.7rem}h2{font-size:1.2rem}section{border-top:1px solid #44546b;padding:18px 0}
    .pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
    figure{margin:0;border:1px solid #4b5c70;border-radius:12px;overflow:hidden}
    img{display:block;width:100%;height:300px;object-fit:contain;background:#222c3c}
    figcaption{display:grid;gap:4px;padding:12px;overflow-wrap:anywhere}
    a{color:#a9d4ff}small,p{color:#c8d2e4}
    @media(max-width:650px){.pair{grid-template-columns:1fr}img{height:260px}}
    """
    count = summary["evaluated_cases"]
    heading = (f'{count} paires évaluées · {summary["changed_cases"]} changements '
               f'· {summary["retained_cases"]} choix conservés')
    return (
        '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        'img-src \'self\'; style-src \'unsafe-inline\';">'
        '<title>MoneyPrinter — revue A/B images</title><style>'
        + style + '</style></head><body><h1>CLIP / Commons — revue A/B</h1>'
        + f'<p>{_safe_text(heading)}</p>'
        + '<p>Ce rapport ne certifie pas la pertinence visuelle. '
          'Les thèmes et licences proviennent des métadonnées Commons. '
          'Inspecter chaque paire avant de juger laquelle illustre mieux la narration. '
          'Les scores CLIP sont relatifs à des libellés fixes, pas des probabilités de vérité.</p>'
        + f'<p>Statut IA : {_safe_text(summary["semantic_status"])} · '
          f'Temps CLIP : {_safe_text(summary["semantic_seconds"])} s · '
          f'Temps Commons : {_safe_text(summary["commons_seconds"])} s.</p>'
        + ''.join(blocks) + '</body></html>\n'
    )


def make_contact_sheet(summary: dict, images: Path, destination: Path) -> bool:
    """Optional contact PNG optimized for direct Android download.

    Order matches report case order. Even unchanged images appear twice:
    left is baseline, right is CLIP, with metadata in report.html.
    """
    cases = [c for c in summary["cases"] if c["status"] == "evaluated"]
    if not cases:
        return False
    inputs = []
    for case in cases:
        for key in ("baseline", "clip"):
            rel = case[key]["image"]
            # The only accepted paths are our own generated images/<filename>.
            if not rel.startswith("images/") or "/" in rel[7:]:
                return False
            path = images / rel.removeprefix("images/")
            if not path.is_file():
                return False
            inputs.extend(["-i", str(path)])
    filters = []
    for index in range(len(cases) * 2):
        filters.append(
            f"[{index}:v]scale=256:224:force_original_aspect_ratio=decrease,"
            f"pad=256:224:(ow-iw)/2:(oh-ih)/2:color=0x202b3b,"
            f"setsar=1[v{index}]"
        )
    for index in range(len(cases)):
        filters.append(f"[v{index*2}][v{index*2+1}]hstack=2[row{index}]")
    stacked = ''.join(f"[row{i}]" for i in range(len(cases)))
    filters.append(f"{stacked}vstack=inputs={len(cases)}[sheet]")
    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        *inputs, "-filter_complex", ";".join(filters), "-map", "[sheet]",
        "-frames:v", "1", "-update", "1", str(destination),
    ]
    try:
        finished = subprocess.run(command, capture_output=True, timeout=90, check=False)
        return finished.returncode == 0 and destination.is_file() and destination.stat().st_size > 1000
    except (OSError, subprocess.SubprocessError):
        return False


def run_benchmark(output: Path, *, queries=BENCHMARK_QUERIES,
                  fetch=commons_assets, compare=rerank,
                  make_sheet=make_contact_sheet) -> dict:
    if not isinstance(queries, (tuple, list)) or not 1 <= len(queries) <= MAX_CASES:
        raise ValueError("Benchmark must have 1 to 5 fixed visual queries")
    if any(q not in BENCHMARK_QUERIES for q in queries) or len(set(queries)) != len(queries):
        raise ValueError("Only unique curated visual benchmark queries are supported")
    output.mkdir(parents=True, exist_ok=True)
    images = output / "images"
    images.mkdir(parents=True, exist_ok=True)
    work = output / "source"
    work.mkdir(parents=True, exist_ok=True)

    collected: dict[str, tuple] = {}
    notices: dict[str, list[str]] = {}
    query_diagnostics: dict[str, dict[str, float | int]] = {}
    started = time.perf_counter()
    def retrieve(entry: tuple[int, str]) -> tuple[str, list, list[str], dict]:
        index, query = entry
        folder = work / f"case_{index:02}"
        folder.mkdir(parents=True, exist_ok=True)
        details: dict[str, float | int] = {}
        try:
            # The production Commons fetcher provides real search telemetry.
            # Injected fixture fetchers retain their older strict signature.
            if fetch is commons_assets:
                assets, warnings = fetch(
                    query, folder, limit=2, target_aspect=9 / 16,
                    timings=details)
            else:
                assets, warnings = fetch(query, folder, limit=2, target_aspect=9 / 16)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            assets, warnings = [], [f"Source lookup failed: {type(exc).__name__}"]
        return query, assets, warnings, details

    # Media downloads are independent and bounded to three parallel Commons
    # requests; model inference still happens once after all lookups finish.
    with ThreadPoolExecutor(max_workers=min(3, len(queries))) as pool:
        for query, assets, warnings, details in pool.map(retrieve, enumerate(queries)):
            collected[query] = (assets, warnings, {})
            notices[query] = warnings
            query_diagnostics[query] = details
    commons_seconds = round(time.perf_counter() - started, 3)

    # One model initialization for the full comparison, no per-scene reloading.
    try:
        ranked, metrics, problems = compare(collected, work)
    except (OSError, RuntimeError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        ranked = collected
        metrics = {"semantic_status": "fallback", "semantic_seconds": 0.0}
        problems = [f"CLIP failed: {type(exc).__name__}"]
    semantic_ok = metrics.get("semantic_status") == "ok"

    rows = []
    for query in queries:
        raw = collected[query][0]
        transformed = ranked.get(query, (raw, [], {}))[0]
        if len(raw) < 2:
            rows.append({"query": query, "status": "skipped",
                         "reason": "; ".join(notices[query]) or "Fewer than two verified CC0/PD images"})
            continue
        if not semantic_ok or len(transformed) != len(raw):
            rows.append({"query": query, "status": "skipped",
                         "reason": "; ".join(problems) or "CLIP unavailable"})
            continue
        try:
            left = source_record(raw[0], images, f"{queries.index(query):02}-baseline")
            right = source_record(transformed[0], images, f"{queries.index(query):02}-clip")
        except (ValueError, OSError) as exc:
            rows.append({"query": query, "status": "skipped",
                         "reason": f"Asset validation failed: {type(exc).__name__}"})
            continue
        rows.append({"query": query, "status": "evaluated",
                     "changed": raw[0].source_url != transformed[0].source_url,
                     "baseline": left, "clip": right})
    valid = [x for x in rows if x["status"] == "evaluated"]
    changed = sum(x["changed"] for x in valid)
    summary = {
        "description": "A/B candidate decisions only; human-rated visual accuracy not measured",
        "queries": list(queries),
        "model": MODEL_NAME,
        "semantic_status": str(metrics.get("semantic_status", "fallback")),
        "commons_seconds": commons_seconds,
        "semantic_seconds": float(metrics.get("semantic_seconds", 0.0)),
        "requested_cases": len(queries),
        "evaluated_cases": len(valid),
        "changed_cases": changed,
        "retained_cases": len(valid) - changed,
        "human_reviews_completed": 0,
        "licensed_pair_coverage": round(len(valid) / len(queries), 3),
        "commons_api_queries": sum(int(d.get("api_queries", 0))
                                   for d in query_diagnostics.values()),
        "fallback_queries_attempted": sum(int(d.get("fallback_search_attempted", 0))
                                           for d in query_diagnostics.values()),
        "fallback_candidates_added": sum(int(d.get("fallback_search_added", 0))
                                         for d in query_diagnostics.values()),
        "near_duplicate_downloads_skipped": sum(
            int(d.get("near_duplicate_downloads_skipped", 0))
            for d in query_diagnostics.values()),
        "search_diagnostics": query_diagnostics,
        "warnings": problems,
        "cases": rows,
    }
    # Generate a single-file offline Android review. A preview failure must
    # never fabricate completed human votes or suppress the normal A/B report.
    try:
        summary["mobile_review_pairs"] = build_mobile_review(
            summary, images, output / "review-mobile.html")
    except (OSError, ValueError, RuntimeError, UnicodeError,
            subprocess.SubprocessError) as exc:
        summary["mobile_review_pairs"] = 0
        summary["warnings"].append(
            f"Offline mobile review unavailable: {type(exc).__name__}")
    summary["mobile_review_ready"] = summary["mobile_review_pairs"] == len(valid) and bool(valid)
    summary["contact_sheet_ready"] = bool(make_sheet(summary, images, output / "contact-sheet.png"))
    (output / "metrics.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "report.html").write_text(render_report(summary), encoding="utf-8")
    with (output / "human-review.csv").open("w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(["query", "baseline_url", "clip_url",
                         "human_choice_baseline_clip_tie_both_bad", "reviewer_notes"])
        for case in valid:
            writer.writerow([case["query"], case["baseline"]["source"],
                             case["clip"]["source"], "", ""])
    return summary


def score_human_reviews(metrics: dict, review_csv: Path) -> dict:
    """Aggregate *actual* human preferences; never infer votes from CLIP scores.

    On unchanged candidates, a preference for either algorithm is invalid:
    they showed exactly the same source image.
    """
    cases = {
        item["query"]: item
        for item in metrics.get("cases", [])
        if item.get("status") == "evaluated"
    }
    if (len(cases) != metrics.get("evaluated_cases") or
            not isinstance(metrics.get("evaluated_cases"), int)):
        raise ValueError("Invalid benchmark metrics or incomplete case list")
    if not review_csv.is_file() or review_csv.stat().st_size > 2_000_000:
        raise ValueError("Missing or oversized human review CSV")
    expected_fields = [
        "query", "baseline_url", "clip_url",
        "human_choice_baseline_clip_tie_both_bad", "reviewer_notes",
    ]
    outcomes = {"baseline": 0, "clip": 0, "tie": 0, "both_bad": 0}
    answered = 0
    changed_reviewed = 0
    seen = set()
    with review_csv.open(newline="", encoding="utf-8-sig") as fp:
        reader = csv.DictReader(fp)
        if reader.fieldnames != expected_fields:
            raise ValueError("Human review CSV header does not match benchmark template")
        for item in reader:
            query = item["query"]
            if query not in cases or query in seen or len(seen) >= MAX_CASES:
                raise ValueError("Review contains duplicate, missing or unknown scene")
            case = cases[query]
            if (item["baseline_url"] != case["baseline"]["source"] or
                    item["clip_url"] != case["clip"]["source"]):
                raise ValueError("Review CSV sources do not match original benchmark")
            seen.add(query)
            choice = item["human_choice_baseline_clip_tie_both_bad"].strip().lower()
            if not choice:
                continue
            if choice not in outcomes:
                raise ValueError("Allowed human ratings: baseline, clip, tie, both_bad")
            if not case["changed"] and choice in ("baseline", "clip"):
                raise ValueError("Identical photos cannot be assigned different winners")
            outcomes[choice] += 1
            answered += 1
            if case["changed"]:
                changed_reviewed += 1
    if seen != set(cases):
        raise ValueError("Human review CSV does not contain exactly the benchmark pairs")
    decided = outcomes["clip"] + outcomes["baseline"]
    return {
        "human_reviews_completed": answered,
        "human_reviews_pending": len(cases) - answered,
        "changed_cases_reviewed": changed_reviewed,
        "human_choices": outcomes,
        "clip_wins_over_baseline": outcomes["clip"] - outcomes["baseline"],
        "clip_share_of_decisive_votes": round(outcomes["clip"] / decided, 3) if decided else None,
        "disclaimer": "Small voluntary visual review; not an objective quality certification, no automatic default switch",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("outputs/clip-ab-benchmark"))
    parser.add_argument("--score-reviews", type=Path,
                        help="Score completed human-review.csv with existing metrics; no model/network")
    args = parser.parse_args(argv)
    if args.score_reviews is not None:
        metrics_path = args.output / "metrics.json"
        if not metrics_path.is_file() or metrics_path.stat().st_size > 2_000_000:
            parser.error("Missing benchmark metrics.json")
        ratings = score_human_reviews(
            json.loads(metrics_path.read_text(encoding="utf-8")), args.score_reviews)
        (args.output / "human-score.json").write_text(
            json.dumps(ratings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(ratings, ensure_ascii=False, indent=2))
        return 0
    report = run_benchmark(args.output)
    print(json.dumps({key: report[key] for key in (
        "semantic_status", "requested_cases", "evaluated_cases",
        "changed_cases", "retained_cases", "licensed_pair_coverage",
        "commons_api_queries", "fallback_queries_attempted",
        "fallback_candidates_added", "commons_seconds",
        "semantic_seconds", "contact_sheet_ready")}, indent=2))
    for case in report["cases"]:
        suffix = "paired" if case["status"] == "evaluated" else case.get("reason", "insufficient licensed photos")
        print(f"  {case['query']}: {suffix}")
    # Exit non-zero when there is no usable A/B evaluation. It must not be
    # misrepresented as success if the network or model was unavailable.
    return 0 if report["semantic_status"] == "ok" and report["evaluated_cases"] >= 2 else 2


if __name__ == "__main__":
    raise SystemExit(main())
