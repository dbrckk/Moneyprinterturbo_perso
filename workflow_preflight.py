#!/usr/bin/env python3
"""Fail fast on invalid GitHub Actions video requests, before downloading models.

The preflight does not inspect credentials, call network services or create MP4s.
It deliberately validates only the existing CLI and workflow constraints.
"""
from __future__ import annotations

import os
import sys

from free_studio import ASPECTS, LANG_VOICES, split_script, template_script
from media_assets import scene_search_queries


CHOICES: dict[str, frozenset[str]] = {
    "LANG": frozenset(LANG_VOICES),
    "ASPECT": frozenset(ASPECTS),
    "RESOLUTION": frozenset({"360", "720", "1080"}),
    "SCRIPT_MODE": frozenset({"template", "local-ai"}),
    "VISUAL_MODE": frozenset({"commons", "abstract"}),
    "VISUAL_AI": frozenset({"off", "clip"}),
    "VOICE": frozenset({"espeak", "piper-fr-siwis"}),
    "SPEECH_RATE": frozenset({"120", "145", "170"}),
    "VISUAL_STYLE": frozenset({"cinematic", "classic"}),
    "CAPTION_STYLE": frozenset({"kinetic", "classic"}),
    "WORD_ALIGNMENT": frozenset({"estimated", "forced"}),
    "TRANSITION": frozenset({"auto", "fade", "wipeleft", "cut"}),
}


def validate_request(values: dict[str, str]) -> dict[str, object]:
    """Validate a workflow_dispatch request without any external dependencies.

    For a genuine local LLM script, the resulting scene count cannot yet be
    known, so model-produced narration validation remains in free_studio.py.
    """
    if not isinstance(values, dict):
        raise ValueError("Expected workflow input mapping")
    for field, allowed in CHOICES.items():
        if values.get(field) not in allowed:
            raise ValueError(f"Invalid workflow setting: {field}")
    topic = values.get("TOPIC")
    if (not isinstance(topic, str) or not 2 <= len(topic.strip()) <= 140
            or "\n" in topic or "\r" in topic):
        raise ValueError("Topic must be 2–140 characters on one line")
    script = values.get("SCRIPT_TEXT", "")
    if not isinstance(script, str) or len(script) > 5000:
        raise ValueError("Narration must not exceed 5000 characters")
    overrides = values.get("SCENE_QUERIES", "")
    if not isinstance(overrides, str) or len(overrides) > 2200:
        raise ValueError("Too many scene query characters")
    ai_mode = values["SCRIPT_MODE"] == "local-ai"
    if ai_mode and script:
        raise ValueError("Custom narration cannot be combined with local AI")
    if overrides and not script:
        raise ValueError("Scene searches require a written narration")
    if values["VOICE"] == "piper-fr-siwis" and values["LANG"] != "fr":
        raise ValueError("The installed Piper SIWIS model supports French only")
    if values["WORD_ALIGNMENT"] == "forced" and values["CAPTION_STYLE"] != "kinetic":
        raise ValueError("Acoustic word alignment requires kinetic captions")
    if values["VISUAL_AI"] == "clip" and values["VISUAL_MODE"] != "commons":
        raise ValueError("Visual CLIP ranking requires Wikimedia Commons")
    if overrides and values["VISUAL_MODE"] != "commons":
        raise ValueError("Per-scene searches require Wikimedia Commons")

    # Match the actual renderer's segmentation, including character budget,
    # maximum 18 scenes and explicit Commons query normalization.
    if script:
        segments = split_script(script)
    elif ai_mode:
        segments = []
    else:
        segments = split_script(template_script(topic, values["LANG"]))
    if values["VISUAL_MODE"] == "commons" and segments:
        queries = overrides.split("|") if overrides else None
        scene_search_queries(topic, segments, queries)

    return {
        "scene_count": len(segments) if segments else None,
        "script_mode": values["SCRIPT_MODE"],
        "visuals": values["VISUAL_MODE"],
        "needs_local_model": bool(ai_mode and not script),
        "needs_clip": values["VISUAL_AI"] == "clip",
        "needs_piper": values["VOICE"] == "piper-fr-siwis",
        "needs_whisper": values["WORD_ALIGNMENT"] == "forced",
    }


def main() -> int:
    try:
        summary = validate_request(dict(os.environ))
    except ValueError as error:
        # Do not print narration text or include user-controlled Markdown.
        print(f"::error title=Invalid video request::{error}", file=sys.stderr)
        return 2
    count = summary["scene_count"]
    scene_label = str(count) if count is not None else "pending local-AI generation"
    print(f"Video request preflight passed ({scene_label} scenes; "
          f"mode={summary['script_mode']}; visuals={summary['visuals']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
