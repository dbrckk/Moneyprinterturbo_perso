"""Optional CPU acoustic word alignment of the *known* narrated script.

Uses stable-ts/Whisper only when explicitly requested. No external inference API,
paid key, or replacement transcript. Questionable scenes use an audited fallback.
"""
from __future__ import annotations
import math
from pathlib import Path
import unicodedata

LANGUAGES = {"fr", "en", "de", "es", "it", "pt"}

def canonical(text: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKC", text).casefold().replace("’", "'") if char.isalnum())

def verify_word_scenes(segments: list[dict], result: dict) -> tuple[list[list[dict] | None], list[str]]:
    entries = result.get("segments") if isinstance(result, dict) else None
    if not isinstance(entries, list) or len(entries) != len(segments):
        raise ValueError("The acoustic model changed the number of scenes")
    scenes: list[list[dict] | None] = []
    warnings: list[str] = []
    for index, (scene, entry) in enumerate(zip(segments, entries, strict=True)):
        words = str(scene["text"]).split()
        matches = entry.get("words", []) if isinstance(entry, dict) else []
        if (not isinstance(matches, list) or len(matches) != len(words) or
            any(not isinstance(item, dict) or canonical(str(item.get("word", ""))) != canonical(words[i])
                for i, item in enumerate(matches))):
            scenes.append(None)
            warnings.append(f"Scene {index+1}: acoustic word mismatch; estimated timing used")
            continue
        scene_start, scene_end = float(scene["start"]), float(scene["end"])
        previous = scene_start - .001
        result_words: list[dict] = []
        for item in matches:
            try:
                start, end = float(item["start"]), float(item["end"])
            except (TypeError, ValueError, KeyError):
                result_words = []
                break
            if (not math.isfinite(start) or not math.isfinite(end) or
                start < scene_start - .1 or start > scene_end or
                end > scene_end + .1 or end < start - .01 or start < previous - .01):
                result_words = []
                break
            start = max(scene_start, min(scene_end, start))
            result_words.append({"start": round(start, 3), "end": round(max(start, min(end, scene_end)), 3)})
            previous = start
        if len(result_words) != len(words):
            scenes.append(None)
            warnings.append(f"Scene {index+1}: invalid acoustic timestamps; estimated timing used")
        else:
            scenes.append(result_words)
    return scenes, warnings

def align_narration(audio: Path, segments: list[dict], *, language: str,
                    model_name: str = "tiny") -> tuple[list[list[dict] | None], list[str]]:
    if language not in LANGUAGES or model_name not in {"tiny", "base"}:
        raise ValueError("Unsupported word aligner language or model")
    if not audio.is_file() or audio.stat().st_size < 1 or not 1 <= len(segments) <= 18:
        raise ValueError("Acoustic alignment requires narration audio and valid scenes")
    try:
        import stable_whisper
    except ImportError as exc:
        raise RuntimeError("Word alignment requires stable-ts==2.19.1; install the optional dependency") from exc
    model = stable_whisper.load_model(model_name, device="cpu")
    expected = [{"text": str(item["text"]), "start": float(item["start"]),
                 "end": float(item["end"])} for item in segments]
    aligned = model.align_words(str(audio), expected, language=language)
    if aligned is None or not hasattr(aligned, "to_dict"):
        raise RuntimeError("Acoustic model returned no alignment")
    timings, warnings = verify_word_scenes(segments, aligned.to_dict())
    if not any(scene is not None for scene in timings):
        raise RuntimeError("No valid acoustically aligned scene; refusing to claim forced alignment")
    return timings, warnings
