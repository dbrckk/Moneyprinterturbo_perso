"""Truthful post-render review summary for the private Android studio.

This module never certifies aesthetic quality, music rights, facts or
publication readiness. It only summarizes metadata from an already-completed
render so creators do not need to inspect a large JSON manifest on a phone.
No new FFmpeg pass, model download, network call or media-file access.
"""
from __future__ import annotations

import math
import re


MAX_WARNINGS = 6
MAX_CHECKS = 6


def _check(key: str, title: str, detail: str, *, attention: bool = False) -> dict[str, object]:
    return {"key": key, "title": title, "detail": detail, "attention": attention}


def _safe_warning(value: object) -> str | None:
    if type(value) is not str:
        return None
    # These messages are only used through textContent in an authenticated
    # same-origin UI. Still normalize control/newline characters and bound size.
    clean = re.sub(r"\s+", " ", "".join(c for c in value if c.isprintable() or c.isspace())).strip()
    return clean[:220] if clean else None


def summarize_completed_render(metadata: object) -> dict[str, object] | None:
    """Bounded, actionable review derived from one completed render manifest.

    Never imply that an MP4 is artistically reviewed, that its rights are
    verified or that synthetic benchmark results describe the user's video.
    A corrupt/missing manifest is omitted instead of producing false green.
    """
    if type(metadata) is not dict:
        return None
    segments = metadata.get("segments")
    if type(segments) is not list or not 1 <= len(segments) <= 18:
        return None
    if any(type(s) is not dict or
           type(s.get("start")) not in (int, float) or
           type(s.get("end")) not in (int, float) or
           not math.isfinite(s["start"]) or not math.isfinite(s["end"]) or
           s["start"] < 0 or s["start"] >= s["end"] for s in segments):
        return None
    n = len(segments)
    media = metadata.get("media")
    if type(media) is not list:
        media = []
    used = {
        item["segment"] for item in media
        if type(item) is dict and type(item.get("segment")) is int
        and 1 <= item["segment"] <= n and
        item.get("kind") in ("image", "video")
    }
    fallback = n - len(used)
    checks = [
        _check("media", "Visuels du montage",
               (f"{len(used)}/{n} scène(s) avec média identifié."
                + (f" {fallback} scène(s) avec fond graphique de secours."
                   if fallback else " Aucun fond graphique de secours.")),
               attention=bool(fallback)),
    ]
    speech = metadata.get("speech")
    engine = metadata.get("speech_engine")
    if speech is False or engine == "muted":
        checks.append(_check(
            "voice", "Narration", "Vidéo exportée sans voix, conformément au mode muet."))
    elif engine == "piper":
        checks.append(_check(
            "voice", "Narration", "Voix synthétique Piper. Écoutez le MP4 pour valider l'intonation."))
    elif engine == "espeak":
        checks.append(_check(
            "voice", "Narration", "Voix synthétique eSpeak. Écoutez sa prononciation et son rythme.",
            attention=True))
    else:
        checks.append(_check(
            "voice", "Narration", "Moteur vocal non précisé dans le manifeste. Vérifiez le son.",
            attention=True))

    timing = metadata.get("caption_timing")
    if timing == "forced-audio-word-onsets":
        checks.append(_check(
            "captions", "Synchronisation des sous-titres",
            "Alignement des mots mesuré sur l'audio ; vérifiez tout de même la lecture."))
    elif timing == "scene-level":
        checks.append(_check(
            "captions", "Sous-titres",
            "Sous-titres par scène. Vérifiez la lisibilité sur petit écran."))
    elif timing in ("estimated-per-word", "mixed-measured-estimated"):
        checks.append(_check(
            "captions", "Synchronisation des sous-titres",
            "Certaines positions des mots sont estimées. Vérifiez leur synchronisation.",
            attention=True))
    else:
        checks.append(_check(
            "captions", "Sous-titres",
            "Méthode de synchronisation non précisée. Vérifiez les sous-titres.",
            attention=True))

    boundary_count = max(0, n - 1)
    visual_review = metadata.get("final_visual_review")
    if boundary_count == 0:
        checks.append(_check("transitions", "Raccords", "Une seule scène : aucun raccord à vérifier."))
    elif type(visual_review) is dict:
        boundaries = visual_review.get("boundaries")
        boundaries = boundaries if type(boundaries) is list else []
        flagged = sum(1 for row in boundaries
                      if type(row) is dict and row.get("requires_manual_review") is True)
        unavailable = visual_review.get("samples_unavailable")
        unavailable = unavailable if type(unavailable) is int and unavailable >= 0 else 0
        inspected = visual_review.get("sampled_count")
        inspected = inspected if type(inspected) is int and inspected >= 0 else len(boundaries)
        if flagged or unavailable:
            checks.append(_check(
                "transitions", "Raccords vidéo",
                f"{flagged} raccord(s) signalé(s) pour vérification ; "
                f"{unavailable} échantillon(s) indisponible(s) parmi {inspected} analysé(s).",
                attention=True))
        elif inspected:
            checks.append(_check(
                "transitions", "Raccords vidéo",
                f"{inspected}/{boundary_count} frontière(s) échantillonnée(s), "
                "aucun défaut prioritaire signalé. Les autres plans restent à regarder."))
        else:
            checks.append(_check(
                "transitions", "Raccords vidéo",
                "Aucun raccord analysé après encodage. Vérifiez les transitions.",
                attention=True))
    else:
        checks.append(_check(
            "transitions", "Raccords vidéo",
            f"{boundary_count} raccord(s) : contrôle après encodage non disponible.",
            attention=True))

    checks.append(_check(
        "music", "Musique et ambiance",
        "Aucune musique automatiquement ajoutée par ce mode. "
        "Les sons des vidéos sources ne sont pas automatiquement conservés."))
    checks.append(_check(
        "rights", "Droits et publication",
        "Vérifiez les sources, les personnes filmées, les faits et les règles "
        "des plateformes avant de publier.",
        attention=True))

    raw_warnings = metadata.get("warnings")
    raw_warnings = raw_warnings if type(raw_warnings) is list else []
    warnings = []
    for item in raw_warnings:
        parsed = _safe_warning(item)
        if parsed is not None:
            warnings.append(parsed)
        if len(warnings) >= MAX_WARNINGS:
            break
    return {
        "status": "exported-not-artistically-approved",
        "scenes": n,
        "with_media": len(used),
        "with_graphic_fallback": fallback,
        "checks": checks[:MAX_CHECKS],
        "warnings": warnings,
        "warnings_total": min(1000, len(raw_warnings)),
        "manual_review_required": True,
        "summary": ("Le fichier vidéo est exporté. Ce rapport repose sur ses "
                    "métadonnées techniques, pas sur un jugement esthétique."),
    }
