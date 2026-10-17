"""No-cost visual sources for Free Studio.

Remote media is strictly limited to Wikimedia Commons files marked CC0 or
public domain. Every selected asset carries provenance into the output.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
import html
import json
import math
from pathlib import Path
import re
import subprocess
import time
import unicodedata
from urllib import parse, request

from visual_hash import near_duplicate, visual_signature
from semantic_rank import english_visual_prompt, rerank as semantic_rerank

API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "MoneyPrinterTurboPerso-FreeStudio/0.2 (+https://github.com/dbrckk/Moneyprinterturbo_perso)"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv"}
ALLOWED_LICENSES = {"cc0", "cc0 1.0", "public domain", "pd-old", "pd-self", "pd-us", "pd-art"}
MAX_IMAGE_BYTES = 18 * 1024 * 1024


@dataclass(frozen=True)
class MediaAsset:
    file: Path
    kind: str
    source_url: str
    license: str
    author: str
    title: str
    # Descriptive metadata heuristic; not visual content verification.
    visual_medium: str = "unspecified"
    crop_retention_estimate: float | None = None
    visual_fingerprint: int | None = None
    # Deterministic lexical evidence only; not image-content verification.
    query_term_count: int | None = None
    query_term_coverage: float | None = None
    semantic_score: float | None = None

    def metadata(self) -> dict[str, str]:
        result = asdict(self)
        # Rendering internals should not be confused with provenance.
        result.pop("visual_fingerprint", None)
        result["file"] = str(self.file)
        return result


def media_dimensions(path: Path) -> tuple[int, int] | None:
    """Try decoding the first video/image stream before FFmpeg processing."""
    try:
        process = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=12, check=True,
        )
        data = json.loads(process.stdout)
        stream = data.get("streams", [{}])[0]
        width, height = int(stream["width"]), int(stream["height"])
        if 64 <= width <= 10000 and 64 <= height <= 10000:
            return width, height
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError):
        pass
    return None


def local_assets(folder: Path) -> tuple[list[MediaAsset], list[str]]:
    if not folder.is_dir():
        raise ValueError(f"Media directory does not exist: {folder}")
    assets: list[MediaAsset] = []
    warnings: list[str] = []
    for path in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        ext = path.suffix.lower()
        if path.is_symlink() or not path.is_file() or ext not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
            continue
        limit = MAX_IMAGE_BYTES if ext in IMAGE_EXTENSIONS else 350 * 1024 * 1024
        if path.stat().st_size > limit or not media_dimensions(path):
            warnings.append(f"Skipped invalid or oversized local file: {path.name}")
            continue
        kind = "image" if ext in IMAGE_EXTENSIONS else "video"
        assets.append(MediaAsset(path.resolve(), kind, "local", "user-provided", "user", path.name))
    return assets, warnings


def _plain(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]*>", "", value)).strip()[:250]


def _field(info: dict, name: str) -> str:
    metadata = info.get("extmetadata", {})
    if not isinstance(metadata, dict):
        return ""
    value = metadata.get(name, {})
    return str(value.get("value", "")) if isinstance(value, dict) else ""


def _trusted_url(url: str, *, api: bool = False) -> bool:
    try:
        parsed = parse.urlsplit(url)
        return (parsed.scheme == "https" and not parsed.username and not parsed.password
                and parsed.port in (None, 443) and not parsed.fragment
                and parsed.hostname == ("commons.wikimedia.org" if api else "upload.wikimedia.org"))
    except ValueError:
        return False


class _TrustedRedirectHandler(request.HTTPRedirectHandler):
    """Validate the redirect destination *before* any new HTTP request."""

    def __init__(self, *, api: bool):
        self.api = api

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _trusted_url(newurl, api=self.api):
            raise ValueError("Cross-host redirect blocked before request")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_limited(url: str, max_bytes: int, *, timeout: int) -> bytes:
    api = url == API or url.startswith(API + "?")
    if not _trusted_url(url, api=api):
        raise ValueError("Media URL has an unexpected host")
    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    # urlopen() silently follows redirects before response.geturl() can be
    # checked. Restrict every hop to the same approved Wikimedia host.
    opener = request.build_opener(_TrustedRedirectHandler(api=api))
    with opener.open(req, timeout=timeout) as response:
        if not _trusted_url(response.geturl(), api=api):
            raise ValueError("Cross-host redirect blocked")
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > max_bytes:
            raise ValueError("Remote file exceeds size limit")
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("Remote file exceeds size limit")
    return data


def _words(text: str) -> set[str]:
    # Accent-insensitive overlap is deterministic and requires no paid NLP API.
    normalized = unicodedata.normalize("NFKD", text.lower())
    ascii_text = "".join(c for c in normalized if not unicodedata.combining(c))
    return set(re.findall(r"[a-z0-9]{3,}", ascii_text))


# Conservative visual nouns only. This is NOT machine translation or a
# visual-semantic model: named people, locations and abstract claims must still
# match literally. Concept matching helps French narration find English titles.
_VISUAL_CONCEPT_GROUPS = (
    ("forest", "forests", "foret", "forets"),
    ("mountain", "mountains", "montagne", "montagnes"),
    ("river", "rivers", "riviere", "rivieres"),
    ("lake", "lakes", "lac", "lacs"),
    ("beach", "beaches", "plage", "plages"),
    ("waterfall", "waterfalls", "cascade", "cascades"),
    ("snow", "neige", "neiges"),
    ("bridge", "bridges", "pont", "ponts"),
    ("castle", "castles", "chateau", "chateaux"),
    ("garden", "gardens", "jardin", "jardins"),
    ("flower", "flowers", "fleur", "fleurs"),
    ("tree", "trees", "arbre", "arbres"),
    ("bird", "birds", "oiseau", "oiseaux"),
    ("street", "streets", "rue", "rues"),
    ("solar", "solaire"),
)
_VISUAL_CONCEPTS = {
    term: group[0] for group in _VISUAL_CONCEPT_GROUPS for term in group
}


def _concepts(words: set[str]) -> set[str]:
    return {_VISUAL_CONCEPTS.get(word, word) for word in words}


def lexical_evidence(query: str, title: str, description: str = "") -> dict[str, int | float]:
    """Explainable, bounded lexical evidence. Never certifies image contents."""
    wanted = _words(query) - _words(" ".join(_SEARCH_STOPWORDS))
    if not wanted:
        wanted = _words(query)
    targets = _concepts(wanted)
    title_terms = _concepts(_words(title.replace("File:", "")))
    description_terms = _concepts(_words(description[:400]))
    matches = targets & (title_terms | description_terms)
    return {
        "score": 3 * len(targets & title_terms) + len(targets & description_terms),
        "query_terms": len(targets),
        "matched_terms": len(matches),
        "coverage": round(len(matches) / len(targets), 3) if targets else 0.0,
    }


def relevance_score(query: str, title: str, description: str = "") -> int:
    """Deterministic weighted title/description match with limited FR/EN nouns."""
    return int(lexical_evidence(query, title, description)["score"])


# An ambiguous landscape noun can also be a person's family name, a
# painting title or a biography. Do not treat matching the surname alone as
# evidence the picture illustrates the place, even when the request includes
# explicit landscape-context words. Prefer the graphic fallback.
_SCENIC_SINGLE_QUERY_WORDS = _words("""
    montagne montagnes mountain mountains riviere rivieres river rivers
    ocean oceans sea seas mer mers foret forets forest forests
    lake lakes lac lacs beach beaches plage plages waterfall waterfalls
    cascade cascades valley valleys vallee vallees desert deserts
""")
_SCENIC_CONTEXT_WORDS = _words("""
    panorama panoramique panoramic panorama view vista landscape landscapes
    paysage paysages scenery nature naturel natural wild wilderness
    alps alpine alp mountains peak peaks summit summits massif neige snowy
    forest woods woodland bois woodland trees arbres vallon vallee valley
    riverbank stream riverside watercourse shore bank torrent creeks
    seaside coastline coastal coast shoreline ocean sea beach seascape
    plage plages dune dunes dunes waves vague vagues island islands ile iles
    lake lakes lac lacs waterfall waterfalls cascades creeks
    hiking trail trek scenery aerial drone photography photographed photo
    réserve reserve park parc national mountains paysage
    ciel sky clouds nuages sunrise sunset horizon reflection reflected
    sunrise coucher soleil volcan volcano glaciers glacial
""")
_NON_SCENIC_SUBJECT_WORDS = _words("""
    portrait portraits portraits botaniste botanist scientist scientifique
    biographie biography people people person woman man women men
    musician author écrivain ecrivain composer actor actress acteur actrice
    artwork painting paintings peintre artist artists tableau tableaux
    engraving engravings stamp stamps book bookcover couverture
    philosopher philosophe politician politics député depute
""")


def safe_scenic_score(query: str, title: str, description: str = "") -> int:
    """Avoid false landscape matches on people sharing a nature surname.

    Applies to one-word landscape queries and explicit scenic combinations,
    such as "rivière paysage". A named person query ("Henri Rivière") stays
    eligible because it does not request a landscape. This is only a text
    heuristic; no claim of visual understanding is made.
    """
    score = relevance_score(query, title, description)
    if score <= 0:
        return 0
    request_words = _words(query) - _words(" ".join(_SEARCH_STOPWORDS))
    scenic_words = request_words & _SCENIC_SINGLE_QUERY_WORDS
    if not scenic_words:
        return score
    scenic_intent = (len(request_words) == 1 or
                     bool((request_words - scenic_words) & _SCENIC_CONTEXT_WORDS))
    if not scenic_intent:
        return score
    content_words = _words(title + " " + description)
    if content_words & _NON_SCENIC_SUBJECT_WORDS:
        return 0
    # A translated alias of the requested subject is NOT independent scenic
    # context: 'Jean Forest' must not satisfy a request for 'forêt' merely
    # because 'forest' is both an English alias and a context word.
    scenic_concepts = _concepts(scenic_words)
    independent_context = {
        word for word in content_words
        if _VISUAL_CONCEPTS.get(word, word) not in scenic_concepts
    }
    if not (independent_context & _SCENIC_CONTEXT_WORDS):
        return 0
    return score


_SCENIC_EXPANSION_HINTS = {
    "montagne": "panorama", "montagnes": "panorama",
    "mountain": "panorama", "mountains": "panorama",
    "foret": "paysage", "forets": "paysage",
    "forest": "landscape", "forests": "landscape",
    "riviere": "paysage", "rivieres": "paysage",
    "river": "landscape", "rivers": "landscape",
    "ocean": "paysage", "oceans": "paysage",
    "sea": "seascape", "seas": "seascape",
    "mer": "paysage", "mers": "paysage",
    "lac": "paysage", "lacs": "paysage",
    "lake": "landscape", "lakes": "landscape",
    "plage": "paysage", "plages": "paysage",
    "beach": "landscape", "beaches": "landscape",
    "vallee": "paysage", "vallees": "paysage",
    "valley": "landscape", "valleys": "landscape",
}


def expand_scenic_search_query(query: str) -> str:
    """Add one tested scene-context term to an ambiguous single nature word.

    This only changes the Commons API search, not user-visible narration,
    source attribution or candidate relevance checks. Names like 'Henri
    Rivière' remain unchanged because they contain two meaningful words.
    """
    meaningful = _words(query) - _words(" ".join(_SEARCH_STOPWORDS))
    if len(meaningful) != 1:
        return query
    key = next(iter(meaningful))
    hint = _SCENIC_EXPANSION_HINTS.get(key)
    return query + " " + hint if hint else query


_ARTWORK_MEDIUM_CUES = _words("""
    painting paintings painted painter canvas watercolor watercolour
    aquarelle aquarelles huile toile gravure gravures engraving engravings
    lithograph lithographs lithographie lithographies etching etchings
    illustration illustrations illustrated drawing drawings sketch sketches
    print prints oil on canvas
    peinture peintures tableau tableaux dessin dessins
""")
_PHOTOGRAPH_MEDIUM_CUES = _words("""
    photograph photographs photography photographic photographed
    photographe photographie photographies photo photos
    fotograf foto fotografia fotografie camera snapshot snapshots
""")
_MEDIUM_PRIORITY = {"photograph-likely": 0, "unspecified": 1, "artwork-likely": 2}


def visual_medium_hint(title: str, description: str = "", categories: str = "") -> str:
    """Report a conservative *text-based* guess, never a verified image class.

    Explicit art terminology beats photo claims when both occur. Lack of
    positive evidence is 'unspecified', not a fictional photo classification.
    """
    words = _words(" ".join((title, description, categories)))
    if words & _ARTWORK_MEDIUM_CUES:
        return "artwork-likely"
    if words & _PHOTOGRAPH_MEDIUM_CUES:
        return "photograph-likely"
    return "unspecified"


def crop_retention(width: object, height: object,
                   target_aspect: float | None) -> float | None:
    """Estimate the fraction of image area retained by center-cropping.

    Zero processing/network overhead: uses source dimensions from Wikimedia's
    existing imageinfo request. Not an estimate of content/subject position.
    """
    if target_aspect is None:
        return None
    try:
        w, h = float(width), float(height)
    except (ValueError, TypeError):
        return None
    if not all(math.isfinite(v) and v > 0 for v in (w, h)):
        return None
    source_aspect = w / h
    return round(min(source_aspect / target_aspect,
                     target_aspect / source_aspect), 4)


def commons_assets(topic: str, work: Path, *, limit: int = 5,
                   timings: dict[str, float | int] | None = None,
                   target_aspect: float | None = None
                   ) -> tuple[list[MediaAsset], list[str]]:
    """Collect verified CC0 / public-domain image thumbnails. No API key needed.

    Failures do not leave incomplete files or break the offline renderer.
    """
    if not 1 <= limit <= 8:
        raise ValueError("Commons media limit out of range")
    if target_aspect is not None and (
            isinstance(target_aspect, bool) or not isinstance(target_aspect, (int, float))
            or not math.isfinite(target_aspect) or not 0.2 <= target_aspect <= 5):
        raise ValueError("Unsupported target aspect ratio")
    query = re.sub(r"\s+", " ", topic).strip()[:110]
    search_phrase = expand_scenic_search_query(query)
    params = parse.urlencode({
        "action": "query", "format": "json", "generator": "search", "gsrsearch": search_phrase,
        "gsrnamespace": "6", "gsrlimit": "30", "prop": "imageinfo|categories",
        "cllimit": "max", "clshow": "!hidden",
        "iiprop": "url|extmetadata|size", "iiurlwidth": "1440",
    })
    lookup_started = time.perf_counter()
    if timings is not None:
        timings.update({"api_seconds": 0.0, "download_seconds": 0.0,
                        "verification_seconds": 0.0, "download_bytes": 0,
                        "download_attempts": 0})
    try:
        payload = json.loads(_read_limited(API + "?" + params, 2 * 1024 * 1024, timeout=14))
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        return [], [f"Wikimedia Commons lookup failed: {type(exc).__name__}"]
    finally:
        if timings is not None:
            timings["api_seconds"] = round(time.perf_counter() - lookup_started, 4)
    assets: list[MediaAsset] = []
    warnings: list[str] = []
    pages = payload.get("query", {}).get("pages", {})
    if not isinstance(pages, dict):
        return [], ["Wikimedia Commons returned invalid results"]
    # Rank licensed candidates *before downloading*; the Wikimedia result order
    # is not a guarantee that a photo depicts this particular narrated scene.
    ranked: list[tuple[int, int, float, int, dict, dict, str, str, str, str,
                       float | None]] = []
    for page in pages.values():
        if not isinstance(page, dict):
            continue
        title = str(page.get("title", ""))
        if Path(title).suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        info_list = page.get("imageinfo") or []
        if not isinstance(info_list, list) or not info_list or not isinstance(info_list[0], dict):
            continue
        info = info_list[0]
        license_name = _plain(_field(info, "LicenseShortName"))
        if license_name.lower() not in ALLOWED_LICENSES:
            continue
        url = str(info.get("thumburl") or info.get("url") or "")
        page_url = str(info.get("descriptionurl") or "")
        if not _trusted_url(url) or not _trusted_url(page_url, api=True):
            continue
        description = _plain(_field(info, "ObjectName")) + " " + _plain(_field(info, "ImageDescription"))
        score = safe_scenic_score(query, title, description)
        if score:
            categories = " ".join(
                str(c.get("title", "")) for c in page.get("categories", [])
                if isinstance(c, dict)
            )
            categories += " " + _plain(_field(info, "Categories"))
            medium = visual_medium_hint(title, description, categories)
            # A positive photo signal must also be relevant by title; generic
            # descriptions alone should not beat accurately titled scenery.
            medium_rank = _MEDIUM_PRIORITY[medium]
            if medium == "photograph-likely" and score < 3:
                medium_rank = _MEDIUM_PRIORITY["unspecified"]
            retention = crop_retention(
                info.get("thumbwidth", info.get("width")),
                info.get("thumbheight", info.get("height")), target_aspect)
            # Relevance must outrank a weak text-only media-class guess:
            # a barely related photograph must not beat a clearly described
            # artwork. Break relevance ties by medium and then crop suitability.
            ranking_retention = retention if retention is not None else 0.5
            ranked.append((-score, medium_rank, -ranking_retention,
                           int(page.get("index", 100000)), page, info,
                           license_name, url, page_url, medium, retention))
    ranked.sort(key=lambda entry: (entry[0], entry[1], entry[2], entry[3]))
    for _negscore, _, _, _, page, info, license_name, url, page_url, medium, retention in ranked:
        if len(assets) >= limit:
            break
        title = str(page.get("title", ""))
        suffix = Path(title).suffix.lower()
        target = work / f"commons_{len(assets):02}{suffix}"
        if timings is not None:
            timings["download_attempts"] += 1
        downloaded = time.perf_counter()
        try:
            data = _read_limited(url, MAX_IMAGE_BYTES, timeout=25)
            if timings is not None:
                timings["download_bytes"] += len(data)
            target.write_bytes(data)
            if timings is not None:
                timings["download_seconds"] += time.perf_counter() - downloaded
            checked = time.perf_counter()
            valid = media_dimensions(target)
            if timings is not None:
                timings["verification_seconds"] += time.perf_counter() - checked
            if not valid:
                target.unlink(missing_ok=True)
                continue
        except (OSError, ValueError, subprocess.SubprocessError):
            if timings is not None and not target.is_file():
                timings["download_seconds"] += time.perf_counter() - downloaded
            target.unlink(missing_ok=True)
            warnings.append(f"Could not use Wikimedia image: {title[:90]}")
            continue
        author = _plain(_field(info, "Artist")) or "unknown"
        hash_started = time.perf_counter()
        fingerprint = visual_signature(target)
        if timings is not None:
            timings["fingerprint_seconds"] = (timings.get("fingerprint_seconds", 0)
                                              + time.perf_counter() - hash_started)
        evidence = lexical_evidence(query, title, _plain(_field(info, "ObjectName"))
                                    + " " + _plain(_field(info, "ImageDescription")))
        assets.append(MediaAsset(target, "image", page_url, license_name, author, title,
                                 visual_medium=medium, crop_retention_estimate=retention,
                                 visual_fingerprint=fingerprint,
                                 query_term_count=int(evidence["query_terms"]),
                                 query_term_coverage=float(evidence["coverage"])))
        if medium == "artwork-likely":
            warnings.append(f"Likely artwork rather than a photograph: {title[:90]}")
    if not assets:
        warnings.append("No relevant verified public-domain/CC0 images found; using graphic backgrounds")
    return assets, warnings


# Deliberately conservative stop words: these generate search terms, not a
# translation or semantic understanding of a scene. Users can override them.
_SEARCH_STOPWORDS = set("""
    le la les un une des de du et ou mais dans sur sous pour avec sans que qui
    vous nous ils elles sont était être avoir leurs notre votre comme plus moins
    voici voilà découvrir découvrons regardons regardez comprendre exemple exemples
    idées idées utiles faire faites voici tout toute tous très cette ces ceci cela
    pas ne n en à au aux est a ont du et il elle des the a an and of or with
    without in on to for from this that there here these those are is was be
    look watch see explore discover lets useful examples understand learn more
    your their its about because also how why what when where do does did
    vamos las los una unos por para con sobre como que del desde ver estas estos
""".split())


def normalize_scene_query(text: str) -> str:
    """Extract a few searchable keywords without emitting search operators."""
    if not isinstance(text, str):
        raise ValueError("Scene query must be text")
    terms = re.findall(r"[^\W_][\wÀ-ÿ'-]*", text.lower(), flags=re.UNICODE)
    cleaned: list[str] = []
    for term in terms:
        term = term.strip("'-_")
        if len(term) >= 3 and term not in _SEARCH_STOPWORDS and term not in cleaned:
            cleaned.append(term)
        if len(cleaned) >= 3:
            break
    return " ".join(cleaned)[:110]


def scene_search_queries(topic: str, segments: list[str], overrides: list[str] | None = None) -> list[str]:
    """One transparent query per narration scene; never imply AI semantics."""
    if overrides is not None and len(overrides) != len(segments):
        raise ValueError(f"Expected {len(segments)} scene queries, received {len(overrides)}")
    topic_query = normalize_scene_query(topic)
    if not topic_query:
        raise ValueError("Topic does not contain searchable words")
    queries: list[str] = []
    for idx, segment in enumerate(segments):
        supplied = overrides[idx] if overrides is not None else segment
        query = normalize_scene_query(supplied)
        if overrides is not None and not query:
            raise ValueError(f"Scene query {idx + 1} has no valid words")
        queries.append(query or topic_query)
    return queries


# Secondary queries are restricted to known nature subjects. They are used
# only when a repeated-topic storyboard needs additional DIFFERENT images.
# The first request is still always the user's own query (+ optional context).
_SCENIC_ALTERNATE_SEARCHES = {
    "montagne": "montagne paysage", "montagnes": "montagne paysage",
    "mountain": "mountain landscape", "mountains": "mountain landscape",
    "foret": "forêt nature", "forets": "forêt nature",
    "forest": "forest nature", "forests": "forest nature",
    "riviere": "rivière panorama", "rivieres": "rivière panorama",
    "river": "river scenery", "rivers": "river scenery",
    "ocean": "ocean seascape", "oceans": "ocean seascape",
    "sea": "sea landscape", "seas": "sea landscape",
    "lac": "lac panorama", "lacs": "lac panorama",
    "lake": "lake scenery", "lakes": "lake scenery",
    "plage": "plage côte", "plages": "plage côte",
    "beach": "beach seascape", "beaches": "beach seascape",
}


def alternate_scenic_search_query(query: str) -> str | None:
    meaningful = _words(query) - _words(" ".join(_SEARCH_STOPWORDS))
    if len(meaningful) != 1:
        return None
    secondary = _SCENIC_ALTERNATE_SEARCHES.get(next(iter(meaningful)))
    if secondary is None or secondary == expand_scenic_search_query(query):
        return None
    return secondary


def commons_scene_assets(queries: list[str], work: Path, *,
                         indices: list[int] | None = None, max_searches: int = 8,
                         max_workers: int = 3,
                         target_aspect: float | None = None,
                         stats: dict[str, float | int | str] | None = None,
                         semantic_ranking: bool = False
                         ) -> tuple[dict[int, MediaAsset], list[str]]:
    """Fetch varied CC0/PD pictures for repeated topics without extra API searches.

    Eight independent queries and three workers by default. Repeated queries
    reuse their *single* Commons API result but may download up to three
    distinct ranked images, subject to a shared budget of four extra images.
    The minimum of one image attempt per accepted query is preserved.
    """
    if len(queries) > 18 or not 1 <= max_searches <= 18:
        raise ValueError("Commons scene search limit out of range")
    if not isinstance(max_workers, int) or not 1 <= max_workers <= 3:
        raise ValueError("Commons concurrency must be between 1 and 3")
    if not isinstance(semantic_ranking, bool):
        raise ValueError("Semantic ranking must be a boolean")
    if target_aspect is not None and (
            isinstance(target_aspect, bool) or not isinstance(target_aspect, (int, float))
            or not math.isfinite(target_aspect) or not 0.2 <= target_aspect <= 5):
        raise ValueError("Unsupported target aspect ratio")
    wanted = list(range(len(queries))) if indices is None else list(indices)
    if any(not isinstance(i, int) or i < 0 or i >= len(queries) for i in wanted):
        raise ValueError("Invalid requested scene index")

    started = time.perf_counter()
    # Prepare the budget before launching workers: stable regardless of
    # network completion order and no redundant API calls per topic.
    query_indices: dict[str, list[int]] = {}
    budget_exceeded: set[int] = set()
    for i in wanted:
        query = queries[i]
        if query not in query_indices and len(query_indices) >= max_searches:
            budget_exceeded.add(i)
            continue
        query_indices.setdefault(query, []).append(i)

    # One search per user query and at most four extra image candidates.
    # Additional alternative searches are counted against the same global
    # max_searches budget; we never circumvent that upper bound.
    limits = {query: 1 for query in query_indices}
    extra_budget = 4
    for _ in range(2):
        for query, occurrences in query_indices.items():
            if extra_budget and len(occurrences) > limits[query]:
                limits[query] += 1
                extra_budget -= 1

    # Opt-in CLIP needs at least two licensed thumbnail candidates for a
    # meaningful comparison. Keep the existing global four-extra-image budget.
    # Repeated-topic diversity keeps priority over unique-scene reranking.
    if semantic_ranking:
        for query in query_indices:
            if extra_budget and limits[query] == 1 and english_visual_prompt(query):
                limits[query] = 2
                extra_budget -= 1

    available_secondary = min(2, max_searches - len(query_indices))
    alternate_queries: dict[str, str] = {}
    for query, occurrences in query_indices.items():
        alternate = alternate_scenic_search_query(query)
        if (available_secondary and len(occurrences) >= 3
                and limits[query] > 1 and alternate is not None):
            alternate_queries[query] = alternate
            available_secondary -= 1

    def fetch(query: str, index: int, limit: int,
              secondary_query: str | None
              ) -> tuple[list[MediaAsset], list[str], dict[str, float | int]]:
        directory = work / f"commons_scene_{index:02}"
        directory.mkdir(parents=True, exist_ok=True)
        timings: dict[str, float | int] = {}
        aspect_options = {"target_aspect": target_aspect} if target_aspect is not None else {}
        assets, notices = commons_assets(query, directory, limit=limit,
                                         timings=timings, **aspect_options)
        # No second API call when the first licensed result set was sufficient.
        timings["secondary_queries_attempted"] = 0
        if secondary_query and len(assets) < limit:
            alternate_folder = directory / "secondary"
            alternate_folder.mkdir(exist_ok=True)
            second_timings: dict[str, float | int] = {}
            timings["secondary_queries_attempted"] = 1
            try:
                alternatives, extra_notices = commons_assets(
                    secondary_query, alternate_folder,
                    limit=limit-len(assets), timings=second_timings,
                    **aspect_options)
            except Exception as exc:
                alternatives, extra_notices = [], [
                    f"Secondary Commons lookup failed: {type(exc).__name__}"]
            existing = {item.source_url for item in assets}
            for item in alternatives:
                if item.source_url not in existing and len(assets) < limit:
                    # Evidence for a broadened search describes THAT search,
                    # not necessarily the original narrated scene. Do not
                    # report it as original-query coverage in video QA.
                    assets.append(replace(item, query_term_count=None,
                                          query_term_coverage=None))
                    existing.add(item.source_url)
            if assets:
                notices = [notice for notice in notices
                           if "No relevant verified public-domain/CC0 images found" not in notice]
            notices.extend(extra_notices if not assets else [
                notice for notice in extra_notices
                if "No relevant verified public-domain/CC0 images found" not in notice])
            for key in ("api_seconds", "download_seconds", "verification_seconds",
                        "download_bytes", "download_attempts"):
                timings[key] = timings.get(key, 0) + second_timings.get(key, 0)
        # commons_assets already orders by textual relevance, medium and crop.
        # Do not re-sort by medium/crop alone: that would put a weakly related
        # photograph ahead of a more relevant visual during scene assignment.
        # Any supplementary assets stay behind the initial query's matches.
        assets = assets[:limit]
        return assets, notices, timings

    results: dict[str, tuple[list[MediaAsset], list[str], dict[str, float | int]]] = {}
    pool_size = min(max_workers, len(query_indices))
    if pool_size:
        with ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix="commons") as pool:
            futures = {query: pool.submit(fetch, query, ids[0], limits[query],
                                          alternate_queries.get(query))
                       for query, ids in query_indices.items()}
            for query in query_indices:
                try:
                    results[query] = futures[query].result()
                except Exception as exc:
                    results[query] = ([], [f"Wikimedia scene lookup failed: {type(exc).__name__}"], {})

    semantic_metrics: dict[str, float | int | str] = {}
    semantic_warnings: list[str] = []
    if semantic_ranking and results:
        results, semantic_metrics, semantic_warnings = semantic_rerank(results, work)

    selected: dict[int, MediaAsset] = {}
    warnings: list[str] = list(semantic_warnings)
    used_urls: set[str] = set()
    used_signatures: list[int] = []
    duplicate_candidates_avoided = 0
    usage: dict[str, int] = {}
    for i in wanted:
        query = queries[i]
        if i in budget_exceeded or query not in results:
            warnings.append(f"Scene {i+1}: search budget reached; graphic fallback")
            continue
        assets, notices, _ = results[query]
        if i == query_indices[query][0]:
            warnings.extend(f"Scene {i+1}: {notice}" for notice in notices)
        if not assets:
            continue

        # Rotate candidates for repeated topics. If another query happened to
        # return the same Commons file, first try an as-yet-unused alternative.
        cursor = usage.get(query, 0)
        ordered = assets[cursor % len(assets):] + assets[:cursor % len(assets)]
        # Prefer genuinely different pixels, not just differently named files.
        # A hash failure never excludes an image; URL-based rotation remains.
        unique_urls = [a for a in ordered if a.source_url not in used_urls]
        pixel_diverse = [a for a in unique_urls
                         if a.visual_fingerprint is None or not any(
                             near_duplicate(a.visual_fingerprint, prior)
                             for prior in used_signatures)]
        if pixel_diverse:
            chosen = pixel_diverse[0]
            if chosen is not ordered[0]:
                duplicate_candidates_avoided += sum(
                    a.visual_fingerprint is not None and any(
                        near_duplicate(a.visual_fingerprint, prior)
                        for prior in used_signatures)
                    for a in unique_urls[:unique_urls.index(chosen)])
        elif unique_urls:
            chosen = unique_urls[0]
        else:
            chosen = ordered[0]
        selected[i] = chosen
        used_urls.add(chosen.source_url)
        if chosen.visual_fingerprint is not None:
            used_signatures.append(chosen.visual_fingerprint)
        usage[query] = cursor + 1

    if stats is not None:
        request_stats = [result[2] for result in results.values()]
        unique_urls = {a.source_url for a in selected.values()}
        distinct_patterns: list[int] = []
        for asset in selected.values():
            signature = asset.visual_fingerprint
            if signature is not None and not any(
                    near_duplicate(signature, previous) for previous in distinct_patterns):
                distinct_patterns.append(signature)
        stats.update({
            "scenes_requested": len(wanted),
            "scenes_matched": len(selected),
            "photograph_likely_scenes": sum(
                asset.visual_medium == "photograph-likely" for asset in selected.values()),
            "artwork_likely_scenes": sum(
                asset.visual_medium == "artwork-likely" for asset in selected.values()),
            "unspecified_medium_scenes": sum(
                asset.visual_medium == "unspecified" for asset in selected.values()),
            "unique_queries": len(query_indices),
            "contextual_expansions": sum(
                expand_scenic_search_query(query) != query for query in query_indices),
            "image_variants_budget": sum(limits.values()),
            "image_variants_fetched": sum(len(result[0]) for result in results.values()),
            "secondary_queries_planned": len(alternate_queries),
            "secondary_queries_attempted": sum(
                int(t.get("secondary_queries_attempted", 0)) for t in request_stats),
            "total_commons_api_queries": len(query_indices) + sum(
                int(t.get("secondary_queries_attempted", 0)) for t in request_stats),
            "unique_images_selected": len(unique_urls),
            "scenes_reusing_image": len(selected) - len(unique_urls),
            "scenes_with_perceptual_signature": len(used_signatures),
            "distinct_visual_patterns_estimated": len(distinct_patterns),
            "near_duplicate_candidates_avoided": duplicate_candidates_avoided,
            "scenes_with_crop_estimate": sum(
                a.crop_retention_estimate is not None for a in selected.values()),
            "mean_crop_retention_estimate": round(
                sum(a.crop_retention_estimate for a in selected.values()
                    if a.crop_retention_estimate is not None)
                / max(1, sum(a.crop_retention_estimate is not None
                             for a in selected.values())), 3),
            "workers_used": pool_size,
            "wall_seconds": round(time.perf_counter() - started, 4),
            # Request durations overlap across concurrent workers.
            "api_seconds_sum": round(sum(float(t.get("api_seconds", 0))
                                         for t in request_stats), 4),
            "download_seconds_sum": round(sum(float(t.get("download_seconds", 0))
                                              for t in request_stats), 4),
            "verification_seconds_sum": round(sum(float(t.get("verification_seconds", 0))
                                                  for t in request_stats), 4),
            "download_bytes": sum(int(t.get("download_bytes", 0))
                                  for t in request_stats),
            "download_attempts": sum(int(t.get("download_attempts", 0))
                                     for t in request_stats),
            "fingerprint_seconds_sum": round(sum(
                float(t.get("fingerprint_seconds", 0)) for t in request_stats), 4),
            **semantic_metrics,
        })
    return selected, warnings
