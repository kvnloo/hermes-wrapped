"""Serve the forked Oh My Pi stats client over Hermes and z0live data, or write a static snapshot.

The server binds 127.0.0.1 only and answers only to loopback Host headers, so a page on
another origin cannot rebind a name onto it. Every request reads the stores `mode=ro`.
"""
from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from . import stats_api, stats_hermes, stats_voice

DIST = Path(__file__).resolve().parent / "stats" / "dist"
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/index.js": ("index.js", "text/javascript; charset=utf-8"),
    "/index.css": ("index.css", "text/css; charset=utf-8"),
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
}
LOOPBACK = frozenset({"127.0.0.1", "localhost", "[::1]"})
POLL_S = 3.0
SNAPSHOT_TRACES = 25
SNAPSHOT_ROWS = 2_000
SNAPSHOT_SECTIONS = ("overview", "model-dashboard", "costs", "folders", "tools", "providers", "calls",
                     "frustration")


def default_voice_root() -> Path:
    return Path(os.environ.get("HERMES_MOBILE_VOICE_TRACE_DIR")
                or Path.home() / ".local" / "share" / "z0live" / "voice-traces")


@dataclass(frozen=True)
class Models:
    hermes: stats_hermes.HermesData
    voice: stats_voice.VoiceData
    version: int
    last_write_ms: int


class Source:
    """Read models cached by a fingerprint of their files; reloads when Hermes or z0live writes."""

    def __init__(self, home: Path, voice_root: Path) -> None:
        self.home, self.voice_root = home, voice_root
        self._lock = threading.Lock()
        self._fingerprint: tuple | None = None
        self._models: Models | None = None

    def _scan(self) -> tuple[tuple, int]:
        files = [p for _, db in stats_hermes.stores(self.home) for p in (db, db.with_name(db.name + "-wal"))]
        files += sorted(self.voice_root.glob("*/*.jsonl")) if self.voice_root.is_dir() else []
        seen, newest = [], 0
        for path in files:
            try:
                stat = path.stat()
            except OSError:
                continue
            seen.append((str(path), stat.st_mtime_ns, stat.st_size))
            newest = max(newest, stat.st_mtime_ns)
        return tuple(seen), newest // 1_000_000

    def current(self) -> Models:
        with self._lock:
            fingerprint, newest = self._scan()
            if self._models is None or fingerprint != self._fingerprint:
                version = self._models.version + 1 if self._models else 1
                self._models = Models(stats_hermes.load(stats_hermes.stores(self.home)),
                                      stats_voice.load(self.voice_root), version, newest)
                self._fingerprint = fingerprint
            return self._models

    def status(self) -> dict[str, Any]:
        """LiveStatus. `lastSyncedAt` is the newest write to any source, not the poll time."""
        models = self.current()
        return {"version": models.version, "indexingHours": 0,
                "sync": {"phase": "idle", "current": 0, "total": 0, "processed": len(models.hermes.calls),
                         "lastSyncedAt": models.last_write_ms or None, "error": None}}


def _dumps(payload: Any) -> bytes:
    return json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()


class Handler(BaseHTTPRequestHandler):
    server: "StatsServer"  # pyright: ignore[reportIncompatibleVariableOverride]
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        pass  # request lines carry session keys; keep them out of the terminal

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        name, _, port = host.rpartition(":") if not host.endswith("]") else (host, "", "")
        return name in self.server.hosts and port == str(self.server.server_address[1])

    def _send(self, status: int, body: bytes, kind: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, _dumps(payload), "application/json")

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        if not self._host_ok():
            return self._json(421, {"error": "Unknown host."})
        url = urlsplit(self.path)
        source = self.server.source
        if url.path == "/api/events":
            return self._events()
        if url.path == "/api/status":
            return self._json(200, source.status())
        if url.path.startswith("/api/"):
            models = source.current()
            try:
                payload = stats_api.route(url.path, url.query, models.hermes, models.voice, int(time.time() * 1000))
            except stats_api.NotFound as exc:
                return self._json(404, {"error": str(exc)})
            except stats_api.BadRequest as exc:
                return self._json(400, {"error": str(exc)})
            return self._json(200, payload)
        asset = ASSETS.get(url.path)
        if asset is None:
            return self._json(404, {"error": "Not found."})
        try:
            body = (DIST / asset[0]).read_bytes()
        except OSError:
            return self._json(500, {"error": "Dashboard client is not built; run `bun run build` in stats/."})
        self._send(200, body, asset[1])

    do_HEAD = do_GET

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        if not self._host_ok():
            return self._json(421, {"error": "Unknown host."})
        if urlsplit(self.path).path == "/api/sync":
            return self._json(200, self.server.source.status())
        self._json(404, {"error": "Not found."})

    def _events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            while not self.server.stopping.is_set():
                self.wfile.write(b"data: " + _dumps(self.server.source.status()) + b"\n\n")
                self.wfile.flush()
                self.server.stopping.wait(POLL_S)
        except (BrokenPipeError, ConnectionResetError):
            return


class StatsServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], source: Source) -> None:
        super().__init__(address, Handler)
        self.source = source
        self.stopping = threading.Event()
        self.hosts = LOOPBACK

    def server_close(self) -> None:
        self.stopping.set()
        super().server_close()


def serve(source: Source, port: int = 8800) -> StatsServer:
    return StatsServer(("127.0.0.1", port), source)


# ---------------------------------------------------------------- snapshot

def snapshot_api(models: Models, now_ms: int) -> dict[str, Any]:
    """Every payload the client asks for, keyed the way the client's snapshot shim looks them up."""
    hermes, voice = models.hermes, models.voice
    api: dict[str, Any] = {}

    def put(key: str, path: str, query: str = "") -> None:
        api[key] = stats_api.route(path, query, hermes, voice, now_ms)

    for name in stats_api.RANGES:
        for section in SNAPSHOT_SECTIONS:
            path = f"/api/stats/{section}"
            put(f"{path}?range={name}", path, f"range={name}")
        put(f"/api/stats/errors?range={name}", "/api/stats/errors", f"range={name}&limit={SNAPSHOT_ROWS}")
    put("/api/stats/recent", "/api/stats/recent", f"limit={SNAPSHOT_ROWS}")
    put("/api/sessions", "/api/sessions", "limit=200")
    for row in api["/api/sessions"][:SNAPSHOT_TRACES]:
        put(f"/api/session/trace?file={row['file']}", "/api/session/trace", f"file={row['file']}")
    return api


def build_snapshot(source: Source, now_ms: int | None = None) -> str:
    """One self-contained HTML file: client bundle, styles and pre-computed payloads inline."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    models = source.current()
    payload = {"generatedAt": now_ms, "lastWriteAt": models.last_write_ms or None,
               "api": snapshot_api(models, now_ms)}
    # `</` cannot close the script element once escaped; JSON stays valid.
    data = json.dumps(payload, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    script = (DIST / "index.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    css = (DIST / "index.css").read_text(encoding="utf-8")
    html = (DIST / "index.html").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="index.css">', f"<style>{css}</style>")
    html = html.replace('<link rel="icon" type="image/x-icon" href="favicon.ico">', "")
    return html.replace('<script src="index.js" type="module"></script>',
                        f"<script>window.__HERMES_STATS__={data};</script>\n"
                        f'<script type="module">{script}</script>')


def publish_snapshot(source: Source, target: Path) -> Path:
    """Write atomically with owner-only permissions; a reader never sees half a page."""
    html = build_snapshot(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".stats-", suffix=".html", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(html)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


# ---------------------------------------------------------------- CLI

def setup_cli(parser) -> None:
    parser.add_argument("--port", type=int, default=8800, help="Port to serve on at 127.0.0.1 (default 8800)")
    parser.add_argument("--snapshot", metavar="PATH", help="Write a static snapshot to PATH instead of serving")
    parser.add_argument("--voice-traces", metavar="DIR", help="z0live voice-trace directory")


def run_cli(args, hermes_home: Path) -> int:
    import sys

    voice = Path(args.voice_traces).expanduser() if args.voice_traces else default_voice_root()
    source = Source(stats_hermes.root_of(hermes_home), voice)
    if args.snapshot:
        print(publish_snapshot(source, Path(args.snapshot).expanduser()))
        return 0
    try:
        server = serve(source, args.port)
    except OSError as exc:
        print(f"Could not listen on 127.0.0.1:{args.port}: {exc.strerror or exc}", file=sys.stderr)
        return 1
    print(f"Hermes stats on http://127.0.0.1:{args.port}/  (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
