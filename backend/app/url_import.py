"""Import from a link (n°12): a direct link to an audio or video file, or a podcast feed.

Scope and rights: Sténo downloads files that a server hands out as files
(a podcast episode, a conference recording, a file on a public share). It
does not extract videos from platform pages (YouTube…): their terms usually
forbid it, and the user may not hold the rights. A web page is refused with
an explanation; the interface asks the user to import only content they may
use, and the link is kept with the video (`Video.source_url`).

Network safety: the API and the worker sit next to PostgreSQL, Redis and
Ollama. A link to a private or local address is refused, at every redirect,
unless URL_IMPORT_ALLOW_PRIVATE is set (a NAS on the local network).
"""
import ipaddress
import logging
import os
import re
import socket
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from defusedxml import DefusedXmlException
from defusedxml import ElementTree as SafeET

from .config import settings
from .storage import AUDIO_SUFFIXES, VIDEO_SUFFIXES

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 5
FEED_MAX_BYTES = 5 * 1024 * 1024
FEED_MAX_EPISODES = 100
CHUNK = 1024 * 1024
USER_AGENT = "Steno/0.1 (+import local de fichiers audio et video)"
PAGE_MESSAGE = (
    "Ce lien mène à une page web, pas à un fichier audio ou vidéo. Sténo n'extrait pas les vidéos des "
    "plateformes (YouTube…) : si vous avez le droit d'utiliser ce contenu, téléchargez le fichier puis importez-le."
)
CONTENT_TYPES = {
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/aac": ".m4a",
    "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav", "audio/flac": ".flac", "audio/x-flac": ".flac",
    "audio/ogg": ".ogg", "audio/opus": ".opus", "video/mp4": ".mp4", "video/quicktime": ".mov",
    "video/webm": ".webm", "audio/webm": ".webm", "video/x-matroska": ".mkv", "video/ogg": ".ogv",
    "video/x-msvideo": ".avi", "video/x-m4v": ".m4v",
}
FEED_TYPES = {"application/rss+xml", "application/atom+xml", "application/xml", "text/xml", "application/x-rss+xml"}
GENERIC_TYPES = {"", "application/octet-stream", "binary/octet-stream", "application/force-download"}
ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
ATOM = "{http://www.w3.org/2005/Atom}"


class UrlImportError(ValueError):
    """A refused or failed link, with a message for the user."""


def _resolve(host: str) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
    except socket.gaierror as exc:
        raise UrlImportError(f"Adresse introuvable : {host}") from exc


def check_url(url: str) -> str:
    """The URL if it may be fetched; raises UrlImportError otherwise."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UrlImportError("Lien invalide : il doit commencer par http:// ou https://")
    if parts.username or parts.password:
        raise UrlImportError("Les liens contenant un identifiant ou un mot de passe ne sont pas acceptés")
    if not settings.url_import_allow_private:
        for address in _resolve(parts.hostname):
            ip = ipaddress.ip_address(address.split("%")[0])
            if not ip.is_global:
                raise UrlImportError("Les adresses du réseau local ne sont pas acceptées")
    return url.strip()


def _client() -> httpx.Client:
    timeout = httpx.Timeout(settings.download_timeout_seconds, connect=10)
    return httpx.Client(timeout=timeout, follow_redirects=False, headers={"User-Agent": USER_AGENT})


@contextmanager
def open_stream(url: str, client: httpx.Client | None = None):
    """GET `url` as a stream, following redirects one by one, each target checked."""
    own = client is None
    client = client or _client()
    response = None
    try:
        for _ in range(MAX_REDIRECTS + 1):
            url = check_url(url)
            try:
                response = client.send(client.build_request("GET", url), stream=True)
            except httpx.HTTPError as exc:
                raise UrlImportError("Le serveur ne répond pas ; vérifiez le lien") from exc
            if response.is_redirect and response.headers.get("location"):
                url = urljoin(url, response.headers["location"])
                response.close()
                response = None
                continue
            if response.status_code >= 400:
                raise UrlImportError(f"Le serveur a refusé le lien (erreur {response.status_code})")
            yield response
            return
        raise UrlImportError("Trop de redirections")
    finally:
        if response is not None:
            response.close()
        if own:
            client.close()


def _content_type(response: httpx.Response) -> str:
    return response.headers.get("content-type", "").split(";")[0].strip().lower()


def response_filename(response: httpx.Response) -> str:
    """The name the server gives the file, else the last part of the final URL."""
    disposition = response.headers.get("content-disposition", "")
    match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.I) or re.search(r'filename="?([^";]+)"?', disposition, re.I)
    name = unquote(match.group(1)) if match else unquote(Path(urlsplit(str(response.url)).path).name)
    name = os.path.basename(name.replace("\\", "/")).strip()
    return name[:200] or "media"


def media_suffix(response: httpx.Response) -> str | None:
    suffix = Path(response_filename(response)).suffix.lower()
    if suffix in AUDIO_SUFFIXES | VIDEO_SUFFIXES:
        return suffix
    return CONTENT_TYPES.get(_content_type(response))


def _seconds(value: str | None) -> float | None:
    if not value:
        return None
    parts = value.strip().split(":")
    if not all(part.replace(".", "", 1).isdigit() for part in parts) or len(parts) > 3:
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def _date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value.strip()).isoformat()
    except (TypeError, ValueError):
        return value.strip()[:40]


def parse_feed(data: bytes, base_url: str) -> dict:
    """Title and episodes (with an audio or video enclosure) of an RSS or Atom feed."""
    try:
        # defusedxml: no entity expansion nor external resource, the feed comes from anywhere.
        root = SafeET.fromstring(data)
    except DefusedXmlException as exc:
        raise UrlImportError("Flux refusé : il déclare des entités XML") from exc
    except ET.ParseError as exc:
        raise UrlImportError("Ce flux n'est pas un flux RSS ou Atom lisible") from exc
    episodes = []
    if root.tag == "rss" or root.find("channel") is not None:
        channel = root.find("channel")
        title = (channel.findtext("title") if channel is not None else None) or "Podcast"
        items = channel.findall("item") if channel is not None else []
        for item in items:
            enclosure = item.find("enclosure")
            if enclosure is None or not enclosure.get("url"):
                continue
            kind = (enclosure.get("type") or "").lower()
            if kind and not kind.startswith(("audio/", "video/")):
                continue
            length = enclosure.get("length") or ""
            episodes.append({
                "title": (item.findtext("title") or "Épisode").strip()[:200],
                "url": urljoin(base_url, enclosure.get("url").strip()),
                "published": _date(item.findtext("pubDate")),
                "duration_seconds": _seconds(item.findtext(f"{ITUNES}duration")),
                "size_bytes": int(length) if length.isdigit() and int(length) > 0 else None,
                "content_type": kind or None,
            })
    elif root.tag == f"{ATOM}feed":
        title = root.findtext(f"{ATOM}title") or "Flux"
        for entry in root.findall(f"{ATOM}entry"):
            link = next((node for node in entry.findall(f"{ATOM}link") if node.get("rel") == "enclosure" and node.get("href")), None)
            if link is None:
                continue
            length = link.get("length") or ""
            episodes.append({
                "title": (entry.findtext(f"{ATOM}title") or "Épisode").strip()[:200],
                "url": urljoin(base_url, link.get("href").strip()),
                "published": entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated"),
                "duration_seconds": None,
                "size_bytes": int(length) if length.isdigit() and int(length) > 0 else None,
                "content_type": link.get("type"),
            })
    else:
        raise UrlImportError("Ce flux n'est pas un flux RSS ou Atom lisible")
    if not episodes:
        raise UrlImportError("Ce flux ne contient aucun épisode audio ou vidéo")
    return {"kind": "feed", "title": title.strip()[:200], "episodes": episodes[:FEED_MAX_EPISODES]}


def _read_limited(response: httpx.Response, limit: int) -> bytes:
    data = bytearray()
    for chunk in response.iter_bytes():
        data.extend(chunk)
        if len(data) > limit:
            raise UrlImportError("Flux trop volumineux")
    return bytes(data)


def probe(url: str, client: httpx.Client | None = None) -> dict:
    """What the link points to: a media file (name, size) or a feed (its episodes)."""
    with open_stream(url, client) as response:
        final_url = str(response.url)
        kind = _content_type(response)
        suffix = media_suffix(response)
        if suffix and kind not in FEED_TYPES:
            size = response.headers.get("content-length", "")
            name = response_filename(response)
            if Path(name).suffix.lower() != suffix:
                name = f"{Path(name).stem or 'media'}{suffix}"
            return {
                "kind": "media", "url": final_url, "title": Path(name).stem, "filename": name,
                "size_bytes": int(size) if size.isdigit() else None, "content_type": kind or None,
            }
        if kind in FEED_TYPES or kind in GENERIC_TYPES or kind == "text/plain":
            return parse_feed(_read_limited(response, FEED_MAX_BYTES), final_url)
        if kind in ("text/html", "application/xhtml+xml"):
            raise UrlImportError(PAGE_MESSAGE)
        raise UrlImportError("Ce lien ne mène pas à un fichier audio ou vidéo pris en charge")


def download(url: str, folder: Path, stem: str, *, on_progress=None, client: httpx.Client | None = None) -> tuple[Path, str]:
    """Download the media into `folder/<stem><suffix>`; returns the path and the server's file name."""
    with open_stream(url, client) as response:
        if _content_type(response) in ("text/html", "application/xhtml+xml"):
            raise UrlImportError(PAGE_MESSAGE)
        suffix = media_suffix(response)
        if suffix is None:
            raise UrlImportError("Ce lien ne mène pas à un fichier audio ou vidéo pris en charge")
        length = response.headers.get("content-length", "")
        total = int(length) if length.isdigit() else None
        if total and total > settings.max_download_bytes:
            raise UrlImportError(f"Fichier trop volumineux (limite : {settings.max_download_bytes // 1024 ** 2} Mo)")
        folder.mkdir(parents=True, exist_ok=True)
        final = folder / f"{stem}{suffix}"
        partial = folder / f".{stem}.download"
        done = 0
        try:
            with partial.open("wb") as out:
                for chunk in response.iter_bytes(CHUNK):
                    done += len(chunk)
                    if done > settings.max_download_bytes:
                        raise UrlImportError(f"Fichier trop volumineux (limite : {settings.max_download_bytes // 1024 ** 2} Mo)")
                    out.write(chunk)
                    if on_progress:
                        on_progress(done, total)
            if done == 0:
                raise UrlImportError("Le fichier téléchargé est vide")
            os.replace(partial, final)
        except httpx.HTTPError as exc:
            raise UrlImportError("Le téléchargement a été interrompu") from exc
        finally:
            partial.unlink(missing_ok=True)
        name = response_filename(response)
        if Path(name).suffix.lower() != suffix:
            name = f"{Path(name).stem or 'media'}{suffix}"
        return final, name
