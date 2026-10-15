#!/usr/bin/env python3
"""Free, reproducible short-video generator: Python stdlib + FFmpeg + eSpeak.

Optional local Ollama provides *real* AI scripting. Without it, a clearly
identified deterministic outline is used so video rendering still works offline.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
from urllib import request

from media_assets import MediaAsset, commons_scene_assets, local_assets, scene_search_queries

PALETTE = ("0x0b1025", "0x153653", "0x23315f", "0x234747", "0x45315c", "0x16425b")
DEFAULT_MODELS = ("llama3.2:1b", "qwen2.5:1.5b", "gemma3:1b")
ASPECTS = {"9:16": (9, 16), "16:9": (16, 9), "1:1": (1, 1)}
LANG_VOICES = {"fr": "fr", "en": "en", "es": "es", "de": "de", "it": "it", "pt": "pt"}


def check_program(program: str) -> None:
    if not shutil.which(program):
        raise RuntimeError(f"Missing system dependency: {program}. Install FFmpeg and eSpeak first.")


def execute(args: list[str], *, cwd: Path | None = None, timeout: int = 900) -> None:
    completed = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if completed.returncode:
        raise RuntimeError(f"{args[0]} failed ({completed.returncode}): {completed.stderr[-2500:]}")


def split_script(text: str, *, max_segments: int = 18) -> list[str]:
    if not isinstance(text, str) or not text.strip() or len(text) > 5000:
        raise ValueError("Script must be 1–5000 characters")
    compact = re.sub(r"[ \t\f\v]+", " ", text).strip()
    segments = [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+|\n+", compact) if part.strip()]
    result: list[str] = []
    for part in segments:
        # Keep the caption sizes manageable, without discarding long sentences.
        words = part.split()
        buffer: list[str] = []
        for word in words:
            if buffer and len(" ".join(buffer + [word])) > 105:
                result.append(" ".join(buffer))
                buffer = []
            buffer.append(word)
        if buffer:
            result.append(" ".join(buffer))
    if not result or len(result) > max_segments:
        raise ValueError(f"Script must yield between 1 and {max_segments} caption segments")
    return result


def template_script(topic: str, language: str) -> str:
    subject = re.sub(r"\s+", " ", topic).strip()
    if not (2 <= len(subject) <= 140) or any(c in subject for c in "\r\n"):
        raise ValueError("Topic must contain between 2 and 140 characters")
    if language == "fr":
        return (f"Découvrons {subject}. "
                f"Voici les idées essentielles pour comprendre {subject}. "
                "Regardons les exemples et les usages les plus utiles. "
                "Pour aller plus loin, confrontez ces informations à des sources fiables.")
    translated = {
        "es": (f"Descubramos {subject}.", f"Estas son las ideas principales para entender {subject}.", "Veamos algunos ejemplos y aplicaciones útiles.", "Consulta fuentes fiables para obtener más información."),
        "de": (f"Entdecken wir {subject}.", f"Das sind die wichtigsten Ideen zu {subject}.", "Betrachten wir praktische Beispiele und Anwendungen.", "Prüfe diese Informationen anhand zuverlässiger Quellen."),
        "it": (f"Scopriamo {subject}.", f"Ecco le idee principali per capire {subject}.", "Vediamo alcuni esempi e applicazioni utili.", "Verifica queste informazioni con fonti affidabili."),
        "pt": (f"Vamos explorar {subject}.", f"Estas são as ideias principais sobre {subject}.", "Vejamos exemplos e aplicações úteis.", "Verifique estas informações em fontes confiáveis."),
        "en": (f"Let's explore {subject}.", f"Here are the key ideas behind {subject}.", "Consider the most useful examples and real-world applications.", "To learn more, verify these ideas using reliable sources."),
    }
    return " ".join(translated[language])


def ollama_script(topic: str, language: str, model: str, url: str) -> str:
    # No remote addresses: local inference only, no surprise network/API billing.
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1") or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Ollama must run on local http://127.0.0.1 or localhost")
    if parsed.path not in ("", "/"):
        raise ValueError("Ollama URL must be the host and optional port")
    prompt = (f"Write an engaging, factual short-video narration in {language} about {topic}. "
              "Return plain narration only, 4 to 6 concise sentences, 60 to 110 words. "
              "Avoid unverified statistics, copyrighted lyrics, and hashtags.")
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.5}}).encode()
    req = request.Request(url.rstrip("/") + "/api/generate", data=payload, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=180) as resp:
        data = json.load(resp)
    script = data.get("response", "")
    split_script(script)
    return script


def duration_seconds(path: Path) -> float:
    completed = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], capture_output=True, text=True, check=True, timeout=25)
    duration = float(completed.stdout.strip())
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("Invalid audio duration")
    return duration


def srt_timestamp(seconds: float) -> str:
    ms = round(max(0.0, seconds) * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def size_for(aspect: str, resolution: int) -> tuple[int, int]:
    w_ratio, h_ratio = ASPECTS[aspect]
    # Resolution refers to the short side, 360/720/1080.
    if w_ratio <= h_ratio:
        width = resolution
        height = round(resolution * h_ratio / w_ratio / 2) * 2
    else:
        height = resolution
        width = round(resolution * w_ratio / h_ratio / 2) * 2
    return width, height


def make_audio(segments: list[str], lang: str, work: Path, *, mute: bool,
               tts: str = "espeak", piper_model: Path | None = None,
               speech_rate: int = 145) -> tuple[list[float], Path | None]:
    if mute:
        return [max(2.6, min(9.0, len(s) / 16.0)) for s in segments], None
    if tts not in ("espeak", "piper"):
        raise ValueError("Unsupported speech engine")
    if tts == "piper":
        check_program("piper")
        if piper_model is None:
            raise ValueError("Piper requires --piper-model pointing to a local ONNX voice")
        piper_model = piper_model.expanduser().resolve()
        if (piper_model.suffix.lower() != ".onnx" or not piper_model.is_file()
                or not piper_model.with_suffix(".onnx.json").is_file()):
            raise ValueError("Piper requires local .onnx and .onnx.json model files")
    else:
        if not 80 <= speech_rate <= 220:
            raise ValueError("eSpeak speech rate must be between 80 and 220 words/minute")
        check_program("espeak")
    filenames: list[Path] = []
    durations: list[float] = []
    for idx, sentence in enumerate(segments):
        audio_path = work / f"voice_{idx:02}.wav"
        if tts == "piper":
            # Piper runs locally using a voice already installed by the user.
            # No implicit model download or API request is permitted here.
            process = subprocess.run(
                ["piper", "--model", str(piper_model), "--output_file", str(audio_path)],
                input=sentence + "\n", text=True, capture_output=True, timeout=150,
            )
            if process.returncode:
                raise RuntimeError(f"Piper failed: {process.stderr[-1200:]}")
        else:
            execute(["espeak", "-v", LANG_VOICES[lang], "-s", str(speech_rate),
                     "-w", str(audio_path), sentence], timeout=35)
        durations.append(duration_seconds(audio_path))
        filenames.append(audio_path)
    manifest = work / "audio_list.txt"
    manifest.write_text("".join(f"file '{path.name}'\n" for path in filenames), encoding="utf-8")
    audio = work / "narration.m4a"
    execute(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", manifest.name, "-c:a", "aac", "-b:a", "160k", audio.name], cwd=work)
    return durations, audio


def make_captions(segments: list[str], durations: list[float], work: Path) -> list[dict[str, object]]:
    chunks: list[str] = []
    timings: list[dict[str, object]] = []
    current = 0.0
    for idx, (text, duration) in enumerate(zip(segments, durations, strict=True)):
        end = current + duration
        chunks.append(f"{idx+1}\n{srt_timestamp(current)} --> {srt_timestamp(end)}\n{text}\n\n")
        timings.append({"text": text, "start": round(current, 3), "end": round(end, 3)})
        current = end
    (work / "captions.srt").write_text("".join(chunks), encoding="utf-8")
    return timings


def render_scene(asset: MediaAsset | None, duration: float, index: int, work: Path,
                 *, width: int, height: int, fps: int) -> Path:
    """Normalize one photo/video to a silent CFR clip with a gentle camera move."""
    target = work / f"scene_{index:02}.mp4"
    frames = max(1, round(duration * fps))
    if asset is None:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
               "-i", f"color=c={PALETTE[index % len(PALETTE)]}:s={width}x{height}:r={fps}"]
        effect = "format=yuv420p"
    elif asset.kind == "image":
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-loop", "1",
               "-framerate", str(fps), "-i", str(asset.file)]
        effect = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                  f"crop={width}:{height},setsar=1,"
                  f"zoompan=z='min(zoom+0.0008,1.10)':d=1:s={width}x{height}:fps={fps},"
                  "eq=contrast=1.05:saturation=1.07,format=yuv420p")
    else:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-stream_loop", "-1",
               "-i", str(asset.file)]
        effect = (f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=increase,"
                  f"crop={width}:{height},setsar=1,"
                  "eq=contrast=1.05:saturation=1.07,format=yuv420p")
    cmd += ["-an", "-vf", effect, "-frames:v", str(frames), "-r", str(fps),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            str(target)]
    execute(cmd, timeout=240)
    if target.stat().st_size < 1000:
        raise RuntimeError("Media scene output is empty")
    return target


def prepare_media_background(assets: list[MediaAsset | None], durations: list[float], work: Path,
                             *, width: int, height: int, fps: int) -> Path:
    if len(assets) != len(durations):
        raise ValueError("Each scene must have exactly one media slot")
    scenes = [render_scene(asset, duration, i, work, width=width,
                           height=height, fps=fps) for i, (asset, duration) in enumerate(zip(assets, durations))]
    manifest = work / "scenes.txt"
    manifest.write_text("".join(f"file '{scene.name}'\n" for scene in scenes), encoding="utf-8")
    output = work / "background.mp4"
    execute(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
             "-safe", "0", "-i", manifest.name, "-c", "copy", output.name], cwd=work)
    return output


def render(topic: str, script: str, output: Path, *, lang: str = "fr", aspect: str = "9:16",
           resolution: int = 720, mute: bool = False, fps: int = 24,
           media_dir: Path | None = None, commons: bool = False,
           scene_queries: list[str] | None = None, tts: str = "espeak",
           piper_model: Path | None = None, speech_rate: int = 145) -> dict[str, object]:
    check_program("ffmpeg")
    check_program("ffprobe")
    if not (1 <= fps <= 60):
        raise ValueError("Frame rate must be 1–60")
    if aspect not in ASPECTS or resolution not in (360, 720, 1080) or lang not in LANG_VOICES:
        raise ValueError("Unsupported aspect, resolution or voice language")
    sentences = split_script(script)
    if scene_queries is not None and not commons:
        raise ValueError("Scene search queries require --commons")
    if tts not in ("espeak", "piper") or (tts == "espeak" and piper_model is not None):
        raise ValueError("Invalid voice engine or model combination")
    if tts == "piper" and piper_model is None and not mute:
        raise ValueError("Piper voice requires a local model")
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    width, height = size_for(aspect, resolution)
    with tempfile.TemporaryDirectory(prefix="mpt-free-") as tmp:
        work = Path(tmp)
        durations, audio = make_audio(sentences, lang, work, mute=mute,
                                      tts=tts, piper_model=piper_model,
                                      speech_rate=speech_rate)
        segments = make_captions(sentences, durations, work)
        total = sum(durations)
        warnings: list[str] = []
        slots: list[MediaAsset | None] = [None] * len(sentences)
        queries: list[str] | None = None
        if media_dir is not None:
            local, notices = local_assets(media_dir.expanduser().resolve())
            warnings.extend(notices)
            for i in range(len(sentences)):
                if local and (not commons or i < len(local)):
                    slots[i] = local[i % len(local)]
        if commons:
            queries = scene_search_queries(topic, sentences, scene_queries)
            missing = [i for i, item in enumerate(slots) if item is None]
            remote, notices = commons_scene_assets(queries, work, indices=missing)
            for i, item in remote.items():
                slots[i] = item
            warnings.extend(notices)
        using_media = any(item is not None for item in slots)
        # Composite images and abstract fallbacks in timeline order to prevent
        # a single lost Commons result from failing or mislabelling a scene.
        video_background = (prepare_media_background(slots, durations, work, width=width,
                            height=height, fps=fps) if using_media else None)
        filters = ["format=yuv420p"]
        if using_media:
            # Preserve the photo's details while improving caption legibility.
            filters.append("drawbox=x=0:y=0:w=iw:h=ih:color=0x06101e@0.30:t=fill")
        else:
            # Deterministic fallback if no stock or user media can be used.
            for i, segment in enumerate(segments):
                start, end = segment["start"], segment["end"]
                color = PALETTE[i % len(PALETTE)]
                filters.append(f"drawbox=x=0:y=0:w=iw:h=ih:color={color}:t=fill:enable='between(t,{start},{end})'")
        filters += [
            "drawgrid=w=110:h=110:t=1:color=white@0.025",
            "drawbox=x=0:y=ih*0.68:w=iw:h=ih*0.23:color=0x040915@0.20:t=fill",
            f"drawbox=x=0:y=0:w=iw:h={max(5,height//115)}:color=0x42e8d6:t=fill",
            f"drawbox=x={int(width*.09)}:y={int(height*.285)}:w={max(5,width//70)}:h={int(height*.19)}:color=0x42e8d6:t=fill",
        ]
        (work / "brand.txt").write_text("MONEYPRINTER  /  FREE STUDIO", encoding="utf-8")
        filters.append(f"drawtext=textfile=brand.txt:fontcolor=0x97cfcf:fontsize={max(10,width//45)}:x=(w-text_w)/2:y=h*0.12")
        for i, segment in enumerate(segments):
            start, end = segment["start"], segment["end"]
            heading = " ".join(str(segment["text"]).rstrip(".!?…").split()[:8])
            heading_size = max(17, width // 20)
            max_chars = max(11, int(width * 0.74 / (heading_size * 0.60)))
            lines = textwrap.wrap(heading, width=max_chars, break_long_words=True, break_on_hyphens=False)[:4]
            (work / f"heading_{i}.txt").write_text("\n".join(lines), encoding="utf-8")
            filters.append(
                f"drawtext=textfile=heading_{i}.txt:fontcolor=white:fontsize={heading_size}:line_spacing={max(5,heading_size//3)}:"
                f"x=(w-text_w)/2:y=h*0.38:enable='between(t,{start},{end})'"
            )
        subtitle_size = max(13, int(width / 34))
        filters.append("subtitles=captions.srt:force_style='FontName=DejaVu Sans,FontSize=" + str(subtitle_size) + ",PrimaryColour=&H00FFFFFF,OutlineColour=&H00201712,Outline=2,Shadow=0,Alignment=2,MarginV=" + str(max(35, height//8)) + "'")
        staged = work / "result.mp4"
        if using_media:
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video_background)]
        else:
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                   f"color=c=0x0b1025:s={width}x{height}:r={fps}:d={total:.3f}"]
        if audio:
            cmd += ["-i", str(audio)]
        cmd += ["-vf", ",".join(filters), "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "23", "-pix_fmt", "yuv420p"]
        if audio:
            cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "128k"]
        else:
            cmd += ["-an"]
        cmd += ["-movflags", "+faststart", "-t", f"{total:.3f}", str(staged)]
        execute(cmd, cwd=work)
        if not staged.is_file() or staged.stat().st_size < 1000:
            raise RuntimeError("Video output missing or truncated")
        # Only publish the finished result; preexisting output remains untouched on failure.
        temp_out = output.with_name(output.stem + ".partial.mp4")
        try:
            shutil.copyfile(staged, temp_out)
            os.replace(temp_out, output)
        finally:
            temp_out.unlink(missing_ok=True)
        # Deliver editable captions next to the actual video output.
        shutil.copyfile(work / "captions.srt", output.with_suffix(".srt"))
        media_used: list[dict[str, object]] = []
        for index, asset in enumerate(slots):
            if asset is None:
                continue
            media_used.append({"segment": index + 1, "title": asset.title, "kind": asset.kind,
                               "source": asset.source_url, "license": asset.license,
                               "author": asset.author,
                               "search_query": queries[index] if queries else None})
        credits_path = output.with_suffix(".credits.txt")
        if media_used:
            credits = ["MEDIA SOURCES — MoneyPrinterTurbo Perso", "Local media: users must hold appropriate rights.", ""]
            credits.extend(f"Scene {item['segment']}: {item['title']} | Author: {item['author']} | "
                           f"License: {item['license']} | {item['source']}" for item in media_used)
            credits_path.write_text("\n".join(credits) + "\n", encoding="utf-8")
        else:
            credits_path.unlink(missing_ok=True)
        info: dict[str, object] = {
            "output": str(output), "duration_seconds": round(total, 3), "size": [width, height],
            "segments": segments, "speech": not mute, "source": "local-free-studio",
            "speech_engine": tts if not mute else "muted",
            "visual_mode": ("media" if all(slots) else "mixed-media" if using_media else "abstract-fallback"),
            "scene_search_queries": queries, "media": media_used, "warnings": warnings,
        }
        output.with_suffix(".json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return info


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fully free short video rendering with local FFmpeg/eSpeak; optional Ollama for AI scripts.")
    parser.add_argument("--topic", required=True, help="Subject of the video")
    parser.add_argument("--script", help="Custom narrated script text")
    parser.add_argument("--script-file", type=Path, help="Read custom narrated script from UTF-8 file")
    parser.add_argument("--ollama-model", help="Optional local AI model, e.g. llama3.2:1b")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--output", type=Path, default=Path("outputs/video.mp4"))
    parser.add_argument("--lang", choices=sorted(LANG_VOICES), default="fr")
    parser.add_argument("--aspect", choices=sorted(ASPECTS), default="9:16")
    parser.add_argument("--resolution", type=int, choices=[360, 720, 1080], default=720)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--mute", action="store_true", help="Render without synthetic voice")
    parser.add_argument("--media-dir", type=Path, help="Directory of licensed local images or videos")
    parser.add_argument("--commons", action="store_true", help="Search Wikimedia Commons per scene for CC0/public-domain photos")
    parser.add_argument("--scene-queries", help="Optional pipe-separated search words, one per narration segment")
    parser.add_argument("--tts", choices=["espeak", "piper"], default="espeak", help="Speech engine; Piper requires a preinstalled local voice")
    parser.add_argument("--piper-model", type=Path, help="Offline Piper voice .onnx path (matching .onnx.json required)")
    parser.add_argument("--speech-rate", type=int, default=145, help="eSpeak speaking speed in words per minute (80–220)")
    args = parser.parse_args(argv)
    if args.script and args.script_file:
        parser.error("Use either --script or --script-file")
    if (args.script or args.script_file) and args.ollama_model:
        parser.error("Do not combine a provided script with an AI script")
    try:
        if args.script_file:
            text = args.script_file.read_text(encoding="utf-8")
            mode = "user-script"
        elif args.script:
            text = args.script
            mode = "user-script"
        elif args.ollama_model:
            text = ollama_script(args.topic, args.lang, args.ollama_model, args.ollama_url)
            mode = "local-ollama-ai"
        else:
            text = template_script(args.topic, args.lang)
            mode = "deterministic-template-not-ai"
        print(f"Script mode: {mode}", flush=True)
        info = render(args.topic, text, args.output, lang=args.lang, aspect=args.aspect,
                      resolution=args.resolution, fps=args.fps, mute=args.mute,
                      media_dir=args.media_dir, commons=args.commons,
                      scene_queries=args.scene_queries.split("|") if args.scene_queries is not None else None,
                      tts=args.tts, piper_model=args.piper_model, speech_rate=args.speech_rate)
        info["script_mode"] = mode
        Path(info["output"]).with_suffix(".json").write_text(
            json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(info, ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Generation failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
