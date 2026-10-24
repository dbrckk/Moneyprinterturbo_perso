"""Offline creator's publication kit, derived exclusively from narrated text.

The tool formats copy for Reels, TikTok and Shorts without inventing facts,
trends, endorsements, hashtags from third-party data or media permissions.
It is NOT a fact-checker, rights check or an automatic platform uploader.
"""
from __future__ import annotations

import re
import unicodedata

_LANGS = frozenset(("fr", "en", "es", "de", "it", "pt"))
_STOP = frozenset((
    "avec", "dans", "pour", "plus", "moins", "cette", "comme", "comment",
    "quel", "quelle", "quels", "quelles", "nous", "vous", "leurs", "notre",
    "les", "des", "une", "sur", "aux", "par", "est", "son", "ses", "qui",
    "and", "the", "for", "from", "with", "this", "what", "that", "your",
    "del", "las", "los", "una", "para", "con", "por", "que",
    "der", "die", "das", "den", "und", "von", "mit", "ein", "eine",
    "gli", "dei", "delle", "come", "nel", "con", "per",
    "dos", "das", "mais", "sobre", "por", "com", "uma", "este",
))
_GENERIC = {
    "fr": ("Video", "Decouverte"),
    "en": ("Video", "Discover"),
    "es": ("Video", "Descubre"),
    "de": ("Video", "Entdecken"),
    "it": ("Video", "Scopri"),
    "pt": ("Video", "Descobrir"),
}
_LABELS = {
    "fr": ("À propos", "Récapitulatif"),
    "en": ("About", "Highlights"),
    "es": ("Acerca de", "Resumen"),
    "de": ("Über das Video", "Highlights"),
    "it": ("Informazioni", "In breve"),
    "pt": ("Sobre o vídeo", "Resumo"),
}


def _plain(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected plain Unicode text")
    # Never propagate control characters or user-supplied newlines into a
    # copyable title/hashtag. Keep ordinary Unicode accents and punctuation.
    return re.sub(r"\s+", " ", "".join(
        char for char in value if char.isprintable() or char.isspace())).strip()


def _cut(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    portion = value[:limit - 1].rsplit(" ", 1)[0].rstrip(" -.,;:")
    return (portion if len(portion) > limit // 3 else value[:limit - 1]) + "…"


def _topic_tags(topic: str, lang: str) -> list[str]:
    # Ignore URL fragments and potential query tokens: promotional links do
    # not belong in inferred hashtags and must not leak into metadata tags.
    cleaned_topic = re.sub(r"https?://\S+", "", topic, flags=re.IGNORECASE)
    simplified = unicodedata.normalize("NFKD", cleaned_topic).encode(
        "ascii", errors="ignore").decode("ascii")
    terms = re.findall(r"[A-Za-z][A-Za-z0-9]{2,20}", simplified)
    tags: list[str] = []
    for term in terms:
        key = term.lower()
        if key in _STOP or key in (t.lower() for t in tags):
            continue
        tags.append(term[0].upper() + term[1:])
        if len(tags) == 3:
            break
    # Strict limit: topic hashtags, then two language-specific safe tags.
    for generic in _GENERIC[lang]:
        if generic.lower() not in {t.lower() for t in tags}:
            tags.append(generic)
    return ["#" + tag for tag in tags[:5]]


def prepare_publication(topic: str, scenes: list[str], *, lang: str,
                        aspect: str) -> dict[str, object]:
    """Build only copy supported by existing audio script and a given topic.

    The text is guidance awaiting human review, NOT a statement that music,
    visuals, source rights or platform compliance have been verified.
    """
    if (lang not in _LANGS or aspect not in ("9:16", "16:9", "1:1")
            or not isinstance(topic, str)
            or not 2 <= len(topic.strip()) <= 140
            or not isinstance(scenes, list) or not 1 <= len(scenes) <= 18
            or any(not isinstance(scene, str) or not 1 <= len(scene) <= 500
                   for scene in scenes)):
        raise ValueError("Invalid publish kit inputs")
    heading = _plain(topic)
    passages = [_plain(scene) for scene in scenes]
    if not heading or any(not passage for passage in passages):
        raise ValueError("Empty publish text")
    tags = _topic_tags(heading, lang)
    hook = _cut(passages[0], 190)
    description = _cut(" ".join(passages[:3]), 640)
    about, highlights = _LABELS[lang]
    # Format-specific generic tags are honest labeling, not promises about
    # placement in recommendation feeds.
    reels_tags = (tags + ["#Reels"])[:6]
    tik_tags = (tags + ["#TikTok"])[:6]
    is_shorts = aspect != "16:9"
    shorts_tags = (tags + ["#Shorts" if is_shorts else "#Video"])[:6]
    title = _cut(heading, 90)
    youtube_title = (_cut(title, 90) + " #Shorts") if is_shorts else title
    assert len(youtube_title) <= 100
    platforms = {
        "instagram_reels": {
            "title": title,
            "description": f"{heading}\n\n{hook}\n\n" + " ".join(reels_tags),
            "hashtags": reels_tags,
        },
        "tiktok": {
            "title": title,
            "description": hook + "\n\n" + " ".join(tik_tags),
            "hashtags": tik_tags,
        },
        "youtube_shorts" if is_shorts else "youtube_video": {
            "title": youtube_title,
            "description": f"{about}: {heading}\n\n{highlights}: {description}\n\n"
                           + " ".join(shorts_tags),
            "hashtags": shorts_tags,
        },
    }
    return {
        "filename": "video.publish.txt",
        "generated_from": "actual-narration-and-topic-only",
        "platforms": platforms,
        "aspect": aspect,
        "ready_for_publication": False,
        "human_review_required": [
            *([] if aspect == "9:16" else
              ["TikTok/Reels are primarily vertical; verify/crop framing before posting"]),
            "Check facts, spelling, names and suitability of descriptions",
            "Verify rights to media, music and voice; see video.credits.txt",
            "Confirm each platform's current publication and disclosure rules",
            "Inspect exported MP4 and adjust titles/hashtags before posting",
        ],
    }


def render_publication_text(data: dict[str, object]) -> str:
    """UTF-8 sidecar designed for easy copy/paste in an Android browser."""
    platforms = data["platforms"]
    lines = [
        "MONEYPRINTER — KIT DE PUBLICATION",
        "Texte proposé automatiquement depuis le scénario réellement narré.",
        "À vérifier et modifier avant publication. Aucune mise en ligne automatique.",
        "",
    ]
    for name, label in (
        ("instagram_reels", "INSTAGRAM REELS"),
        ("tiktok", "TIKTOK"),
        ("youtube_shorts" if "youtube_shorts" in platforms else "youtube_video",
         "YOUTUBE SHORTS" if "youtube_shorts" in platforms else "YOUTUBE VIDÉO"),
    ):
        entry = platforms[name]
        lines.extend([
            "═" * 30,
            label,
            "Titre :", entry["title"],
            "",
            "Description / légende à copier :",
            entry["description"],
            "",
        ])
    lines.extend([
        "═" * 30,
        "AVANT DE PUBLIER",
        "Vérifier la véracité des informations et les noms.",
        "Vérifier les droits des images, vidéos, voix et musiques.",
        "Lire video.credits.txt pour les sources et attributions.",
        "Vérifier le MP4, les sous-titres et les règles de la plateforme.",
        *data["human_review_required"],
        "Aucune publication n'a été effectuée automatiquement.",
        "",
    ])
    return "\n".join(lines)
