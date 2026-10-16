"""No-cost visual sources for Free Studio.

Remote media is strictly limited to Wikimedia Commons files marked CC0 or
public domain. Every selected asset carries provenance into the output.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
import html
import json
from pathlib import Path
import re
import subprocess
import time
import unicodedata
from urllib import parse, request

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

    def metadata(self) -> dict[str, str]:
        result = asdict(self)
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
    value = info.get("extmetadata", {}).get(name, {})
    return str(value.get("value", "")) if isinstance(value, dict) else ""


def _trusted_url(url: str, *, api: bool = False) -> bool:
    try:
        parsed = parse.urlsplit(url)
        return (parsed.scheme == "https" and not parsed.username and not parsed.password
                and parsed.port in (None, 443) and not parsed.fragment
                and parsed.hostname == ("commons.wikimedia.org" if api else "upload.wikimedia.org"))
    except ValueError:
        return False


def _read_limited(url: str, max_bytes: int, *, timeout: int) -> bytes:
    if not _trusted_url(url, api=(url == API or url.startswith(API + "?"))):
        raise ValueError("Media URL has an unexpected host")
    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    with request.urlopen(req, timeout=timeout) as response:
        if not _trusted_url(response.geturl(), api=(url == API or url.startswith(API + "?"))):
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


def relevance_score(query: str, title: str, description: str = "") -> int:
    """Transparent filename/description keyword score, not AI similarity.

    Zero score means we cannot justify using this image for the scene.
    Exact words are preferred over guessed semantics; users can override
    scene search terms for improved Wikimedia matching.
    """
    wanted = _words(query) - _words(" ".join(_SEARCH_STOPWORDS))
    if not wanted:
        wanted = _words(query)
    title_words = _words(title.replace("File:", ""))
    desc_words = _words(description[:400])
    return 3 * len(wanted & title_words) + len(wanted & desc_words)


# A single ambiguous landscape noun can also be someone's family name, a
# painting title, or a biography. Do not treat matching the surname alone as
# evidence the picture illustrates the place. Prefer graphic fallback.
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
    """Conservative extra filter for *single-word* landscape requests.

    A portrait of someone named 'Rivière' is NOT a river photograph. Scene
    relevance remains deterministic, and a missed image is safer than a
    misleading illustration. Non-scenic queries keep ordinary relevance.
    """
    score = relevance_score(query, title, description)
    if score <= 0:
        return 0
    request_words = _words(query) - _words(" ".join(_SEARCH_STOPWORDS))
    if len(request_words) != 1 or not (request_words & _SCENIC_SINGLE_QUERY_WORDS):
        return score
    content_words = _words(title + " " + description)
    if content_words & _NON_SCENIC_SUBJECT_WORDS:
        return 0
    if not ((content_words - request_words) & _SCENIC_CONTEXT_WORDS):
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


def commons_assets(topic: str, work: Path, *, limit: int = 5,
                   timings: dict[str, float | int] | None = None) -> tuple[list[MediaAsset], list[str]]:
    """Collect verified CC0 / public-domain image thumbnails. No API key needed.

    Failures do not leave incomplete files or break the offline renderer.
    """
    if not 1 <= limit <= 8:
        raise ValueError("Commons media limit out of range")
    query = re.sub(r"\s+", " ", topic).strip()[:110]
    search_phrase = expand_scenic_search_query(query)
    params = parse.urlencode({
        "action": "query", "format": "json", "generator": "search", "gsrsearch": search_phrase,
        "gsrnamespace": "6", "gsrlimit": "30", "prop": "imageinfo", "iiprop": "url|extmetadata|size",
        "iiurlwidth": "1440",
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
    ranked: list[tuple[int, int, dict, dict, str, str, str]] = []
    for page in pages.values():
        title = str(page.get("title", ""))
        if Path(title).suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        info_list = page.get("imageinfo") or []
        if not info_list:
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
            ranked.append((-score, int(page.get("index", 100000)), page, info, license_name, url, page_url))
    ranked.sort(key=lambda entry: (entry[0], entry[1]))
    for _negscore, _, page, info, license_name, url, page_url in ranked:
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
        assets.append(MediaAsset(target, "image", page_url, license_name, author, title))
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


def commons_scene_assets(queries: list[str], work: Path, *,
                         indices: list[int] | None = None, max_searches: int = 8,
                         max_workers: int = 3,
                         stats: dict[str, float | int] | None = None
                         ) -> tuple[dict[int, MediaAsset], list[str]]:
    """Search independent scenes concurrently with strict budgets and safe fallbacks.

    Up to three Wikimedia requests execute in parallel, not eighteen. Repeated
    queries are downloaded only once. The returned warnings and scene indices
    follow the original storyboard order regardless of completion order.
    """
    if len(queries) > 18 or not 1 <= max_searches <= 18:
        raise ValueError("Commons scene search limit out of range")
    if not isinstance(max_workers, int) or not 1 <= max_workers <= 3:
        raise ValueError("Commons concurrency must be between 1 and 3")
    wanted = list(range(len(queries))) if indices is None else list(indices)
    if any(not isinstance(i, int) or i < 0 or i >= len(queries) for i in wanted):
        raise ValueError("Invalid requested scene index")

    started = time.perf_counter()
    # Preallocate a deterministic query budget before starting any threads.
    first_indices: dict[str, int] = {}
    budget_exceeded: set[int] = set()
    for i in wanted:
        query = queries[i]
        if query not in first_indices:
            if len(first_indices) >= max_searches:
                budget_exceeded.add(i)
                continue
            first_indices[query] = i

    def fetch(query: str, index: int
              ) -> tuple[MediaAsset | None, list[str], dict[str, float | int]]:
        directory = work / f"commons_scene_{index:02}"
        directory.mkdir(parents=True, exist_ok=True)
        query_timings: dict[str, float | int] = {}
        assets, notices = commons_assets(query, directory, limit=1, timings=query_timings)
        return (assets[0] if assets else None), notices, query_timings

    results: dict[str, tuple[MediaAsset | None, list[str], dict[str, float | int]]] = {}
    pool_size = min(max_workers, len(first_indices))
    if pool_size:
        with ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix="commons") as pool:
            futures = {q: pool.submit(fetch, q, i) for q, i in first_indices.items()}
            for query in first_indices:
                try:
                    results[query] = futures[query].result()
                except Exception as exc:
                    # A malformed Wikimedia response or one failed request must
                    # never abort the other scenes or the offline background.
                    results[query] = (None, [f"Wikimedia scene lookup failed: {type(exc).__name__}"], {})

    selected: dict[int, MediaAsset] = {}
    warnings: list[str] = []
    for i in wanted:
        query = queries[i]
        if i in budget_exceeded or query not in results:
            warnings.append(f"Scene {i+1}: search budget reached; graphic fallback")
            continue
        asset, notices, _ = results[query]
        if i == first_indices[query]:
            warnings.extend(f"Scene {i+1}: {notice}" for notice in notices)
        if asset is not None:
            selected[i] = asset

    if stats is not None:
        request_stats = [result[2] for result in results.values()]
        stats.update({
            "scenes_requested": len(wanted),
            "scenes_matched": len(selected),
            "unique_queries": len(first_indices),
            "contextual_expansions": sum(expand_scenic_search_query(q) != q for q in first_indices),
            "workers_used": pool_size,
            "wall_seconds": round(time.perf_counter() - started, 4),
            # These are sums of task durations and can exceed wall_seconds
            # because independent requests run at the same time.
            "api_seconds_sum": round(sum(float(t.get("api_seconds", 0)) for t in request_stats), 4),
            "download_seconds_sum": round(sum(float(t.get("download_seconds", 0)) for t in request_stats), 4),
            "verification_seconds_sum": round(sum(float(t.get("verification_seconds", 0)) for t in request_stats), 4),
            "download_bytes": sum(int(t.get("download_bytes", 0)) for t in request_stats),
            "download_attempts": sum(int(t.get("download_attempts", 0)) for t in request_stats),
        })
    return selected, warnings
