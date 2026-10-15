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
    for page in sorted(pages.values(), key=lambda p: p.get("index", 100000)):
        if len(assets) >= limit:
            break
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
        if not _trusted_url(url):
            continue
        page_url = str(info.get("descriptionurl") or "")
        if not _trusted_url(page_url, api=True):
            continue
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
        warnings.append("No verified public-domain/CC0 images found; using graphic backgrounds")
    return assets, warnings
