import argparse
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import stat

import pytest

from _load import load

plugin = load()
from hermes_wrapped.stats import Counts, load_stats
from hermes_wrapped.stats_view import render_stats


NOW = datetime(2026, 6, 7, 12, tzinfo=timezone.utc)


def stamp(value):
    return datetime.fromisoformat(value + '+00:00').timestamp()


@pytest.fixture
def state(tmp_path):
    path = tmp_path / 'profile #1?' / 'state.db'
    path.parent.mkdir()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('''CREATE TABLE sessions (started_at REAL, tool_call_count INTEGER,
                   input_tokens INTEGER, output_tokens INTEGER, estimated_cost_usd REAL,
                   archived INTEGER, hidden INTEGER, title TEXT)''')
        db.executemany('INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?)', [
            (stamp('2026-06-04T23:59:59.999999'), 300, 300, 300, 3, 0, 0, 'outside'),
            (stamp('2026-06-05T00:00:00'), 2, 21, 7, 0.25, 1, 0, 'PRIVATE TITLE'),
            (stamp('2026-06-05T23:59:59.999999'), 3, 21, 8, None, 0, 0, 'PRIVATE TITLE'),
            (stamp('2026-06-07T03:00:00'), None, 12, None, 0.0, 0, 0, 'PRIVATE TITLE'),
            (stamp('2026-06-07T04:00:00'), 100, 100, 100, 1, 0, 1, 'hidden'),
            (NOW.timestamp(), 200, 200, 200, 2, 0, 0, 'outside'),
        ])
        db.execute('CREATE TABLE messages (content TEXT)')
        db.execute("INSERT INTO messages VALUES ('PRIVATE MESSAGE')")
    return path


def test_utc_buckets_and_read_only_output(state):
    original = state.read_bytes()
    stats = load_stats(state, days=3, now=NOW)
    assert stats.total == Counts(3, 5, 54, 15, 0.25, 2)
    assert [(str(day), counts.sessions) for day, counts in stats.daily] == [
        ('2026-06-05', 2), ('2026-06-06', 0), ('2026-06-07', 1),
    ]
    assert state.read_bytes() == original
    html = render_stats(stats)
    assert 'Usage overview' in html and '$0.25' in html and '2 of 3 records' in html
    assert '<td>2026-06-05</td><td>2</td><td>5</td><td>42</td><td>15</td>' in html
    assert 'PRIVATE' not in html and str(state) not in html
    assert 'https://' not in html and '<script' not in html


def test_empty_and_recorded_zero_are_distinct(state):
    empty = load_stats(state, days=1, now=datetime(2026, 6, 6, 12, tzinfo=timezone.utc))
    assert empty.total.sessions == 0 and empty.total.estimated_usd is None
    assert 'No session records' in render_stats(empty) and 'Not recorded' in render_stats(empty)
    zero = load_stats(state, days=1, now=NOW)
    assert zero.total.estimated_usd == 0
    assert '$0.00' in render_stats(zero)


def test_legacy_schema_does_not_invent_cost(tmp_path):
    path = tmp_path / 'state.db'
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE sessions (started_at, tool_call_count, input_tokens, output_tokens)')
        db.execute('INSERT INTO sessions VALUES (?, 1, 2, 3)', (NOW.timestamp() - 10,))
    assert load_stats(path, now=NOW).total == Counts(1, 1, 2, 3)


def test_cli_creates_a_private_html_file(state, tmp_path, monkeypatch, capsys):
    import tempfile

    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setattr(plugin.store, 'hermes_home', lambda: state.parent)
    parser = argparse.ArgumentParser()
    plugin._cli_setup(parser)
    args = parser.parse_args(['stats', '--days', '30'])
    assert args.func(args) == 0
    page = Path(capsys.readouterr().out.strip())
    assert page.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO) == 0
    assert '<h1>Usage overview</h1>' in page.read_text()


def test_missing_store_is_not_created(tmp_path):
    missing = tmp_path / 'state.db'
    with pytest.raises(sqlite3.OperationalError):
        load_stats(missing, now=NOW)
    assert not missing.exists()
    with pytest.raises(ValueError):
        load_stats(missing, days=0, now=NOW)
