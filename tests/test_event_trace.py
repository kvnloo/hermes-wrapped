import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from _load import load

load()
from hermes_wrapped.event_trace import load_trace
from hermes_wrapped.trace_view import publish_trace, render_trace


class EventTraceTest(unittest.TestCase):
    now = 1_800_000_000

    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.db = self.root / 'profile # 1' / 'state.db'
        self.db.parent.mkdir()
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE sessions (id, title, started_at REAL, ended_at REAL, hidden)')
            db.execute('CREATE TABLE messages (content)')
            db.execute('INSERT INTO messages VALUES (?)', ('PRIVATE_MESSAGE',))
            db.executemany('INSERT INTO sessions VALUES (?,?,?,?,?)', [
                ('PRIVATE_SESSION_ID', 'PRIVATE_TITLE', self.now-4000, self.now-3000, 0),
                ('open', 'title', self.now-6, None, 0),
                ('hidden', 'title', self.now-10, self.now-9, 1),
                ('stale', 'title', self.now-8000, None, 0),
            ])
        self.voice = self.root / 'voice'
        self.day = self.voice / 'day'
        self.day.mkdir(parents=True)

    def event(self, kind, elapsed, *, wall=None, **fields):
        return {'ev': kind, 't': elapsed, 'wall': self.now-200+elapsed if wall is None else wall, **fields}

    def write(self, path, *events, append=False, partial=False):
        with path.open('a' if append else 'w') as target:
            target.write('\n'.join(json.dumps(event) if not isinstance(event, str) else event for event in events))
            if not partial:
                target.write('\n')
        os.utime(path, (self.now, self.now))

    def snapshot(self):
        return load_trace(self.db, self.voice, hours=1, now=self.now)

    def test_render_combines_sources_without_leaking_payloads(self):
        before = hashlib.sha256(self.db.read_bytes()).digest()
        self.write(self.day / 'PRIVATE_FILE.jsonl',
                   self.event('call.created', 0), self.event('call.start', 1),
                   self.event('delegation.start', 4, did='PRIVATE_DID', text='PRIVATE_TEXT'),
                   self.event('lanes.tier', 5, answer='PRIVATE_ANSWER'),
                   self.event('delegation.done', 7, did='PRIVATE_DID', wall=self.now-197),
                   self.event('call.closed', 8), self.event('rx', 9, text='PRIVATE_RX'),
                   self.event('<script>PRIVATE_TYPE</script>', 9))
        snapshot = self.snapshot()
        page = render_trace(snapshot)
        self.assertIn('2 Hermes records · 1 voice traces', page)
        self.assertIn('1,000.00 s · inferred from wall time', page)
        self.assertIn('3.00 s · measured elapsed', page)
        self.assertIn('End unknown · Duration unknown', page)
        self.assertIn('lanes.tier', page)
        self.assertNotIn('PRIVATE_', page)
        self.assertNotIn('<script>', page)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).digest(), before)

    def test_overlapping_calls_do_not_share_ids_or_double_repeated_events(self):
        start = self.event('delegation.start', 2, did='same')
        self.write(self.day / 'a.jsonl', self.event('call.created', 0), start, start,
                   self.event('delegation.start', 3, did='second'),
                   self.event('delegation.done', 5, did='second'))
        self.write(self.day / 'b.jsonl', self.event('call.created', 0),
                   self.event('delegation.done', 6, did='same'), self.event('call.closed', 10))
        spans = [row for lane in self.snapshot().lanes for row in lane.details if row.kind == 'Delegation']
        self.assertCountEqual([(s.state, s.seconds) for s in spans], [
            ('Start unknown', None), ('End unknown', None), ('Complete', 2),
        ])

    def test_refresh_retries_partial_line_and_drops_rotated_generation(self):
        path = self.day / 'call.jsonl'
        self.write(path, self.event('call.created', 0), self.event('call.closed', 10), partial=True)
        output = self.root / 'timeline.html'
        publish_trace(output, self.snapshot(), watch=True)
        self.assertIn('End unknown', output.read_text())
        self.assertNotIn('10.00 s · measured elapsed', output.read_text())
        self.write(path, '', append=True)
        publish_trace(output, self.snapshot(), watch=True)
        page = output.read_text()
        self.assertIn('10.00 s · measured elapsed', page)
        self.assertIn('connect-src \'self\'', page)
        self.assertIn('Pause refresh', page)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        path.rename(path.with_suffix('.old'))
        self.write(path, 'not JSON', self.event('call.closed', 7))
        publish_trace(output, self.snapshot())
        page = output.read_text()
        self.assertIn('Start unknown · Duration unknown', page)
        self.assertIn('1 measured malformed records skipped', page)
        self.assertNotIn('10.00 s · measured elapsed', page)

    def test_missing_db_does_not_create_it_or_block_voice(self):
        self.db.unlink()
        self.write(self.day / 'call.jsonl', self.event('call.created', 0))
        page = render_trace(self.snapshot())
        self.assertIn('Hermes session store unavailable', page)
        self.assertIn('0 Hermes records · 1 voice traces', page)
        self.assertFalse(self.db.exists())

    def test_trailing_window_is_bounded_and_disclosed(self):
        self.write(self.day / 'call.jsonl', 'x' * (3 * 1024 * 1024), self.event('call.closed', 7))
        page = render_trace(self.snapshot())
        self.assertIn('Start unknown · Duration unknown', page)
        self.assertIn('hit a read or display limit', page)
        self.assertNotIn('malformed records', page)
