#!/usr/bin/env python3
"""Benchmark actual JPEG/MP4 files; image content is reproducible synthetic test media."""
from pathlib import Path
import json
import re
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from free_studio import render


def ffmpeg(*args):
    result=subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",*args],
                          capture_output=True,text=True,timeout=150)
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:])


def visual_ssim(reference: Path, candidate: Path) -> float:
    """Measure frame-by-frame SSIM, not subjective aesthetic quality."""
    process=subprocess.run(
        ["ffmpeg","-hide_banner","-nostdin","-loglevel","info",
         "-i",str(reference),"-i",str(candidate),"-filter_complex",
         "[0:v][1:v]ssim","-an","-f","null","-"],
        capture_output=True,text=True,timeout=180)
    if process.returncode:
        raise RuntimeError("SSIM video comparison failed: "+process.stderr[-1200:])
    match=re.search(r"SSIM\s+Y:[0-9.]+.*?All:([0-9.]+)",process.stderr)
    if not match:
        raise RuntimeError("FFmpeg did not report a valid SSIM value")
    value=float(match.group(1))
    if not 0.95 <= value <= 1.0:
        raise RuntimeError(f"Excessive video quality variation: SSIM={value:.4f}")
    return round(value,6)


def main():
    folder=ROOT/"outputs"/"benchmarks"/"media"
    sources=folder/"fixtures"
    sources.mkdir(parents=True,exist_ok=True)
    ffmpeg("-f","lavfi","-i","testsrc2=s=640x480:r=1",
           "-frames:v","1",str(sources/"01-photo.jpg"))
    ffmpeg("-f","lavfi","-i","testsrc2=s=240x426:r=18","-t","2",
           "-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p",
           str(sources/"02-clip.mp4"))
    lines=[]
    comparisons={}
    narration="Les paysages révèlent une belle lumière. Le mouvement raconte une nouvelle histoire."
    for resolution in (720,1080):
        rows=[]
        for label,onepass in (("fused",False),("media-onepass",True)):
            target=folder/f"{resolution}-{label}.mp4"
            start=time.perf_counter()
            info=render("Étalonnage montage",narration,target,media_dir=sources,
                resolution=resolution,fps=12,mute=True,transition="fade",
                caption_style="kinetic",use_media_onepass=onepass)
            seconds=round(time.perf_counter()-start,3)
            result=subprocess.run([sys.executable,str(ROOT/"video_qa.py"),str(target)],
                                   capture_output=True,text=True,timeout=180)
            if result.returncode:
                raise RuntimeError(result.stdout[-1500:]+result.stderr[-1500:])
            quality=json.loads(target.with_suffix(".quality.json").read_text())
            if not quality["ok"] or quality["checks"]["pixel_format"]!="yuv420p":
                raise RuntimeError(f"QA failed: {target} {quality['errors']}")
            expected="direct-media-onepass" if onepass else "fused-xfade"
            if info["render_performance"]["compositing_pipeline"]!=expected:
                raise RuntimeError("Unexpected pipeline")
            rows.append({"resolution":resolution,"method":label,
                         "seconds":seconds,"codec":quality["checks"]["video_codec"],
                         "format":quality["checks"]["pixel_format"],
                         "duration":quality["checks"]["duration"],
                         "bytes":target.stat().st_size,
                         "metrics":info["render_performance"]})
            print(f"{resolution}p {label} {seconds}s: QA OK",flush=True)
        if abs(rows[0]["duration"]-rows[1]["duration"])>.15:
            raise RuntimeError("Videos have different durations")
        before,after=rows
        change=round(100*(before["seconds"]-after["seconds"])/before["seconds"],1)
        reference=folder/f"{resolution}-fused.mp4"
        candidate=folder/f"{resolution}-media-onepass.mp4"
        comparisons[str(resolution)]={"speedup_percent":change,
                                      "full_video_ssim":visual_ssim(reference,candidate)}
        print(f"{resolution}p: {change:+.1f}% faster, SSIM {comparisons[str(resolution)]['full_video_ssim']}",flush=True)
        lines+=rows
    (folder/"comparison.json").write_text(json.dumps({
        "source":"synthetic JPEG and moving MP4 files, not documentary photography",
        "scope":"actual render() time, excluding fixtures, job setup and QA",
        "narration":"muted","fps":12,"transition":"fade","rows":lines,
        "comparisons":comparisons
    },indent=2)+"\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
