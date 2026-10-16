#!/usr/bin/env python3
"""Live, capped comparison of alternate public-domain Commons searches.

This is a source-discovery probe; it does not assert that any image is
photographic unless metadata provides evidence.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from media_assets import commons_assets

SEARCHES = (
    "montagne panorama",
    "montagne paysage",
    "mountain landscape",
    "mountain photograph",
    "Alpes montagne",
)


def main():
    output=ROOT/"outputs"/"commons-alternative-research"
    output.mkdir(parents=True,exist_ok=True)
    stats=[]
    seen=set()
    started=time.perf_counter()
    for number,query in enumerate(SEARCHES):
        folder=output/f"query-{number}"
        folder.mkdir(parents=True,exist_ok=True)
        elapsed={}
        items, notices=commons_assets(query,folder,limit=3,timings=elapsed)
        unique=[]
        for item in items:
            if item.source_url not in seen:
                unique.append(item)
            seen.add(item.source_url)
        stats.append({
            "query": query,
            "licensed_candidates":len(items),
            "new_distinct_sources":len(unique),
            "items":[{"title":i.title,"url":i.source_url,"license":i.license,
                      "visual_medium":i.visual_medium} for i in items],
            "timings":elapsed,"warnings":notices,
        })
        print(query,"licensed",len(items),"new",len(unique),
              "mediums", [item.visual_medium for item in items],flush=True)
    payload={
      "tests":"five live Commons query variants, CC0/public domain only, 3 images per variant maximum",
      "elapsed_seconds":round(time.perf_counter()-started,3),
      "unique_sources_found":len(seen),
      "candidates":stats,
      "caution":"These are permissive candidate counts; visual content still requires human review.",
    }
    (output/"results.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("TOTAL DISTINCT SOURCES",len(seen),flush=True)


if __name__=="__main__":
    main()
