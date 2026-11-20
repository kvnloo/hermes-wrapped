"""Insights: deterministic findings, the narration grounding gate, and what reaches a model.

Every expected number below is worked out by hand from the fixture, not read back from the code.
Offline and stdlib-only. Run: python3 -m unittest discover -s tests
"""

import itertools
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _load import load  # noqa: E402

plugin = load()
from hermes_wrapped import stats_api, stats_hermes, stats_insights, stats_narrate, stats_server, stats_voice  # noqa: E402
from test_stats_adapter import _store  # noqa: E402

NOW = int(time.time())
FOLDER = "acme-confidential"


def _hermes(root):
    """One profile. Total API-equivalent cost $142.60:
    12 ordinary sessions at $1 (2 calls each); on s0 also compaction 6 calls $6 and titles 10 calls
    $0.10 on the main model m-big, and approvals 20 calls $2 on m-small; `fan` $10 over 4 calls with
    20 subagents at $1.50 each; `heavy` $30 over 3 calls; `long` $50 over 100 calls; `flaky` $2.50
    over 25 calls on m-flaky, 3 of them failed; `free` 4 unpriced calls on m-free."""
    sessions, messages, usage = [], [], []
    ids = itertools.count(1)

    def session(sid, calls, cost, model="m-big", provider="prov", source="cli", parent=None, failed=0):
        start = NOW - 7200
        sessions.append((sid, source, model, provider, parent, start, None, f"/home/u/{FOLDER}"))
        t = start + 10
        messages.append((next(ids), sid, "user", None, t, None, 0, None, None))
        for i in range(calls):
            t += 5
            messages.append((next(ids), sid, "assistant", None, t, "stop", 0, "failed_turn" if i < failed else None,
                             None))
        usage.append((sid, model, provider, "", calls, 1000 * calls, 100 * calls, 0, 0, 0, cost, 0, start, t))

    for i in range(12):
        session(f"s{i}", 2, 1.0)
    usage += [("s0", "m-big", "prov", "compression", 6, 600, 60, 0, 0, 0, 6.0, 0, NOW - 7000, NOW - 7000),
              ("s0", "m-big", "prov", "title_generation", 10, 100, 10, 0, 0, 0, 0.10, 0, NOW - 7000, NOW - 7000),
              ("s0", "m-small", "prov", "approval", 20, 200, 20, 0, 0, 0, 2.0, 0, NOW - 7000, NOW - 7000)]
    session("fan", 4, 10.0)
    for i in range(20):
        session(f"fan-c{i}", 1, 1.5, source="subagent", parent="fan")
    session("heavy", 3, 30.0)
    session("long", 100, 50.0)
    session("flaky", 25, 2.5, model="m-flaky", provider="p2", failed=3)
    session("free", 4, 0.0, model="m-free", provider="sub")
    _store(root / "state.db", sessions, messages, usage)


W = NOW - 3600


def _voice(root):
    """50 fast-lane hand-offs. m-good answers 30 and declines 20 (60%); m-shy answers 5 and declines 45 (10%)."""
    def ev(t, kind, **fields):
        return json.dumps({"ev": kind, "t": t, "wall": W + t, **fields})

    lines = [ev(0.0, "call.created", engine="gpt-live", model="live-1", profile="z0")]
    for i in range(50):
        t = 10.0 + i * 10
        if i < 30:
            model, errors = "m-good", {"m-shy": "SKIP"}
        elif i < 35:
            model, errors = "m-shy", {"m-good": "SKIP"}
        else:
            model, errors = None, {"m-good": "SKIP", "m-shy": "empty"}
        lines += [ev(t, "delegation.start", did=f"d{i}"),
                  ev(t + 1, "lanes.tier", did=f"d{i}", tier="fast", model=model, first_s=0.3, s=0.8, errors=errors),
                  ev(t + 5, "delegation.done", did=f"d{i}", s=5.0, closed=False)]
    lines.append(ev(600.0, "call.closed", seconds=600.0, reason="client_closed"))
    path = root / "2026-11-20" / "100000-call.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class FakeLlm:
    """Records what a model would receive and answers with a fixed reply."""

    def __init__(self, reply):
        self.reply, self.calls = reply, []
        self.llm = stats_narrate.Llm("fake", self.complete)

    def complete(self, messages):
        self.calls.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return (self.reply if isinstance(self.reply, str) else json.dumps(self.reply)), "fake-model-1"


class InsightsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "hermes"
        cls.voice_root = Path(cls.tmp.name) / "voice"
        _hermes(cls.root)
        _voice(cls.voice_root)
        cls.data = stats_hermes.load(stats_hermes.stores(cls.root), now=NOW)
        cls.voice = stats_voice.load(cls.voice_root, now=NOW)
        cls.w = stats_api.window("7d", NOW * 1000)
        cls.report = stats_insights.report(cls.data, cls.voice, cls.w, "7d", NOW * 1000)
        cls.payload = stats_insights.llm_payload(cls.report, cls.data, cls.voice, cls.w)
        cls.by_id = {f["id"]: f for f in cls.report["findings"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def evidence(self, finding_id):
        return {m["key"]: m["value"] for m in self.by_id[finding_id]["evidence"]}

    def finding(self, detector):
        (found,) = [f for f in self.report["findings"] if f["detector"] == detector]
        return found

    # ------------------------------------------------------------ detectors

    def test_totals_and_unpriced_note(self):
        self.assertEqual(self.report["totals"]["costUsd"], 142.6)
        self.assertEqual(self.report["totals"]["unpricedRequests"], 4)
        self.assertIn("4 of", self.report["notes"][0])
        self.assertIn("m-free", self.report["notes"][0])

    def test_side_tasks_on_the_main_model_name_the_settings(self):
        f = self.by_id["side-tasks"]
        ev = self.evidence("side-tasks")
        # Compaction $6 + titles $0.10 on m-big; approvals ran on m-small and do not count.
        self.assertEqual((ev["cost"], ev["calls"], ev["compression"], ev["title_generation"]), (6.1, 16, 6.0, 0.1))
        self.assertNotIn("approval", ev)
        self.assertEqual(ev["share"], 4.3)  # 6.10 / 142.60
        self.assertEqual((f["severity"], f["impact"]["value"]), ("medium", 6.1))
        self.assertIn("auxiliary.compression.model", f["action"])
        self.assertIn("auxiliary.title_generation.prefer_fast_model", f["action"])
        self.assertNotIn("auxiliary.approval", f["action"])

    def test_subagents_on_the_main_model(self):
        ev = self.evidence("subagent-model")
        self.assertEqual((ev["sessions"], ev["calls"], ev["cost"], ev["share"]), (20, 20, 30.0, 21.0))
        self.assertIn("delegation.model", self.by_id["subagent-model"]["action"])

    def test_big_sessions_need_a_measured_cause(self):
        sessions = {f["title"]: f for f in self.report["findings"] if f["detector"] == "big-sessions"}
        fan = sessions["A default session spent $30.00 on 20 subagents"]
        self.assertEqual(fan["impact"]["value"], 30.0)
        self.assertIn("&s=", fan["link"])
        heavy = sessions["A default session paid 20x the typical cost per call"]  # $10 vs $0.50 per call
        self.assertEqual(heavy["impact"]["value"], 28.5)  # ($10.00 - $0.50) x 3 calls
        self.assertEqual(len(sessions), 2)
        # `long` is the biggest ($50) but runs at the typical $0.50 per call: a note, not a finding.
        (check,) = [c for c in self.report["checks"] if c["detector"] == "big-sessions"]
        self.assertIn("$50.00 (35.1%) over 100 calls", check["note"])

    def test_failed_turns_cluster_by_model(self):
        f = self.finding("failed-turns")
        ev = {m["key"]: m["value"] for m in f["evidence"]}
        self.assertEqual((ev["events"], ev["turns"], ev["rate"]), (3, 25, 12.0))
        self.assertEqual(f["severity"], "high")
        self.assertIn("m-flaky", f["title"])

    def test_voice_lane_model_that_mostly_declines(self):
        f = self.finding("voice-lanes")
        ev = {m["key"]: m["value"] for m in f["evidence"]}
        self.assertEqual(f["id"], "lane-fast-m-shy")
        self.assertEqual((ev["answered"], ev["attempts"], ev["declined"], ev["rate"]), (5, 50, 45, 10.0))
        self.assertEqual((ev["mateAnswered"], ev["mateAttempts"], ev["mateRate"]), (30, 50, 60.0))

    def test_findings_rank_by_severity_then_impact(self):
        order = [(f["severity"], f["impact"]["value"] if f["impact"] else 0) for f in self.report["findings"]]
        rank = {"high": 0, "medium": 1, "low": 2}
        self.assertEqual(order, sorted(order, key=lambda o: (rank[o[0]], -o[1])))

    def test_a_fresh_install_says_what_it_waits_for(self):
        empty = stats_hermes.load([], now=NOW)
        rep = stats_insights.report(empty, stats_voice.VoiceData((), ()), self.w, "7d", NOW * 1000)
        self.assertEqual(rep["findings"], [])
        waiting = {c["detector"]: c["note"] for c in rep["checks"] if c["status"] == "needs-data"}
        self.assertIn("Needs 10 priced sessions in the window; has 0.", waiting["big-sessions"])
        self.assertIn("period-compare", waiting)
        self.assertIn("Nothing to change this window.", stats_insights.as_text(rep))

    # ------------------------------------------------------------ privacy

    def test_llm_payload_and_prompt_hold_no_folders_or_session_keys(self):
        fake = FakeLlm({"summary": "x", "actions": []})
        stats_narrate.narrate(self.payload, fake.llm, stats_narrate.Cache(None))
        sent = json.dumps([self.payload, fake.calls])
        self.assertNotIn(FOLDER, sent)
        for key in self.data.sessions:
            self.assertNotIn(key, sent)  # session keys stay out; links are dropped from findings
        self.assertNotIn("link", json.dumps(self.payload["findings"]))
        self.assertEqual(sorted(self.payload), ["findings", "models", "notYetJudged", "notes", "sideTasks",
                                                "totals", "voice", "window"])

    # ------------------------------------------------------------ grounding

    def narrate(self, reply):
        return stats_narrate.narrate(self.payload, FakeLlm(reply).llm, stats_narrate.Cache(None))

    def test_slots_are_filled_from_the_findings(self):
        out = self.narrate({"summary": "Subagents cost {subagent-model.cost}, {subagent-model.share} of the window.",
                            "actions": [{"finding": "side-tasks", "text": "Side tasks hold {side-tasks.impact}."}]})
        self.assertEqual(out["status"], "verified")
        self.assertEqual(out["summary"], "Subagents cost $30.00, 21.0% of the window.")
        self.assertEqual(out["actions"], [{"finding": "side-tasks", "text": "Side tasks hold $6.10."}])
        self.assertEqual(out["model"], "fake-model-1")

    def test_copied_numbers_pass_within_rounding(self):
        out = self.narrate({"summary": "About $30 went to 20 subagents and $143 in all; m-shy answered 10%.",
                            "actions": []})
        self.assertEqual(out["status"], "verified", out.get("rejected"))

    def test_a_hallucinated_number_fails_closed(self):
        out = self.narrate({"summary": "Subagents cost $412.50, so switch models.",
                            "actions": [{"finding": "subagent-model", "text": "Saves {subagent-model.cost}."}]})
        self.assertEqual(out["status"], "unverified")
        self.assertIn("412.50", out["rejected"])
        self.assertNotIn("summary", out)  # the model's text never leaves the gate
        self.assertNotIn("actions", out)

    def test_derived_numbers_unknown_slots_ids_and_comparisons_fail(self):
        cases = {
            "Subagents and side tasks cost $36.10 together.": "36.10",  # a sum the model computed
            "Saves {subagent-model.savings}.": "{subagent-model.savings}",
            "Cost rose since last week.": '"last week"',
        }
        for summary, problem in cases.items():
            out = self.narrate({"summary": summary, "actions": []})
            self.assertEqual(out["status"], "unverified", summary)
            self.assertIn(problem, out["rejected"])
        out = self.narrate({"summary": "Fine.", "actions": [{"finding": "made-up", "text": "Do it."}]})
        self.assertEqual(out["status"], "unverified")

    def test_a_unit_typed_around_a_slot_is_absorbed_or_rejected(self):
        out = self.narrate({"summary": "Subagents cost ${subagent-model.cost}, {subagent-model.share}% of it.",
                            "actions": []})
        self.assertEqual(out["summary"], "Subagents cost $30.00, 21.0% of it.")
        heavy = next(f["id"] for f in self.report["findings"] if "20x" in f["title"])
        out = self.narrate({"summary": f"It ran {{{heavy}.hours}} hours.", "actions": []})
        self.assertEqual(out["summary"], "It ran 0.0h.")
        out = self.narrate({"summary": "Subagents made {subagent-model.calls}% of calls.", "actions": []})
        self.assertEqual(out["status"], "unverified")
        self.assertIn("unit {subagent-model.calls}%", out["rejected"])

    def test_names_with_digits_units_and_compact_numbers(self):
        payload = {"models": [{"model": "claude-opus-5-5", "costUsd": 12.5, "tokens": 1_900_000_000}],
                   "voice": {"hermesAnswerS": {"p50": 1.5}}}
        self.assertEqual(stats_narrate.ungrounded("claude-opus-5-5 cost $12.50 over 1.9B tokens", payload), [])
        self.assertEqual(stats_narrate.ungrounded("Hermes answered in 1500 ms", payload), [])
        self.assertEqual(stats_narrate.ungrounded("Hermes answered in 2500 ms", payload), ["2500 ms"])
        self.assertEqual(stats_narrate.ungrounded("gpt-7 cost $12.50", payload), ["7"])  # an unknown name is not exempt

    def test_failures_and_missing_model_are_statuses_not_crashes(self):
        self.assertEqual(self.narrate(RuntimeError("boom"))["status"], "failed")
        self.assertEqual(self.narrate("not json")["status"], "failed")
        out = stats_narrate.narrate(self.payload, None, stats_narrate.Cache(None))
        self.assertEqual(out["status"], "unavailable")
        self.assertEqual(out["sent"]["keys"], sorted(self.payload))

    def test_results_are_cached_by_input_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = stats_narrate.Cache(Path(tmp))
            fake = FakeLlm({"summary": "Subagents cost {subagent-model.cost}.", "actions": []})
            self.assertEqual(stats_narrate.narrate(self.payload, fake.llm, cache, cached_only=True)["status"],
                             "not_generated")
            first = stats_narrate.narrate(self.payload, fake.llm, cache)
            second = stats_narrate.narrate(self.payload, fake.llm, cache)
            self.assertEqual((first["cached"], second["cached"], len(fake.calls)), (False, True, 1))
            changed = dict(self.payload, totals=dict(self.payload["totals"], requests=1))
            stats_narrate.narrate(changed, fake.llm, cache)
            self.assertEqual(len(fake.calls), 2)
            # A failed call is not cached: asking again retries.
            broken = FakeLlm(RuntimeError("timeout"))
            self.assertEqual(stats_narrate.narrate(changed, stats_narrate.Llm("other", broken.complete), cache)
                             ["status"], "failed")
            stats_narrate.narrate(changed, stats_narrate.Llm("other", broken.complete), cache)
            self.assertEqual(len(broken.calls), 2)

    def test_a_cached_reply_is_checked_again_on_every_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = stats_narrate.Cache(Path(tmp))
            key = stats_narrate.input_hash(self.payload, "fake")
            cache.put(key, {"parsed": {"summary": "It cost $999.99.", "actions": []}, "model": "m"})
            out = stats_narrate.narrate(self.payload, FakeLlm({}).llm, cache, cached_only=True)
            self.assertEqual((out["status"], out["cached"]), ("unverified", True))

    def test_ask_answers_or_says_not_recorded(self):
        fake = FakeLlm({"answer": "Subagents: {subagent-model.cost}.", "findings": ["subagent-model"],
                        "recorded": True})
        out = stats_narrate.ask(self.payload, "Where did 21% go?", fake.llm, stats_narrate.Cache(None))
        self.assertEqual((out["status"], out["answer"]), ("verified", "Subagents: $30.00."))
        self.assertIn("Where did 21% go?", fake.calls[0][1]["content"])
        fake = FakeLlm({"answer": "Prompt text is not recorded.", "findings": [], "recorded": False})
        out = stats_narrate.ask(self.payload, "What did I ask?", fake.llm, stats_narrate.Cache(None))
        self.assertEqual((out["status"], out["recorded"]), ("verified", False))

    # ------------------------------------------------------------ surfaces

    def test_server_routes_and_post_guards(self):
        fake = FakeLlm({"summary": "Subagents cost {subagent-model.cost}.", "actions": []})
        server = stats_server.serve(stats_server.Source(self.root, self.voice_root), port=0, llm=fake.llm)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def call(path, body=None, headers=None):
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(base + path, data, {"Content-Type": "application/json", **(headers or {})})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status, json.load(resp)
            except urllib.error.HTTPError as exc:
                with exc:
                    return exc.code, json.load(exc)

        try:
            status, report = call("/api/stats/insights?range=7d")
            self.assertEqual((status, report["totals"]["costUsd"]), (200, 142.6))
            self.assertEqual(call("/api/insights/narrative?range=7d")[1]["status"], "not_generated")
            status, out = call("/api/insights/narrative", {"range": "7d"})
            self.assertEqual((status, out["summary"]), (200, "Subagents cost $30.00."))
            self.assertEqual(call("/api/insights/narrative", {"range": "7d"}, {"Origin": "http://evil.example"})[0],
                             403)
            self.assertEqual(call("/api/insights/ask", {"question": "?"}, {"Content-Type": "text/plain"})[0], 415)
            self.assertEqual(call("/api/insights/narrative", {"range": "2d"})[0], 400)
            self.assertEqual(len(fake.calls), 1)
        finally:
            server.shutdown()
            server.server_close()

    def test_snapshot_inlines_insights_narratives_and_linked_traces(self):
        fake = FakeLlm({"summary": "Subagents cost {subagent-model.cost}.", "actions": []})
        models = stats_server.Source(self.root, self.voice_root).current()
        api = stats_server.snapshot_api(models, NOW * 1000, fake.llm)
        self.assertIn("/api/stats/insights?range=all", api)
        self.assertEqual(api["/api/insights/narrative?range=7d"]["status"], "verified")
        self.assertNotIn("/api/insights/narrative?range=1h", api)
        for f in api["/api/stats/insights?range=7d"]["findings"]:
            if "&s=" in f["link"]:
                self.assertIn(f"/api/session/trace?file={f['link'].split('&s=')[1]}", api)

    def test_slash_command_answers_with_text_and_no_model(self):
        env = {"HERMES_HOME": str(self.root), "HERMES_MOBILE_VOICE_TRACE_DIR": str(self.voice_root)}
        with mock.patch.dict(os.environ, env):
            text = plugin._wrapped_command(None)("insights week")
            self.assertIn("Hermes insights, last 7d", text)
            self.assertIn("Subagents inherit your main model m-big", text)
            self.assertIn("Unknown range", plugin._wrapped_command(None)("insights fortnight"))


if __name__ == "__main__":
    unittest.main()
