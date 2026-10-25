"""Fast, offline structural review of automatically generated video narration.

This is not a fact checker, language classifier or artistic-quality grade.
It deliberately never rejects a user's own script: only opt-in local LLM
generation uses the results to request one bounded repair before rendering.
"""
from __future__ import annotations

import re
import unicodedata

_REVIEW_POLICY = "narration-structure-v1"
_WORDS = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?。！？])\s+|\n+")
_LIST_START = re.compile(r"(?m)^\s*(?:#{1,6}\s|[-*•]\s|\d+[.)]\s)")
_WEB_LINK = re.compile(r"https?://\S+", re.IGNORECASE)


def _tokens(sentence: str) -> set[str]:
    plain = unicodedata.normalize("NFKD", sentence.casefold())
    plain = "".join(letter for letter in plain
                    if not unicodedata.combining(letter))
    return {word for word in _WORDS.findall(plain) if len(word) > 2}


def assess_narration(value: object) -> dict[str, object]:
    """Assess mechanical narration faults without pretending to verify facts.

    A tolerant 40–145-word, 3–9-sentence envelope permits normal variance
    around the LLM prompt's 60–110 words / 4–6 sentences. A human-written
    script remains valid regardless of this recommendation.
    """
    issues: list[str] = []
    if not isinstance(value, str) or not value.strip():
        return {
            "policy": _REVIEW_POLICY, "status": "needs_review",
            "word_count": 0, "sentence_count": 0,
            "issues": ["empty_or_invalid_narration"],
            "factual_accuracy_verified": False,
            "visual_relevance_verified": False,
        }
    raw = value.strip()
    # Preserve raw text: never silently rewrite or invent narration.
    sentences = [line.strip() for line in _SENTENCE_BREAK.split(raw)
                 if line.strip()]
    words = _WORDS.findall(raw)
    if len(raw) > 5000:
        issues.append("script_exceeds_render_limit")
    if not 40 <= len(words) <= 145:
        issues.append("word_count_outside_short_video_range")
    if not 3 <= len(sentences) <= 9:
        issues.append("scene_count_outside_short_video_range")
    if _LIST_START.search(raw) or "\u0060\u0060\u0060" in raw or raw.startswith(("{", "[")):
        issues.append("formatting_instead_of_plain_narration")
    if _WEB_LINK.search(raw) or re.search(r"(?<!\w)#[\wÀ-ÿ]{2,}", raw):
        issues.append("links_or_hashtags_in_spoken_script")
    if re.search(r"(?i)(?:^|\n)\s*(?:narration|script|voice.?over)\s*:", raw):
        issues.append("editorial_heading_in_spoken_script")

    # Temporal repetition is a noticeable low-quality voiceover fault.
    previous: list[set[str]] = []
    for sentence in sentences:
        tokens = _tokens(sentence)
        if len(tokens) >= 5:
            if any(len(other) >= 5 and
                   len(tokens & other) / max(len(tokens | other), 1) >= 0.82
                   for other in previous):
                issues.append("repeated_or_near_duplicate_sentences")
                break
        previous.append(tokens)
    if raw[-1] not in ".!?。！？":
        issues.append("narration_missing_final_punctuation")
    if any(len(sentence) > 240 for sentence in sentences):
        issues.append("long_sentence_requires_rewrite")
    return {
        "policy": _REVIEW_POLICY,
        "status": "structurally_acceptable" if not issues else "needs_review",
        "word_count": len(words), "sentence_count": len(sentences),
        "issues": issues,
        "factual_accuracy_verified": False,
        "visual_relevance_verified": False,
    }


def repair_guidance(review: dict[str, object]) -> str:
    """Generate fixed, non-executable follow-up instructions from known codes."""
    mapping = {
        "empty_or_invalid_narration": "Produce actual narration, not an empty reply.",
        "script_exceeds_render_limit": "Keep the narration within 5000 characters.",
        "word_count_outside_short_video_range": "Aim for 60 to 110 spoken words.",
        "scene_count_outside_short_video_range": "Use four to six distinct sentences.",
        "formatting_instead_of_plain_narration": "Remove bullets, JSON, headings and code blocks.",
        "links_or_hashtags_in_spoken_script": "Remove URLs and social hashtags.",
        "editorial_heading_in_spoken_script": "Omit labels such as Narration: or Script:.",
        "repeated_or_near_duplicate_sentences": "Do not restate the same idea in another sentence.",
        "narration_missing_final_punctuation": "Finish the narration with natural final punctuation.",
        "long_sentence_requires_rewrite": "Shorten sentences for clear voice-over and captions.",
    }
    codes = review.get("issues") if isinstance(review, dict) else None
    if not isinstance(codes, list) or not all(
            isinstance(code, str) and code in mapping for code in codes):
        raise ValueError("Unknown structural narration review errors")
    return " ".join(mapping[code] for code in codes[:10])
