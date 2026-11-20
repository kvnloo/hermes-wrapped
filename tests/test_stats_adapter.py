"""Stats dashboard adapter: Hermes state.db and z0live traces -> the omp stats API shapes.

Offline and stdlib-only. Run: python3 -m unittest discover -s tests
"""

import hashlib
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _load import load  # noqa: E402

load()
from hermes_wrapped import stats_api, stats_hermes, stats_insights, stats_narrate, stats_server, stats_voice  # noqa: E402

SECRET = "SECRET-PROMPT-TEXT"
NOW = int(time.time())

SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, model TEXT, billing_provider TEXT,
    parent_session_id TEXT, started_at REAL NOT NULL, ended_at REAL, cwd TEXT,
    title TEXT, system_prompt TEXT);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT,
    tool_name TEXT, timestamp REAL, finish_reason TEXT, reasoning TEXT, api_content TEXT,
    _compressed_summary INTEGER, display_kind TEXT, display_metadata TEXT, message_uid TEXT);
CREATE TABLE session_model_usage (
    session_id TEXT, model TEXT, billing_provider TEXT, task TEXT, api_call_count INTEGER,
    input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
    cache_write_tokens INTEGER, reasoning_tokens INTEGER, estimated_cost_usd REAL,
    actual_cost_usd REAL, first_seen REAL, last_seen REAL);
"""


def _store(path, sessions, messages, usage):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?)",
                   [s + (SECRET, SECRET) for s in sessions])
    # A row's message_uid is "<session>-<id>" unless the row names the message it copies.
    db.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   [(m[0], m[1], m[2], SECRET, SECRET, m[3], m[4], m[5], SECRET, SECRET, m[6], m[7], m[8],
                     m[9] if len(m) > 9 else f"{m[1]}-{m[0]}") for m in messages])
    db.executemany("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", usage)
    db.commit()
    db.close()


def _hermes(root):
    """Two profiles. Expected all-time: 7 requests, 1 failed, $1.36, 3,000 cache-read tokens."""
    _store(root / "state.db",
           sessions=[("s1", "cli", "m-main", "prov", None, NOW - 3600, NOW - 600, "/work/alpha"),
                     ("s2", "cli", "m-main", "prov", None, NOW - 40 * 86400, None, None)],
           messages=[
               (1, "s1", "user", None, NOW - 3000, None, 0, None, None),
               (2, "s1", "assistant", None, NOW - 2990, "tool_calls", 0, None, None),
               (3, "s1", "tool", "terminal", NOW - 2985, None, 0, None, None),
               (4, "s1", "tool", "read_file", NOW - 2984, None, 0, None, None),
               (5, "s1", "assistant", None, NOW - 2980, "stop", 0, None, None),
               (6, "s1", "user", None, NOW - 2000, None, 0, None, None),
               (7, "s1", "assistant", None, NOW - 1990, None, 0, "failed_turn", None),
               (8, "s1", "assistant", None, NOW - 1500, "stop", 1, None, None),  # compressed: not a call
               (9, "s2", "user", None, NOW - 40 * 86400, None, 0, None, None),
               (10, "s2", "assistant", None, NOW - 40 * 86400 + 7200, "stop", 0, None,
                json.dumps({"interrupted": True, "note": SECRET})),
               # Compaction carried messages 5 and 3 forward: same uid and time, a later row.
               (11, "s1", "assistant", None, NOW - 2980, "stop", 0, None, None, "s1-5"),
               (12, "s1", "tool", "terminal", NOW - 2985, None, 0, None, None, "s1-3"),
           ],
           usage=[("s1", "m-main", "prov", "", 3, 300, 30, 3000, 0, 6, 0.30, 0, NOW - 3000, NOW - 1990),
                  ("s1", "m-small", "prov", "title_generation", 2, 50, 5, 0, 0, 0, 0.02, 0.01,
                   NOW - 2900, NOW - 2900),
                  ("s2", "m-main", "prov", "", 1, 10, 1, 0, 0, 0, 0.05, 0, None, None)])
    _store(root / "profiles" / "alpha" / "state.db",
           sessions=[("a1", "subagent", "m-main", "other", None, NOW - 100, None, None)],
           messages=[(1, "a1", "user", None, NOW - 90, None, 0, None, None),
                     (2, "a1", "assistant", None, NOW - 80, "stop", 0, None, None)],
           usage=[("a1", "m-main", "other", "", 1, 20, 2, 0, 0, 0, 2.0, 1.0, NOW - 80, NOW - 80)])


W = NOW - 3600  # the voice call's wall start


def _voice(root):
    def ev(t, kind, **fields):
        return json.dumps({"ev": kind, "t": t, "wall": W + t, **fields})

    def commit(t):
        return json.dumps({"ev": "rx", "src": "provider", "type": "turn.done", "t": t, "wall": W + t,
                           "event": {"turn": {"role": "user", "transcript": SECRET}}})

    lines = [ev(0.0, "call.created", engine="gpt-live", model="gpt-live-1", profile="z0"),
             ev(0.5, "call.negotiated", setup_s=0.5)]
    # Three user turns; end of speech -> first assistant audio is 1.5s, 2.1s, 3.0s.
    for start, gap in ((1.0, 1.5), (10.0, 2.1), (30.0, 3.0)):
        lines += [ev(start, "audio.on", dir="input"), ev(start + 1, "audio.off", dir="input"),
                  commit(start + 1.1), ev(start + 1 + gap, "audio.on", dir="output"),
                  json.dumps({"ev": "rx", "src": "provider", "type": "response.audio_transcript.done",
                              "t": start + 1 + gap, "wall": W + start + 1 + gap, "transcript": SECRET})]
    lines += [ev(2.2, "user.reaction", correction=True, frustration=False, repeat=False, barge_in=True,
                 text=SECRET),
              ev(4.0, "delegation.start", did="d1", request=SECRET),
              ev(5.0, "lanes.tier", did="d1", tier="fast", model="m-fast", first_s=0.4, s=0.9,
                 errors={"m-bad": SECRET, "m-skip": "SKIP", "m-empty": "empty"}, answer=SECRET),
              ev(5.2, "lanes.spoken", did="d1", tier="fast", first=True, text=SECRET),
              ev(9.0, "delegation.done", did="d1", s=5.0, closed=False, answer=SECRET),
              ev(40.0, "call.closed", seconds=40.0, reason="client_closed")]
    path = root / "2026-11-20" / "101500-call1.jsonl"
    path.parent.mkdir(parents=True)
    # A writer's partial last line must not be read.
    path.write_text("\n".join(lines) + '\n{"ev": "call.closed", "t": 99, "wall": ', encoding="utf-8")


def _route(data, voice, path, query=""):
    return stats_api.route(path, query, data, voice, NOW * 1000)


class StatsAdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "hermes"
        cls.voice_root = Path(cls.tmp.name) / "voice-traces"
        _hermes(cls.root)
        _voice(cls.voice_root)
        cls.digests = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in cls.root.rglob("state.db")}
        cls.data = stats_hermes.load(stats_hermes.stores(cls.root), now=NOW)
        cls.voice = stats_voice.load(cls.voice_root, now=NOW)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def get(self, path, query=""):
        return _route(self.data, self.voice, path, query)

    def test_totals_reconcile_with_the_store_across_profiles(self):
        overall = self.get("/api/stats/overview", "range=all")["overall"]
        self.assertEqual(overall["totalRequests"], 7)  # 3 + 2 title calls + 1 + 1
        self.assertEqual(overall["failedRequests"], 1)
        self.assertAlmostEqual(overall["totalCost"], 0.30 + 0.01 + 0.05 + 1.0)  # actual cost wins when > 0
        self.assertEqual(overall["totalCacheReadTokens"], 3000)
        self.assertEqual(overall["totalInputTokens"], 300 + 50 + 10 + 20)
        self.assertEqual(overall["totalToolCalls"], 2)
        self.assertIsNone(overall["avgTtft"])  # never recorded, so never zero
        self.assertIsNone(overall["cacheSavings"])
        self.assertEqual(sorted(self.get("/api/stats/overview", "range=all")["profiles"]), ["alpha", "default"])
        by_profile = {r["profile"]: r["cost"] for r in self.get("/api/stats/costs", "range=all")["byProfile"]}
        self.assertAlmostEqual(by_profile["default"], 0.36)
        self.assertAlmostEqual(by_profile["alpha"], 1.0)

    def test_range_excludes_older_calls(self):
        overall = self.get("/api/stats/overview", "range=24h")["overall"]
        self.assertEqual(overall["totalRequests"], 6)  # s2 is 40 days old
        self.assertEqual(self.get("/api/stats/overview", "range=30d")["overall"]["totalRequests"], 6)
        with self.assertRaises(stats_api.BadRequest):
            self.get("/api/stats/overview", "range=2d")

    def test_session_usage_is_shared_evenly_across_its_assistant_messages(self):
        rows = [r for r in self.get("/api/stats/recent", "limit=50") if r["model"] == "m-main"
                and r["api"] == "default" and r["timestamp"] > (NOW - 3600) * 1000]
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(row["usage"]["input"], 100)
            self.assertEqual(row["usage"]["cacheRead"], 1000)
            self.assertAlmostEqual(row["usage"]["cost"]["total"], 0.10)
            self.assertIsNone(row["ttft"])

    def test_side_channel_usage_counts_its_api_calls(self):
        side = self.get("/api/stats/overview", "range=24h")["bySideChannel"]
        self.assertEqual([(r["category"], r["totalRequests"]) for r in side], [("title", 2)])

    def test_durations_are_message_gaps_and_idle_gaps_stay_unknown(self):
        rows = {r["timestamp"]: r for r in self.get("/api/stats/recent", "limit=50")}
        self.assertEqual(rows[(NOW - 2990) * 1000]["duration"], 10_000)
        self.assertEqual(rows[(NOW - 2980) * 1000]["duration"], 4_000)  # from the last tool result
        self.assertIsNone(rows[(NOW - 40 * 86400 + 7200) * 1000]["duration"])  # 2h idle

    def test_errors_hold_failed_turns_and_not_interrupted_ones(self):
        rows = {r["timestamp"]: r for r in self.get("/api/stats/recent", "limit=50")}
        self.assertEqual(rows[(NOW - 40 * 86400 + 7200) * 1000]["stopReason"], "aborted")
        errors = self.get("/api/stats/errors", "range=all")
        self.assertEqual([(e["stopReason"], e["errorMessage"]) for e in errors], [("error", "Failed turn")])

    def test_compressed_messages_are_not_calls(self):
        stamps = {r["timestamp"] for r in self.get("/api/stats/recent", "limit=50")}
        self.assertNotIn((NOW - 1500) * 1000, stamps)

    def test_a_message_compaction_carried_forward_counts_once(self):
        stamps = [r["timestamp"] for r in self.get("/api/stats/recent", "limit=50")]
        self.assertEqual(stamps.count((NOW - 2980) * 1000), 1)
        terminal = [r for r in self.get("/api/stats/tools", "range=24h")["byTool"] if r["tool"] == "terminal"]
        self.assertEqual(terminal[0]["calls"], 1)

    def test_tool_calls_and_result_latency(self):
        tools = self.get("/api/stats/tools", "range=24h")
        self.assertEqual({r["tool"]: (r["calls"], r["p50Ms"]) for r in tools["byTool"]},
                         {"terminal": (1, 5000), "read_file": (1, 6000)})
        self.assertEqual(tools["durations"]["samples"], 2)

    def test_voice_calls_match_trace_report_definitions(self):
        payload = self.get("/api/stats/calls", "range=24h")
        overall = payload["overall"]
        self.assertEqual((overall["calls"], overall["userTurns"], overall["handoffs"]), (1, 3, 1))
        self.assertEqual(overall["turnGap"], {"n": 3, "p50": 2.1, "p90": 3.0})
        self.assertEqual(overall["firstAnswer"]["p50"], 1.2)  # hand-off start -> first spoken lane answer
        self.assertEqual(overall["hermesAnswer"]["p50"], 5.0)
        self.assertEqual(overall["setup"]["p50"], 0.5)
        self.assertEqual(overall["talkSeconds"], 40.0)
        (lane,) = payload["lanes"]
        self.assertEqual((lane["tier"], lane["runs"], lane["answered"], lane["spokenFirst"]), ("fast", 1, 1, 1))
        self.assertEqual((lane["firstToken"]["p50"], lane["finish"]["p50"]), (0.4, 0.9))
        self.assertEqual(lane["wins"], [{"model": "m-fast", "count": 1}])
        self.assertEqual(lane["failures"], [{"model": "m-bad", "count": 1}])
        # A lane that declines (SKIP) or returns nothing is not an error.
        self.assertEqual(sorted(r["model"] for r in lane["skips"]), ["m-empty", "m-skip"])
        (call,) = payload["calls"]
        self.assertEqual((call["reason"], call["seconds"], call["model"]), ("client_closed", 40.0, "gpt-live-1"))

    def test_frustration_counts_only_turns_that_recorded_reactions(self):
        payload = self.get("/api/stats/frustration", "range=24h")
        overall = payload["overall"]
        self.assertEqual((overall["turns"], overall["recorded"]), (3, 1))
        self.assertEqual((overall["corrections"], overall["frustration"], overall["bargeIns"]), (1, 0, 1))
        self.assertEqual(payload["recordedSince"], int((W + 2.1) * 1000))

    def test_no_payload_carries_message_text(self):
        payloads = [self.get(path, "range=all") for path in (
            "/api/stats/overview", "/api/stats/model-dashboard", "/api/stats/costs", "/api/stats/folders",
            "/api/stats/tools", "/api/stats/providers", "/api/stats/errors", "/api/stats/calls",
            "/api/stats/frustration")]
        payloads.append(self.get("/api/stats/recent", "limit=50"))
        sessions = self.get("/api/sessions", "limit=50")
        payloads.append(sessions)
        for row in sessions:
            trace = self.get("/api/session/trace", f"file={row['file']}")
            payloads.append(trace)
            for track in trace["tracks"]:
                for span in track["spans"]:
                    if "entryId" in span:  # model and tool spans open an entry; turns do not
                        payloads.append(self.get("/api/session/entry", f"file={track['file']}&id={span['entryId']}"))
        for row in self.get("/api/stats/recent", "limit=50"):  # every row the request drawer can open
            payloads.append(self.get(f"/api/request/{row['id']}"))
        # Insights, the LLM payload, and the exact messages a model would receive.
        sent = []
        llm = stats_narrate.Llm("capture", lambda messages: (sent.append(messages), ('{"summary": "ok", '
                                                                                     '"actions": []}', "m"))[1])
        for name in stats_api.RANGES:
            w = stats_api.window(name, NOW * 1000)
            report = stats_insights.report(self.data, self.voice, w, name, NOW * 1000)
            payload = stats_insights.llm_payload(report, self.data, self.voice, w)
            payloads += [report, payload, stats_narrate.narrate(payload, llm, stats_narrate.Cache(None)),
                         stats_narrate.ask(payload, "where did it go?", llm, stats_narrate.Cache(None))]
        payloads.append(sent)
        text = json.dumps(payloads)
        self.assertGreater(len(payloads), 20)
        self.assertEqual(len(sent), 2 * len(stats_api.RANGES))
        self.assertNotIn(SECRET, text)
        self.assertNotIn("/work/alpha", text)  # folders are basenames only

    def test_stores_are_opened_read_only(self):
        for path, digest in self.digests.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
            self.assertFalse(path.with_name(path.name + "-wal").exists())

    def test_server_binds_loopback_and_rejects_foreign_hosts(self):
        server = stats_server.serve(stats_server.Source(self.root, self.voice_root), port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address[:2]
            self.assertEqual(host, "127.0.0.1")
            url = f"http://127.0.0.1:{port}/api/stats/overview?range=all"
            with urllib.request.urlopen(url, timeout=5) as response:
                self.assertEqual(json.load(response)["overall"]["totalRequests"], 7)
            foreign = urllib.request.Request(url, headers={"Host": f"evil.example:{port}"})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(foreign, timeout=5)
            self.assertEqual(caught.exception.code, 421)
            caught.exception.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
