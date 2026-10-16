"""No-cost visual sources for Free Studio.

Remote media is strictly limited to Wikimedia Commons files marked CC0 or
public domain. Every selected asset carries provenance into the output.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import html
import json
from pathlib import Path
import re
import subprocess
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


def commons_assets(topic: str, work: Path, *, limit: int = 5) -> tuple[list[MediaAsset], list[str]]:
    """Collect verified CC0 / public-domain image thumbnails. No API key needed.

    Failures do not leave incomplete files or break the offline renderer.
    """
    if not 1 <= limit <= 8:
        raise ValueError("Commons media limit out of range")
    query = re.sub(r"\s+", " ", topic).strip()[:110]
    params = parse.urlencode({
        "action": "query", "format": "json", "generator": "search", "gsrsearch": query,
        "gsrnamespace": "6", "gsrlimit": "30", "prop": "imageinfo", "iiprop": "url|extmetadata|size",
        "iiurlwidth": "1440",
    })
    try:
        payload = json.loads(_read_limited(API + "?" + params, 2 * 1024 * 1024, timeout=14))
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        return [], [f"Wikimedia Commons lookup failed: {type(exc).__name__}"]
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
        score = relevance_score(query, title, description)
        if score:
            ranked.append((-score, int(page.get("index", 100000)), page, info, license_name, url, page_url))
    ranked.sort(key=lambda entry: (entry[0], entry[1]))
    for _negscore, _, page, info, license_name, url, page_url in ranked:
        if len(assets) >= limit:
            break
        title = str(page.get("title", ""))
        suffix = Path(title).suffix.lower()
        target = work / f"commons_{len(assets):02}{suffix}"
        try:
            data = _read_limited(url, MAX_IMAGE_BYTES, timeout=25)
            target.write_bytes(data)
            if not media_dimensions(target):
                target.unlink(missing_ok=True)
                continue
        except (OSError, ValueError, subprocess.SubprocessError):
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
                         indices: list[int] | None = None, max_searches: int = 8
                         ) -> tuple[dict[int, MediaAsset], list[str]]:
    """Search each scene independently with hard limits and reusable results.

    Failed searches leave a scene unfilled so it can render a safe graphic
    background instead of silently displaying an unrelated photograph.
    """
    if len(queries) > 18 or max_searches < 1 or max_searches > 18:
        raise ValueError("Commons scene search limit out of range")
    wanted = list(range(len(queries))) if indices is None else list(indices)
    if any(not isinstance(i, int) or i < 0 or i >= len(queries) for i in wanted):
        raise ValueError("Invalid requested scene index")
    fetched: dict[str, MediaAsset | None] = {}
    selected: dict[int, MediaAsset] = {}
    warnings: list[str] = []
    for i in wanted:
        query = queries[i]
        if query not in fetched:
            if len(fetched) >= max_searches:
                warnings.append(f"Scene {i+1}: search budget reached; graphic fallback")
                continue
            directory = work / f"commons_scene_{i:02}"
            directory.mkdir(parents=True, exist_ok=True)
            found, notices = commons_assets(query, directory, limit=1)
            fetched[query] = found[0] if found else None
            warnings.extend(f"Scene {i+1}: {notice}" for notice in notices)
        if fetched[query] is not None:
            selected[i] = fetched[query]
    return selected, warnings
