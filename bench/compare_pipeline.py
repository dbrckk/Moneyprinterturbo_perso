#!/usr/bin/env python3
"""Matched-workload benchmark for the legacy and optimized FFmpeg pipeline.

Run from the project root: python bench/compare_pipeline.py
All MP4s are generated with the same settings. Results depend on CPU load.
"""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from free_studio import render

def main():
    root=ROOT/"outputs"/"benchmarks"
    root.mkdir(parents=True,exist_ok=True)
    narration="La forêt révèle des paysages spectaculaires. La lumière traverse les montagnes."
    rows=[]
    for resolution in (720,1080):
        group=[]
        for version in ("legacy","fused"):
            file=root/f"compare-{resolution}-{version}.mp4"
            started=time.perf_counter()
            info=render("Paysages français",narration,file,resolution=resolution,
                        aspect="9:16",fps=12,mute=True,transition="fade",
                        visual_style="cinematic",caption_style="kinetic",
                        use_fused_xfade=(version=="fused"))
            spent=round(time.perf_counter()-started,3)
            completed=subprocess.run([sys.executable,str(ROOT/"video_qa.py"),str(file)],
                                     cwd=ROOT,capture_output=True,text=True,timeout=180)
            if completed.returncode:
                raise RuntimeError(f"Quality check failed {file}: {completed.stdout[-1500:]} {completed.stderr[-1200:]}")
            quality=json.loads(file.with_suffix(".quality.json").read_text())
            if not quality["ok"]:
                raise RuntimeError(f"Invalid benchmark output: {file}: {quality['errors']}")
            group.append({
                "resolution":resolution,
                "method":version,
                "video_duration_seconds":info["duration_seconds"],
                "render_wall_seconds":spent,
                "metrics":info["render_performance"],
                "size_bytes":file.stat().st_size,
                "qa_ok":quality["ok"],
                "video":file.name,
            })
            print(f"{resolution}p {version}: {spent:.3f}s, QA={quality['ok']}",flush=True)
        if abs(group[0]["video_duration_seconds"]-group[1]["video_duration_seconds"])>.01:
            raise RuntimeError("Versions use different narrative timing")
        legacy,optimized=group[0],group[1]
        improvement=round(100*(legacy["render_wall_seconds"]-optimized["render_wall_seconds"])/legacy["render_wall_seconds"],1)
        rows.extend(group)
        print(f"{resolution}p total render difference: {improvement:+.1f}% faster with fusion",flush=True)
    output=root/"comparison.json"
    output.write_text(json.dumps({
       "description":"Real engine render, no voice/network, 12 fps, 2 animated scenes, 720p and 1080p",
       "baseline":"same codec/preset and assets, legacy intermediate fade encoded before caption overlay",
       "rows":rows},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Artifact",output)

if __name__=="__main__":
    main()
