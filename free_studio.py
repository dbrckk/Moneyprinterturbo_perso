#!/usr/bin/env python3
"""Free, reproducible short-video generator: Python stdlib + FFmpeg + eSpeak.

Optional local Ollama provides *real* AI scripting. Without it, a clearly
identified deterministic outline is used so video rendering still works offline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
import time
import wave
from urllib import request

from media_assets import MediaAsset, commons_scene_assets, local_assets, scene_search_queries
from cinematic import build_filters, gradient_source, transition_plan, xfade_transition_spec
from xfade_final import final_xfade_command
from fast_abstract import build_onepass_abstract_command
from fast_media import build_onepass_media_command
from scene_framing import needs_soft_matte, soft_matte_graph
from caption_motion import generate_karaoke_ass
from forced_alignment import align_narration
from publish_bundle import publish_bundle
from tts_batch import synthesize_piper_batch, wav_duration_seconds
from video_timeline import plan_local_video_timeline, source_seek_seconds
from shot_quality import probe_black_intro
from shot_cuts import probe_shot_profile
from exposure_match import balance_exposure, exposure_filter
from white_balance import balance_white, white_balance_suffix
from smart_transitions import choose_boundaries
from final_scene_review import inspect_final_boundaries
from visual_repair import encoded_duration_ok, improved_review, repairable_boundary

PALETTE = ("0x0b1025", "0x153653", "0x23315f", "0x234747", "0x45315c", "0x16425b")
DEFAULT_MODELS = ("llama3.2:1b", "qwen2.5:1.5b", "gemma3:1b")
ASPECTS = {"9:16": (9, 16), "16:9": (16, 9), "1:1": (1, 1)}
LANG_VOICES = {"fr": "fr", "en": "en", "es": "es", "de": "de", "it": "it", "pt": "pt"}
# Voice provenance is only claimed when model bytes match the reviewed upstream file.
# Target for consistent speech intelligibility on mobile (not a measured LUFS guarantee).
AUDIO_MASTER_FILTER = "loudnorm=I=-16:TP=-1.5:LRA=11"
SIWIS_MEDIUM_SHA256 = "641d1ab097da2b81128c076810edb052b385decc8be3381814802a64a73baf99"
SIWIS_MEDIUM_CARD = ("https://huggingface.co/rhasspy/piper-voices/blob/"
    "d5de91c972ba5a13cea0e77a63fda1623faac2c1/fr/fr_FR/siwis/medium/MODEL_CARD")


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


def _silent_pcm_wav(path: Path) -> bool:
    """Return True only for a fully silent PCM WAV (including 8-bit unsigned).

    Fake voice shims in tests legitimately produce silence; FFmpeg loudnorm can
    emit NaN/-Inf on those files. Do not change normal non-silent speech audio.
    """
    try:
        with wave.open(str(path), "rb") as source:
            width = source.getsampwidth()
            if width not in (1, 2, 3, 4):
                return False
            expected = 128 if width == 1 else 0
            while True:
                block = source.readframes(32768)
                if not block:
                    return True
                if any(value != expected for value in block):
                    return False
    except (OSError, ValueError, wave.Error, EOFError):
        return False


def make_audio(segments: list[str], lang: str, work: Path, *, mute: bool,
               tts: str = "espeak", piper_model: Path | None = None,
               speech_rate: int = 145, diagnostics: dict[str, str] | None = None) -> tuple[list[float], Path | None]:
    if mute:
        if diagnostics is not None:
            diagnostics["tts_backend"] = "muted"
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
    # Loading an ONNX voice for each scene is wasteful. The Python API can
    # retain it for the full storyboard; the legacy CLI remains supported.
    batch = (synthesize_piper_batch(segments, piper_model, work) if tts == "piper" else False)
    if diagnostics is not None:
        diagnostics["tts_backend"] = ("piper-python-batch" if batch else
                                       "piper-cli-legacy" if tts == "piper" else "espeak-cli")
    for idx, sentence in enumerate(segments):
        audio_path = work / f"voice_{idx:02}.wav"
        if tts == "piper" and not batch:
            # Piper runs locally using a voice already installed by the user.
            # No implicit model download or API request is permitted here.
            process = subprocess.run(
                ["piper", "--model", str(piper_model), "--output_file", str(audio_path)],
                input=sentence + "\n", text=True, capture_output=True, timeout=150,
            )
            if process.returncode:
                raise RuntimeError(f"Piper failed: {process.stderr[-1200:]}")
        elif tts == "espeak":
            execute(["espeak", "-v", LANG_VOICES[lang], "-s", str(speech_rate),
                     "-w", str(audio_path), sentence], timeout=35)
        try:
            durations.append(wav_duration_seconds(audio_path))
        except (wave.Error, EOFError):
            # Preserve compatibility with codecs outside PCM WAV.
            durations.append(duration_seconds(audio_path))
        filenames.append(audio_path)
    manifest = work / "audio_list.txt"
    manifest.write_text("".join(f"file '{path.name}'\n" for path in filenames), encoding="utf-8")
    audio = work / "narration.m4a"
    # Master after concatenating the whole narration, not each sentence.
    # Silence-only WAV shims cannot be normalized (loudnorm may emit NaN),
    # but still need a valid AAC stream for deterministic compatibility tests.
    skipped_mastering = all(_silent_pcm_wav(path) for path in filenames)
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-f", "concat", "-safe", "0", "-i", manifest.name]
    if not skipped_mastering:
        command.extend(["-af", AUDIO_MASTER_FILTER])
    command.extend(["-ar", "48000", "-c:a", "aac", "-b:a", "160k", audio.name])
    execute(command, cwd=work)
    if diagnostics is not None:
        diagnostics["audio_mastering"] = ("skipped-silent-input" if skipped_mastering
                                           else "ffmpeg-loudnorm-onepass")
        diagnostics["audio_delivery"] = "aac-stream-copy"
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
                 *, width: int, height: int, fps: int,
                 extra_frames: int = 0) -> Path:
    """Normalize one photo/video to a silent CFR clip with a gentle camera move."""
    target = work / f"scene_{index:02}.mp4"
    frames = max(1, round(duration * fps) + extra_frames)
    if asset is None:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
               "-i", gradient_source(index, width, height, fps)]
        effect = "format=yuv420p"
    elif asset.kind == "image":
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-loop", "1",
               "-framerate", str(fps), "-i", str(asset.file)]
        effect = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                  f"crop={width}:{height},setsar=1,"
                  f"zoompan=z='min(zoom+0.0008,1.10)':d=1:s={width}x{height}:fps={fps},"
                  "eq=contrast=1.05:saturation=1.07,"
                  "scale=in_range=auto:out_range=tv,format=yuv420p")
    elif asset.kind == "video":
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-stream_loop", "-1"]
        seek = source_seek_seconds(asset)
        if seek:
            cmd.extend(["-ss", f"{seek:.3f}"])
        cmd.extend(["-i", str(asset.file)])
        effect = (f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=increase,"
                  f"crop={width}:{height},setsar=1,"
                  f"{exposure_filter(asset)}{white_balance_suffix(asset)},format=yuv420p")
    else:
        raise ValueError("Unsupported local media kind")
    if needs_soft_matte(asset):
        graph = soft_matte_graph(0, width=width, height=height, fps=fps,
                                 prefix=f"legacy{index}", output_name="framed")
        cmd += ["-an", "-filter_complex_threads", "1",
                "-filter_complex", ";".join(graph), "-map", "[framed]"]
    else:
        cmd += ["-an", "-vf", effect]
    cmd += ["-frames:v", str(frames), "-r", str(fps),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            str(target)]
    execute(cmd, timeout=240)
    if target.stat().st_size < 1000:
        raise RuntimeError("Media scene output is empty")
    return target


def prepare_media_background(assets: list[MediaAsset | None], durations: list[float], work: Path,
                             *, width: int, height: int, fps: int,
                             transition: str = "cut",
                             boundary_transitions: tuple[str, ...] | None = None) -> Path:
    """Compose CFR media clips, with optionally frame-accurate crossfades.

    Each crossfade is centered on the original narration/SRT boundary. The
    incoming and outgoing clips receive half a transition's worth of extra
    frames; the overlap then removes those exact frames, so runtime is not
    shortened and subtitle/voice synchronization is preserved.
    """
    if len(assets) != len(durations) or not assets:
        raise ValueError("Each scene must have exactly one media slot")
    plan = transition_plan(durations, fps=fps, transition=transition)
    if boundary_transitions is not None and (
            len(boundary_transitions) != len(assets) - 1 or
            any(name not in ("cut", "fade") for name in boundary_transitions)):
        raise ValueError("Invalid scene transition decisions")
    scenes = [
        render_scene(asset, duration, i, work, width=width, height=height, fps=fps,
                     extra_frames=plan.extra_frames[i])
        for i, (asset, duration) in enumerate(zip(assets, plan.durations))
    ]
    output = work / "background.mp4"
    if len(scenes) == 1:
        # Avoid a redundant FFmpeg remux for a one-scene video.
        shutil.copyfile(scenes[0], output)
        return output
    if plan.overlap_frames == 0:
        manifest = work / "scenes.txt"
        manifest.write_text("".join(f"file '{scene.name}'\n" for scene in scenes), encoding="utf-8")
        execute(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
                 "-safe", "0", "-i", manifest.name, "-c", "copy", output.name], cwd=work)
        return output
    filters: list[str] = []
    previous = "[0:v]"
    for i in range(1, len(scenes)):
        current = f"[xf{i}]"
        chosen = (boundary_transitions[i-1] if boundary_transitions is not None
                  else transition)
        filters.append(
            f"{previous}[{i}:v]xfade={xfade_transition_spec(chosen)}:"
            f"duration={plan.overlap_frames/fps:.6f}:"
            f"offset={plan.offset_frames[i-1]/fps:.6f}{current}"
        )
        previous = current
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-filter_complex_threads", "1"]
    for scene in scenes:
        cmd.extend(["-i", str(scene)])
    cmd += ["-filter_complex", ";".join(filters), "-map", previous,
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-frames:v", str(plan.total_frames), str(output)]
    execute(cmd, timeout=300)
    if not output.exists() or output.stat().st_size < 1000:
        raise RuntimeError("No valid transition output")
    return output


def render(topic: str, script: str, output: Path, *, lang: str = "fr", aspect: str = "9:16",
           resolution: int = 720, mute: bool = False, fps: int = 24,
           media_dir: Path | None = None, commons: bool = False,
           scene_queries: list[str] | None = None, tts: str = "espeak",
           piper_model: Path | None = None, speech_rate: int = 145,
           visual_style: str = "cinematic", transition: str = "auto",
           caption_style: str = "kinetic", word_alignment: str = "estimated",
           use_fused_xfade: bool = True, use_media_onepass: bool = True,
           visual_ai: str = "off", script_mode: str | None = None) -> dict[str, object]:
    check_program("ffmpeg")
    check_program("ffprobe")
    if not (1 <= fps <= 60):
        raise ValueError("Frame rate must be 1–60")
    if script_mode not in (None, "user-script", "local-ollama-ai", "deterministic-template-not-ai"):
        raise ValueError("Unsupported scripting provenance")
    if visual_style not in ("cinematic", "classic"):
        raise ValueError("Unsupported visual style")
    if caption_style not in ("kinetic", "classic"):
        raise ValueError("Unsupported caption style")
    if word_alignment not in ("estimated", "forced") or (word_alignment == "forced" and (caption_style != "kinetic" or mute)):
        raise ValueError("Forced word alignment requires spoken kinetic captions")
    if transition not in ("auto", "cut", "fade", "wipeleft"):
        raise ValueError("Unsupported scene transition")
    applied_transition = ("fade" if visual_style == "cinematic" else "cut") if transition == "auto" else transition
    if aspect not in ASPECTS or resolution not in (360, 720, 1080) or lang not in LANG_VOICES:
        raise ValueError("Unsupported aspect, resolution or voice language")
    sentences = split_script(script)
    if scene_queries is not None and not commons:
        raise ValueError("Scene search queries require --commons")
    if visual_ai not in ("off", "clip") or (visual_ai == "clip" and not commons):
        raise ValueError("CLIP visual comparison requires --commons")
    if tts not in ("espeak", "piper") or (tts == "espeak" and piper_model is not None):
        raise ValueError("Invalid voice engine or model combination")
    if tts == "piper" and piper_model is None and not mute:
        raise ValueError("Piper voice requires a local model")
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    width, height = size_for(aspect, resolution)
    render_started = time.perf_counter()
    metrics: dict[str, float | str] = {}
    diagnostics: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="mpt-free-") as tmp:
        work = Path(tmp)
        stage_started = time.perf_counter()
        durations, audio = make_audio(sentences, lang, work, mute=mute,
                                      tts=tts, piper_model=piper_model,
                                      speech_rate=speech_rate, diagnostics=diagnostics)
        metrics["audio_seconds"] = round(time.perf_counter() - stage_started, 3)
        segments = make_captions(sentences, durations, work)
        warnings: list[str] = []
        if diagnostics.get("audio_mastering") == "skipped-silent-input":
            warnings.append("Narration contains no audible samples; mastering skipped. Verify TTS output.")
        word_times = None
        if word_alignment == "forced":
            if audio is None:
                raise ValueError("Acoustic word alignment requires a narration audio track")
            word_times, notices = align_narration(audio, segments, language=lang)
            warnings.extend(notices)
        caption_meta = (generate_karaoke_ass(segments, work, width=width, height=height,
                          word_times=word_times) if caption_style == "kinetic" else None)
        total = sum(durations)
        stage_started = time.perf_counter()
        slots: list[MediaAsset | None] = [None] * len(sentences)
        queries: list[str] | None = None
        if media_dir is not None:
            local, notices = local_assets(media_dir.expanduser().resolve())
            warnings.extend(notices)
            for i in range(len(sentences)):
                if local and (not commons or i < len(local)):
                    slots[i] = local[i % len(local)]
        commons_stats: dict[str, float | int] = {}
        if commons:
            queries = scene_search_queries(topic, sentences, scene_queries)
            missing = [i for i, item in enumerate(slots) if item is None]
            remote, notices = commons_scene_assets(
                queries, work, indices=missing,
                target_aspect=width/height, stats=commons_stats,
                **({"semantic_ranking": True} if visual_ai == "clip" else {}))
            for i, item in remote.items():
                slots[i] = item
            warnings.extend(notices)
        # Reused local videos advance through their actual playback timeline.
        # Commons stills and unrelated local images are left untouched.
        slots, clip_notices = plan_local_video_timeline(
            slots, durations,
            probe_intro=probe_black_intro if media_dir is not None else None,
            probe_profile=probe_shot_profile if media_dir is not None else None)
        warnings.extend(clip_notices)
        if media_dir is not None:
            slots, exposure_notices = balance_exposure(slots, durations)
            warnings.extend(exposure_notices)
            slots, color_notices = balance_white(slots, durations)
            warnings.extend(color_notices)
        using_media = any(item is not None for item in slots)
        boundary_transitions: tuple[str, ...] | None = None
        if transition == "auto" and applied_transition == "fade" and using_media:
            boundary_transitions, adaptive_notes = choose_boundaries(slots, durations)
            warnings.extend(adaptive_notes)
        metrics["media_selection_seconds"] = round(time.perf_counter() - stage_started, 3)
        if commons:
            metrics["commons_wall_seconds"] = float(commons_stats.get("wall_seconds", 0))
            metrics["commons_api_seconds_sum"] = float(commons_stats.get("api_seconds_sum", 0))
            metrics["commons_download_seconds_sum"] = float(commons_stats.get("download_seconds_sum", 0))
        stage_started = time.perf_counter()
        # For small graphic-only storyboards, generate all moving backgrounds,
        # crossfades, text and subtitles in the FINAL FFmpeg graph. No temporary
        # H.264 scene clips are needed. More complex media retains tested paths.
        plan = transition_plan(durations, fps=fps, transition=applied_transition)
        if boundary_transitions is not None and plan.overlap_frames == 0:
            boundary_transitions = tuple("cut" for _ in range(len(slots) - 1))
        direct_abstract = bool(use_fused_xfade and not using_media and len(slots) <= 6)
        # Limit memory for simultaneous decoders, filters and animated images.
        # Two-source 1080p and up-to-four-source 720p media are validated.
        direct_media = bool(
            use_fused_xfade and use_media_onepass and using_media and
            ((resolution <= 720 and len(slots) <= 4) or
             (resolution == 1080 and len(slots) <= 2)))
        fused = bool(not (direct_abstract or direct_media) and plan.overlap_frames > 0
                     and use_fused_xfade)
        if fused:
            clips = [render_scene(asset, length, i, work, width=width,
                         height=height, fps=fps, extra_frames=plan.extra_frames[i])
                     for i, (asset, length) in enumerate(zip(slots, plan.durations, strict=True))]
        elif not (direct_abstract or direct_media):
            video_background = prepare_media_background(slots, durations, work,
                width=width, height=height, fps=fps,
                transition=applied_transition,
                boundary_transitions=boundary_transitions)
        metrics["scene_encoding_seconds"] = round(time.perf_counter() - stage_started, 3)
        stage_started = time.perf_counter()
        filters = build_filters(segments, slots, work, width=width,
                                height=height, total=total, style=visual_style)
        subtitle_size = max(13, int(width / 34))
        if caption_meta is not None:
            filters.append("ass=captions.ass")
        else:
            filters.append("subtitles=captions.srt:force_style='FontName=DejaVu Sans,FontSize=" + str(subtitle_size) + ",PrimaryColour=&H00FFFFFF,OutlineColour=&H00201712,Outline=2,Shadow=0,Alignment=2,MarginV=" + str(max(35, height//8)) + "'")
        staged = work / "result.mp4"
        if direct_abstract:
            # Abstract animated backgrounds are not source local footage.
            # Their renderer has no adaptive scene boundaries.
            cmd = build_onepass_abstract_command(
                durations, width=width, height=height, fps=fps,
                transition=applied_transition, visual_filters=filters,
                audio=audio, output=staged, total=total)
        elif direct_media:
            cmd = build_onepass_media_command(
                slots, durations, width=width, height=height, fps=fps,
                transition=applied_transition, visual_filters=filters,
                audio=audio, output=staged, total=total,
                boundary_transitions=boundary_transitions)
        elif fused:
            cmd = final_xfade_command(clips, plan, fps=fps,
                transition=applied_transition, overlays=filters, audio=audio,
                duration=total, output=staged, boundary_transitions=boundary_transitions)
        else:
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video_background)]
            if audio:
                cmd += ["-i", str(audio)]
            cmd += ["-vf", ",".join(filters), "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "23", "-pix_fmt", "yuv420p"]
            if audio:
                cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "copy"]
            else:
                cmd += ["-an"]
            cmd += ["-movflags", "+faststart", "-t", f"{total:.3f}", str(staged)]
        fallback = False
        try:
            execute(cmd, cwd=work, timeout=360)
        except (RuntimeError, OSError, subprocess.SubprocessError):
            if not (direct_abstract or direct_media):
                raise
            # Unsupported FFmpeg input combinations retain the established
            # media path. Keep the actual strategy visible in the report.
            fallback = True
            warnings.append("Single-pass composition failed; using legacy FFmpeg rendering")
            retry_started = time.perf_counter()
            video_background = prepare_media_background(slots, durations, work,
                width=width, height=height, fps=fps,
                transition=applied_transition,
                boundary_transitions=boundary_transitions)
            metrics["scene_encoding_seconds"] = round(time.perf_counter() - retry_started, 3)
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video_background)]
            if audio:
                cmd += ["-i", str(audio)]
            cmd += ["-vf", ",".join(filters), "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "23", "-pix_fmt", "yuv420p"]
            if audio:
                cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "copy"]
            else:
                cmd += ["-an"]
            cmd += ["-movflags", "+faststart", "-t", f"{total:.3f}", str(staged)]
            execute(cmd, cwd=work, timeout=360)
        metrics["compositing_pipeline"] = ("legacy-media-onepass-fallback" if fallback and direct_media else
                                           "legacy-single-pass-fallback" if fallback else
                                           "direct-abstract-onepass" if direct_abstract else
                                           "direct-media-onepass" if direct_media else
                                           "fused-xfade" if fused else
                                           "legacy-xfade" if plan.overlap_frames else
                                           "cut-concat")
        metrics["final_encoding_seconds"] = round(time.perf_counter() - stage_started, 3)
        if not staged.is_file() or staged.stat().st_size < 1000:
            raise RuntimeError("Video output missing or truncated")
        # Inspect actual encoded scene boundaries, not only source previews.
        # Restrict this optional on-device work to multi-scene local footage.
        # No visual heuristic can reject an otherwise valid MP4.
        rendered_visual_review = None
        if media_dir is not None and len(slots) > 1 and any(
                item is not None and item.source_url == "local"
                and item.kind == "video" for item in slots):
            review_start = time.perf_counter()
            try:
                chosen_transitions = (list(boundary_transitions) if
                    boundary_transitions is not None else
                    [applied_transition if plan.overlap_frames else "cut"]
                    * (len(slots) - 1))
                rendered_visual_review = inspect_final_boundaries(
                    staged, segments, chosen_transitions)
                # A post-encode isolated flash on an automatically chosen
                # local-video cut is a *candidate* for one safer fade.
                # Preserve the first MP4 unless a second actual encode and
                # a complete re-scan both demonstrably improve the result.
                if (transition == "auto" and applied_transition == "fade"
                        and direct_media and not fallback
                        and boundary_transitions is not None
                        and plan.overlap_frames > 0 and fps <= 30
                        and resolution <= 720 and total <= 30):
                    repair_index = repairable_boundary(
                        rendered_visual_review, boundary_transitions, slots)
                    if repair_index is not None:
                        repair_start = time.perf_counter()
                        alternative = list(boundary_transitions)
                        alternative[repair_index] = "fade"
                        alternative_modes = tuple(alternative)
                        candidate_file = work / "candidate-transition-repair.mp4"
                        try:
                            retry = build_onepass_media_command(
                                slots, durations, width=width, height=height,
                                fps=fps, transition=applied_transition,
                                visual_filters=filters, audio=audio,
                                output=candidate_file, total=total,
                                boundary_transitions=alternative_modes)
                            execute(retry, cwd=work, timeout=180)
                            candidate_review = inspect_final_boundaries(
                                candidate_file, segments, alternative_modes)
                            if (improved_review(rendered_visual_review, candidate_review,
                                                repair_index)
                                    and encoded_duration_ok(
                                        candidate_file, expected=total, fps=fps)):
                                staged = candidate_file
                                boundary_transitions = alternative_modes
                                rendered_visual_review = candidate_review
                                metrics["postencode_transition_repair"] = "accepted"
                                warnings.append(
                                    f"Boundary {repair_index + 1}: auto-cut flash "
                                    "repaired by a verified fade")
                            else:
                                metrics["postencode_transition_repair"] = "rejected"
                                warnings.append(
                                    "Automatic transition repair did not improve "
                                    "the verified MP4; original video retained")
                        except (OSError, ValueError, TypeError, RuntimeError,
                                subprocess.SubprocessError):
                            metrics["postencode_transition_repair"] = "failed"
                            warnings.append(
                                "Automatic transition repair unavailable; "
                                "original MP4 retained")
                        metrics["transition_repair_seconds"] = round(
                            time.perf_counter() - repair_start, 3)
                warnings.extend(rendered_visual_review["warnings"])
            except (OSError, ValueError, TypeError, RuntimeError,
                    subprocess.SubprocessError):
                warnings.append("Final encoded visual review unavailable; MP4 kept unchanged")
            metrics["final_visual_review_seconds"] = round(
                time.perf_counter() - review_start, 3)
        # Prepare every final artifact before changing anything in the public
        # output folder. Failures must not leave a new MP4 with old subtitles
        # or credits from a different render.
        publications: dict[Path, Path] = {
            output: staged, output.with_suffix(".srt"): work / "captions.srt",
        }
        obsolete: list[Path] = [
            output.with_suffix(".quality.json"),
            output.with_suffix(".storyboard.png"),
        ]
        if caption_meta is not None:
            publications[output.with_suffix(".ass")] = work / "captions.ass"
            if caption_meta["estimated_word_timing"]:
                warnings.append("Some word timings are estimated, not measured acoustically")
            alignment_file = work / "alignment-metadata.json"
            alignment_file.write_text(json.dumps({
                "requested": word_alignment, "method": caption_meta["timing_method"],
                "aligned_scenes": caption_meta["aligned_scenes"],
                "scene_count": caption_meta["scene_count"],
                "phrases": caption_meta["phrases"], "word_timings": word_times,
                "warnings": warnings,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            publications[output.with_suffix(".alignment.json")] = alignment_file
        else:
            obsolete.extend([output.with_suffix(".ass"),
                             output.with_suffix(".alignment.json")])
        media_used: list[dict[str, object]] = []
        for index, asset in enumerate(slots):
            if asset is None:
                continue
            media_used.append({"segment": index + 1, "title": asset.title, "kind": asset.kind,
                               "source": asset.source_url, "license": asset.license,
                               "author": asset.author, "visual_medium": asset.visual_medium,
                               "crop_retention_estimate": asset.crop_retention_estimate,
                               "query_term_count": asset.query_term_count,
                               "query_term_coverage": asset.query_term_coverage,
                               "semantic_score": asset.semantic_score,
                               "source_start_seconds": (
                                   round(source_seek_seconds(asset), 3) if asset.kind == "video" else None),
                               "exposure_shift": (asset.exposure_shift if asset.kind == "video" else None),
                               "white_balance_gains": (asset.white_balance_gains if asset.kind == "video"
                                                       else None),
                               "render_framing": ("soft-matte-full-image" if needs_soft_matte(asset)
                                                  else "cover-center-crop"),
                               "search_query": queries[index] if queries else None})
        credits_path = work / "prepared-credits.txt"
        voice_info = None
        credits = ["ASSET & VOICE CREDITS — MoneyPrinterTurbo Perso", ""]
        if audio is not None and tts == "piper" and piper_model is not None:
            # Never assign a known CC BY license to an unverified, user-supplied model.
            model = piper_model.expanduser().resolve()
            with model.open("rb") as model_file:
                digest = hashlib.file_digest(model_file, "sha256").hexdigest()
            known = model.name == "fr_FR-siwis-medium.onnx" and digest == SIWIS_MEDIUM_SHA256
            voice_info = {"model": model.name, "sha256": digest, "license_verified": known}
            if known:
                credits.append("Voice: Piper / SIWIS French medium (model and dataset SIWIS, CC BY 4.0).")
                credits.append("Attribution: SIWIS speech dataset (University of Edinburgh).")
                credits.append("License: https://creativecommons.org/licenses/by/4.0/")
                credits.append("Source and provenance: " + SIWIS_MEDIUM_CARD)
            else:
                credits.append("Voice: Piper / " + model.name + " (model rights not automatically verified).")
                credits.append("Verify and credit your own voice model before publication.")
            credits.append("")
        if media_used:
            credits.append("MEDIA SOURCES: User-provided media must have appropriate rights.")
            credits.extend(f"Scene {item['segment']}: {item['title']} | Author: {item['author']} | "
                           f"License: {item['license']} | "
                           f"Visual medium hint: {item['visual_medium']} | {item['source']}"
                           for item in media_used)
        # Always ship provenance, even for original gradients and eSpeak:
        # Android publishers should never have to guess where visuals/voice came from.
        if not media_used:
            credits.append("Visuals: Original animated graphic backgrounds (no Commons or third-party media selected).")
        if audio is None:
            credits.append("Voice: None (muted output).")
        elif tts == "espeak":
            credits.append("Voice: eSpeak synthetic speech produced locally; review applicable voice rights before publication.")
        credits_path.write_text("\n".join(credits) + "\n", encoding="utf-8")
        metrics["total_seconds"] = round(time.perf_counter() - render_started, 3)
        info: dict[str, object] = {
            "output": str(output), "script_mode": script_mode or "provided-script",
            "duration_seconds": round(total, 3), "size": [width, height],
            "segments": segments, "speech": not mute, "source": "local-free-studio",
            "tts_backend": diagnostics.get("tts_backend"), "render_performance": metrics,
            "audio_mastering": ({"method": diagnostics.get("audio_mastering"),
                                  "target_integrated_lufs": (
                                      -16 if diagnostics.get("audio_mastering") == "ffmpeg-loudnorm-onepass"
                                      else None),
                                  "target_true_peak_dbfs": (
                                      -1.5 if diagnostics.get("audio_mastering") == "ffmpeg-loudnorm-onepass"
                                      else None),
                                  "audio_encoding": "aac-160k-48kHz-stream-copy"}
                                 if audio else None),
            "commons_fetch": commons_stats if commons else None,
            "visual_ai": visual_ai,
            "speech_engine": tts if not mute else "muted", "visual_style": visual_style, "scene_transition": applied_transition,
            "scene_transitions": list(boundary_transitions) if boundary_transitions is not None else
                                 [applied_transition if plan.overlap_frames else "cut"] * (len(slots) - 1),
            "voice_model": voice_info,
            "caption_style": caption_style, "caption_timing": (
                "forced-audio-word-onsets" if caption_meta and not caption_meta["estimated_word_timing"] else
                "mixed-measured-estimated" if caption_meta and caption_meta["aligned_scenes"] else
                "estimated-per-word" if caption_meta else "scene-level"),
            "caption_phrases": len(caption_meta["phrases"]) if caption_meta else len(segments),
            "visual_mode": ("media" if all(slots) else "mixed-media" if using_media else "abstract-fallback"),
            "scene_search_queries": queries, "media": media_used,
            "final_visual_review": rendered_visual_review, "warnings": warnings,
        }
        metadata_file = work / "prepared-manifest.json"
        metadata_file.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
        publications[output.with_suffix(".credits.txt")] = credits_path
        publications[output.with_suffix(".json")] = metadata_file
        # The caller's previous complete export survives a copy or rename
        # failure. On success, invalidate reports tied to the former video.
        publish_bundle(publications, remove=obsolete)
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
    parser.add_argument("--transition", choices=["auto", "cut", "fade", "wipeleft"], default="auto",
                        help="Scene transition; auto chooses cut/fade for suitable local video scenes, otherwise preserves original style")
    parser.add_argument("--word-alignment", choices=["estimated", "forced"], default="estimated",
                        help="Optional true acoustic word alignment using CPU stable-ts/Whisper")
    parser.add_argument("--caption-style", choices=["kinetic", "classic"], default="kinetic",
                        help="Estimated word-progress captions or traditional sentence subtitles")
    parser.add_argument("--visual-style", choices=["cinematic", "classic"],
                        default="cinematic", help="Animated cinematic look or classic titles")
    parser.add_argument("--mute", action="store_true", help="Render without synthetic voice")
    parser.add_argument("--media-dir", type=Path, help="Directory of licensed local images or videos")
    parser.add_argument("--commons", action="store_true", help="Search Wikimedia Commons per scene for CC0/public-domain photos")
    parser.add_argument("--visual-ai", choices=["off", "clip"], default="off",
                        help="Optional CPU-only quantized CLIP comparison of licensed Commons thumbnails")
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
                      media_dir=args.media_dir, commons=args.commons, visual_ai=args.visual_ai,
                      scene_queries=args.scene_queries.split("|") if args.scene_queries is not None else None,
                      tts=args.tts, piper_model=args.piper_model, speech_rate=args.speech_rate,
                      visual_style=args.visual_style, transition=args.transition,
                      caption_style=args.caption_style, word_alignment=args.word_alignment,
                      script_mode=mode)
        # The manifest was staged and published alongside the MP4 by render().
        # Do not perform a second unguarded post-publication write.
        print(json.dumps(info, ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Generation failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
