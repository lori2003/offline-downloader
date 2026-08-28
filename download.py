#!/usr/bin/env python3
"""Download an authorised Vixcloud video into a local directory."""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

import yt_dlp


ALLOWED_DOMAINS = ("vixcloud.co",)
HTML_LIMIT_BYTES = 2 * 1024 * 1024
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0 Safari/537.36"
)

TOKEN_RE = re.compile(r"['\"]token['\"]\s*:\s*['\"]([\w-]+)['\"]")
EXPIRES_RE = re.compile(r"['\"]expires['\"]\s*:\s*['\"]?(\d+)['\"]?")
STREAM_URL_RE = re.compile(r"\burl\s*:\s*['\"]([^'\"\r\n]+)['\"]")
FHD_RE = re.compile(r"window\.canPlayFHD\s*=\s*true", re.IGNORECASE)
SECRET_QUERY_RE = re.compile(r"(?i)(token|expires)=([^&\s]+)")
ENCODED_LINE_BREAK_RE = re.compile(r"%0[ad]", re.IGNORECASE)
INVISIBLE_WHITESPACE_RE = re.compile(r"[\s\u200b-\u200d\ufeff]+")


class URLValidationError(ValueError):
    """Raised when the supplied URL is not a supported public web URL."""


class DownloadResultError(RuntimeError):
    """Raised when yt-dlp reports success but produces no media file."""


@dataclass(frozen=True)
class ResolvedVideo:
    url: str
    referer: str
    extracted_from_player: bool


class DownloadProgress:
    """Print compact, unbuffered progress updates suitable for GitHub logs."""

    def __init__(self) -> None:
        self._last_bucket = -1
        self._last_update = 0.0

    def __call__(self, data: dict) -> None:
        status = data.get("status")
        if status == "finished":
            print("AVANZAMENTO: 100% — download completato, preparo il file...", flush=True)
            return
        if status != "downloading":
            return

        downloaded = data.get("downloaded_bytes") or 0
        total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
        fragment_index = data.get("fragment_index") or 0
        fragment_count = data.get("fragment_count") or 0

        current = downloaded
        maximum = total
        unit_label = ""
        if not maximum and fragment_count:
            current = fragment_index
            maximum = fragment_count
            unit_label = f" | frammento {fragment_index}/{fragment_count}"

        now = time.monotonic()
        if maximum:
            percent = min(100.0, current * 100 / maximum)
            bucket = int(percent // 5)
            if bucket == self._last_bucket and now - self._last_update < 30:
                return
            self._last_bucket = bucket
            percent_label = f"{percent:5.1f}%"
        else:
            if now - self._last_update < 30:
                return
            percent_label = "in corso"

        self._last_update = now
        details = []
        if downloaded:
            size_label = _format_bytes(downloaded)
            if total:
                size_label += f" / {_format_bytes(total)}"
            details.append(size_label)
        if data.get("speed"):
            details.append(f"{_format_bytes(data['speed'])}/s")
        if data.get("eta") is not None:
            details.append(f"restano {_format_duration(data['eta'])}")

        suffix = f" | {' | '.join(details)}" if details else ""
        print(f"AVANZAMENTO: {percent_label}{unit_label}{suffix}", flush=True)


class RedactingLogger:
    """Keep expiring Vixcloud tokens out of normal and error logs."""

    @staticmethod
    def debug(_message: str) -> None:
        return

    @staticmethod
    def info(_message: str) -> None:
        return

    @staticmethod
    def warning(message: str) -> None:
        print(f"AVVISO yt-dlp: {_redact_secrets(message)}", file=sys.stderr, flush=True)

    @staticmethod
    def error(message: str) -> None:
        print(f"ERRORE yt-dlp: {_redact_secrets(message)}", file=sys.stderr, flush=True)


def _is_allowed_hostname(hostname: str) -> bool:
    hostname = hostname.lower().rstrip(".")
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in ALLOWED_DOMAINS
    )


def normalize_video_url(value: str) -> str:
    """Remove line wraps accidentally introduced while copying a player URL."""
    without_whitespace = INVISIBLE_WHITESPACE_RE.sub("", value)
    return ENCODED_LINE_BREAK_RE.sub("", without_whitespace)


def validate_video_url(value: str) -> str:
    """Return a normalised Vixcloud URL or raise a readable error."""
    url = normalize_video_url(value)
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise URLValidationError("L'URL deve iniziare con http:// o https://.")
    if not parsed.hostname or not _is_allowed_hostname(parsed.hostname):
        raise URLValidationError(
            "Sono accettati solo vixcloud.co e i suoi sottodomini."
        )
    if parsed.username or parsed.password:
        raise URLValidationError("L'URL non può contenere credenziali incorporate.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise URLValidationError("La porta indicata nell'URL non è valida.") from exc
    if port not in {None, 80, 443}:
        raise URLValidationError("Sono ammesse solo le porte web standard 80 e 443.")

    return url


def fetch_player_html(url: str) -> str:
    """Fetch a small Vixcloud player page without accepting an unbounded body."""
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": USER_AGENT,
        },
    )
    with urlopen(request, timeout=30) as response:
        body = response.read(HTML_LIMIT_BYTES + 1)
        if len(body) > HTML_LIMIT_BYTES:
            raise DownloadResultError("La pagina del player è troppo grande.")
        charset = response.headers.get_content_charset() or "utf-8"
    return body.decode(charset, errors="replace")


def _build_manifest_url(
    stream_url: str, token: str, expires: str, full_hd: bool
) -> str:
    parsed = urlparse(stream_url)
    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in {"token", "expires", "h"}
    ]
    query_items.extend((("token", token), ("expires", expires)))
    if full_hd:
        query_items.append(("h", "1"))
    return urlunparse(parsed._replace(query=urlencode(query_items)))


def resolve_video_url(
    url: str, fetch_html: Callable[[str], str] = fetch_player_html
) -> ResolvedVideo:
    """Resolve the signed HLS URL embedded in a Vixcloud player page.

    Direct playlist URLs are passed through. If the page shape is unknown, yt-dlp
    receives the original URL so its generic extractor still gets a chance.
    """
    url = validate_video_url(url)
    path = urlparse(url).path.lower()
    if path.endswith(".m3u8") or "/playlist/" in path:
        return ResolvedVideo(url=url, referer=url, extracted_from_player=False)

    player_html = fetch_html(url)
    token_match = TOKEN_RE.search(player_html)
    expires_match = EXPIRES_RE.search(player_html)
    stream_match = STREAM_URL_RE.search(player_html)

    if not (token_match and expires_match and stream_match):
        return ResolvedVideo(url=url, referer=url, extracted_from_player=False)

    raw_stream_url = html.unescape(stream_match.group(1)).replace(r"\/", "/")
    stream_url = urljoin(url, raw_stream_url)
    validate_video_url(stream_url)
    manifest_url = _build_manifest_url(
        stream_url,
        token_match.group(1),
        expires_match.group(1),
        bool(FHD_RE.search(player_html)),
    )
    return ResolvedVideo(
        url=manifest_url,
        referer=url,
        extracted_from_player=True,
    )


def _redact_secrets(message: str) -> str:
    return SECRET_QUERY_RE.sub(r"\1=***", message)


def _format_bytes(value: float) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def _format_duration(seconds: float) -> str:
    remaining = max(0, int(seconds))
    hours, remainder = divmod(remaining, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def download_video(url: str, output_dir: str | Path = "downloads") -> list[Path]:
    """Download one Vixcloud video and return the media files produced."""
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    before = {path.resolve() for path in destination.rglob("*") if path.is_file()}

    try:
        resolved = resolve_video_url(url)
    except URLValidationError:
        raise
    except Exception:
        print(
            "Avviso: non è stato possibile leggere prima il player; "
            "provo con l'estrattore generico."
        )
        original_url = validate_video_url(url)
        resolved = ResolvedVideo(
            url=original_url,
            referer=original_url,
            extracted_from_player=False,
        )

    origin = urlparse(resolved.referer)
    http_headers = {
        "Origin": f"{origin.scheme}://{origin.netloc}",
        "Referer": resolved.referer,
        "User-Agent": USER_AGENT,
    }
    progress = DownloadProgress()
    ydl_options = {
        "continuedl": True,
        "concurrent_fragment_downloads": 4,
        "format": "bv*+ba/b",
        "fragment_retries": 10,
        "http_headers": http_headers,
        "ignoreconfig": True,
        "logger": RedactingLogger(),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "outtmpl": str(destination / "%(title).150B [%(id)s].%(ext)s"),
        "overwrites": False,
        "progress_hooks": [progress],
        "quiet": True,
        "retries": 10,
        "socket_timeout": 30,
        "windowsfilenames": True,
    }

    print(
        "Player Vixcloud risolto. Avvio il download..."
        if resolved.extracted_from_player
        else "URL Vixcloud pronto. Avvio il download...",
        flush=True,
    )
    with yt_dlp.YoutubeDL(ydl_options) as ydl:
        ydl.extract_info(resolved.url, download=True)

    ignored_suffixes = {".part", ".ytdl", ".json"}
    produced = sorted(
        path
        for path in destination.rglob("*")
        if path.is_file()
        and path.resolve() not in before
        and path.suffix.lower() not in ignored_suffixes
    )
    if not produced:
        raise DownloadResultError("Il download non ha prodotto alcun file multimediale.")

    for path in produced:
        print(f"COMPLETATO: {path.name}")
    return produced


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Scarica un video Vixcloud che puoi legalmente conservare offline."
        )
    )
    parser.add_argument("url", help="URL del player o della playlist Vixcloud")
    parser.add_argument(
        "--output-dir",
        default="downloads",
        help="Cartella di destinazione (predefinita: downloads)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    normalised_url = normalize_video_url(args.url)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::add-mask::{normalised_url}", flush=True)
    try:
        download_video(normalised_url, args.output_dir)
    except (URLValidationError, DownloadResultError, yt_dlp.utils.DownloadError) as exc:
        print(f"ERRORE: {_redact_secrets(str(exc))}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Download interrotto.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
