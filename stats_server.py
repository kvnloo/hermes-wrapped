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
from urllib.parse import parse_qs, urlsplit

from . import stats_api, stats_hermes, stats_insights, stats_narrate, stats_voice

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
NARRATE_RANGES = ("24h", "7d", "30d")
MAX_BODY = 8 * 1024


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


def insights(models: Models, range_name: str, now_ms: int) -> tuple[dict, dict]:
    """(report, LLM payload) for one range. Raises stats_api.BadRequest on an unknown range."""
    w = stats_api.window(range_name, now_ms)
    report = stats_insights.report(models.hermes, models.voice, w, range_name, now_ms)
    return report, stats_insights.llm_payload(report, models.hermes, models.voice, w)


def default_cache() -> stats_narrate.Cache:
    from .store import data_dir

    return stats_narrate.Cache(data_dir() / "insights-cache")


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
            now = int(time.time() * 1000)
            try:
                if url.path == "/api/stats/insights":
                    payload: Any = insights(models, self._range(url.query), now)[0]
                elif url.path == "/api/insights/narrative":
                    report_payload = insights(models, self._range(url.query), now)[1]
                    payload = stats_narrate.narrate(report_payload, self.server.llm, self.server.cache,
                                                    cached_only=True)
                else:
                    payload = stats_api.route(url.path, url.query, models.hermes, models.voice, now)
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

    @staticmethod
    def _range(query: str) -> str:
        return parse_qs(query).get("range", ["7d"])[0]

    def _origin_ok(self) -> bool:
        """A browser POST from another site carries its Origin; only this dashboard's own may spend tokens."""
        origin = self.headers.get("Origin")
        port = self.server.server_address[1]
        return origin is None or origin in {f"http://{host}:{port}" for host in self.server.hosts}

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        if not self._host_ok():
            return self._json(421, {"error": "Unknown host."})
        url = urlsplit(self.path)
        if url.path == "/api/sync":
            return self._json(200, self.server.source.status())
        if url.path not in ("/api/insights/narrative", "/api/insights/ask"):
            return self._json(404, {"error": "Not found."})
        # A cross-site form cannot send JSON without a CORS preflight, which this server never grants.
        if not self._origin_ok():
            return self._json(403, {"error": "Cross-origin request refused."})
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            return self._json(415, {"error": "Send JSON."})
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 <= length <= MAX_BODY:
            return self._json(413, {"error": "Request too large."})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json(400, {"error": "Body is not JSON."})
        if not isinstance(body, dict):
            return self._json(400, {"error": "Body must be a JSON object."})
        range_name = body.get("range") if isinstance(body.get("range"), str) else self._range(url.query)
        try:
            payload = insights(self.server.source.current(), range_name, int(time.time() * 1000))[1]
        except stats_api.BadRequest as exc:
            return self._json(400, {"error": str(exc)})
        server = self.server
        if url.path == "/api/insights/narrative":
            return self._json(200, stats_narrate.narrate(payload, server.llm, server.cache))
        question = body.get("question")
        if not isinstance(question, str):
            return self._json(400, {"error": "question is required."})
        self._json(200, stats_narrate.ask(payload, question, server.llm, server.cache))

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

    def __init__(self, address: tuple[str, int], source: Source, llm: stats_narrate.Llm | None = None,
                 cache: stats_narrate.Cache | None = None) -> None:
        super().__init__(address, Handler)
        self.source = source
        self.llm = llm  # None: the narrative and the ask box say no model is configured
        self.cache = cache or stats_narrate.Cache(None)
        self.stopping = threading.Event()
        self.hosts = LOOPBACK

    def server_close(self) -> None:
        self.stopping.set()
        super().server_close()


def serve(source: Source, port: int = 8800, llm: stats_narrate.Llm | None = None,
          cache: stats_narrate.Cache | None = None) -> StatsServer:
    return StatsServer(("127.0.0.1", port), source, llm, cache)


# ---------------------------------------------------------------- snapshot

def snapshot_api(models: Models, now_ms: int, llm: stats_narrate.Llm | None = None,
                 cache: stats_narrate.Cache | None = None) -> dict[str, Any]:
    """Every payload the client asks for, keyed the way the client's snapshot shim looks them up.
    With an `llm`, the narrative for NARRATE_RANGES is generated (or read from `cache`) and inlined."""
    hermes, voice = models.hermes, models.voice
    api: dict[str, Any] = {}

    def put(key: str, path: str, query: str = "") -> None:
        api[key] = stats_api.route(path, query, hermes, voice, now_ms)

    linked: list[str] = []
    for name in stats_api.RANGES:
        for section in SNAPSHOT_SECTIONS:
            path = f"/api/stats/{section}"
            put(f"{path}?range={name}", path, f"range={name}")
        put(f"/api/stats/errors?range={name}", "/api/stats/errors", f"range={name}&limit={SNAPSHOT_ROWS}")
        report, payload = insights(models, name, now_ms)
        api[f"/api/stats/insights?range={name}"] = report
        linked += [parse_qs(f["link"].partition("?")[2]).get("s", [""])[0] for f in report["findings"]]
        if llm is not None and name in NARRATE_RANGES:
            api[f"/api/insights/narrative?range={name}"] = stats_narrate.narrate(
                payload, llm, cache or stats_narrate.Cache(None))
    put("/api/stats/recent", "/api/stats/recent", f"limit={SNAPSHOT_ROWS}")
    put("/api/sessions", "/api/sessions", "limit=200")
    # The newest traces, plus every trace a finding links to.
    for key in dict.fromkeys([row["file"] for row in api["/api/sessions"][:SNAPSHOT_TRACES]] + linked):
        if key:
            put(f"/api/session/trace?file={key}", "/api/session/trace", f"file={key}")
    return api


def build_snapshot(source: Source, now_ms: int | None = None, llm: stats_narrate.Llm | None = None,
                   cache: stats_narrate.Cache | None = None) -> str:
    """One self-contained HTML file: client bundle, styles and pre-computed payloads inline."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    models = source.current()
    payload = {"generatedAt": now_ms, "lastWriteAt": models.last_write_ms or None,
               "api": snapshot_api(models, now_ms, llm, cache)}
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


def publish_snapshot(source: Source, target: Path, llm: stats_narrate.Llm | None = None,
                     cache: stats_narrate.Cache | None = None) -> Path:
    """Write atomically with owner-only permissions; a reader never sees half a page."""
    html = build_snapshot(source, llm=llm, cache=cache)
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

def add_llm_args(parser) -> None:
    parser.add_argument("--llm-url", metavar="URL",
                        help="OpenAI-compatible endpoint for summaries, e.g. http://127.0.0.1:8080/v1 (a llama-server). "
                             "Default: your Hermes auxiliary model, auxiliary.wrapped_insights in config.yaml")
    parser.add_argument("--llm-model", default="", help="Model name to send to --llm-url")
    parser.add_argument("--llm-key-env", default="", metavar="VAR", help="Env var holding a key for --llm-url")


def llm_from_args(args) -> stats_narrate.Llm | None:
    return stats_narrate.pick_llm(args.llm_url, args.llm_model, args.llm_key_env)


def setup_cli(parser) -> None:
    parser.add_argument("--port", type=int, default=8800, help="Port to serve on at 127.0.0.1 (default 8800)")
    parser.add_argument("--snapshot", metavar="PATH", help="Write a static snapshot to PATH instead of serving")
    parser.add_argument("--narrate", action="store_true",
                        help="With --snapshot: write the summaries for 24h, 7d and 30d into it (sends only the "
                             "aggregates and findings to the model)")
    parser.add_argument("--voice-traces", metavar="DIR", help="z0live voice-trace directory")
    add_llm_args(parser)


def insights_text(hermes_home: Path, range_name: str = "7d", llm: stats_narrate.Llm | None = None,
                  narrate: bool = False, voice_root: Path | None = None, as_json: bool = False) -> str:
    """`/wrapped insights` and `hermes wrapped insights`. Without `narrate` no model is called: a summary
    shows only when one for this exact input is already cached."""
    source = Source(stats_hermes.root_of(hermes_home), voice_root or default_voice_root())
    report, payload = insights(source.current(), range_name, int(time.time() * 1000))
    narrative = stats_narrate.narrate(payload, llm, default_cache(), cached_only=not narrate) if llm else None
    if as_json:
        return json.dumps({"report": report, "narrative": narrative}, indent=2)
    return stats_insights.as_text(report, narrative)


def setup_insights_cli(parser) -> None:
    parser.add_argument("--range", default="7d", choices=list(stats_api.RANGES), help="Window (default 7d)")
    parser.add_argument("--narrate", action="store_true",
                        help="Also write a summary with a model (sends only the aggregates and findings)")
    parser.add_argument("--json", action="store_true", help="Print the findings, checks and summary as JSON")
    parser.add_argument("--voice-traces", metavar="DIR", help="z0live voice-trace directory")
    add_llm_args(parser)


def run_insights_cli(args, hermes_home: Path) -> int:
    import sys

    llm = llm_from_args(args)
    if args.narrate and llm is None:
        print("--narrate needs a model: run inside Hermes or pass --llm-url.", file=sys.stderr)
        return 1
    voice = Path(args.voice_traces).expanduser() if args.voice_traces else None
    print(insights_text(hermes_home, args.range, llm, args.narrate, voice, args.json))
    return 0


def run_cli(args, hermes_home: Path) -> int:
    import sys

    voice = Path(args.voice_traces).expanduser() if args.voice_traces else default_voice_root()
    source = Source(stats_hermes.root_of(hermes_home), voice)
    llm = llm_from_args(args)
    if args.snapshot:
        if args.narrate and llm is None:
            print("--narrate needs a model: run inside Hermes or pass --llm-url.", file=sys.stderr)
            return 1
        print(publish_snapshot(source, Path(args.snapshot).expanduser(), llm if args.narrate else None,
                               default_cache()))
        return 0
    try:
        server = serve(source, args.port, llm, default_cache())
    except OSError as exc:
        print(f"Could not listen on 127.0.0.1:{args.port}: {exc.strerror or exc}", file=sys.stderr)
        return 1
    print(f"Hermes stats on http://127.0.0.1:{args.port}/  (Ctrl-C to stop)", flush=True)
    print(f"Summaries: {llm.identity if llm else 'off (no model; run inside Hermes or pass --llm-url)'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
