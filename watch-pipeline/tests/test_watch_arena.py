#!/usr/bin/env python3
"""Tests for watch_arena parse_arena_html function."""

import subprocess
import sys
from pathlib import Path

import pytest

# Add scripts/ to path so we can import watch_arena
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from watch_arena import (
    build_arena_snapshot,
    fetch_url,
    parse_arena_html,
    preserve_timestamp_when_unchanged,
    write_json_atomic,
)


FIXTURE_PATH = Path(__file__).parent / 'fixtures' / 'arena-sample.html'


class TestParseArenaHtml:
    """Tests for parse_arena_html() function."""
    
    def setup_method(self):
        """Load fixture HTML."""
        self.html = FIXTURE_PATH.read_text(encoding='utf-8')
    
    def test_parses_fixture(self):
        """parse_arena_html should extract entries from real HTML."""
        result = parse_arena_html(self.html, top_n=100)
        assert len(result) > 0
        assert len(result) <= 100
    
    def test_entry_structure(self):
        """Each entry should have required keys."""
        result = parse_arena_html(self.html, top_n=10)
        required_keys = {'model_id', 'effort', 'rank', 'rating', 'context', 'organization'}
        for entry in result:
            assert set(entry.keys()) == required_keys
    
    def test_model_id_normalized(self):
        """model_id should be lowercase with hyphens."""
        result = parse_arena_html(self.html, top_n=10)
        for entry in result:
            model_id = entry['model_id']
            assert model_id == model_id.lower()
            assert ' ' not in model_id
    
    def test_rating_is_float(self):
        """rating should be a float, rounded to 2 decimals."""
        result = parse_arena_html(self.html, top_n=10)
        for entry in result:
            assert isinstance(entry['rating'], float)
            # Check rounding: rating * 100 should be an integer
            assert entry['rating'] == round(entry['rating'], 2)
    
    def test_rank_is_int(self):
        """rank should be an integer."""
        result = parse_arena_html(self.html, top_n=10)
        for entry in result:
            assert isinstance(entry['rank'], int)
    
    def test_top_n_limits_results(self):
        """top_n should limit the number of returned entries."""
        result_5 = parse_arena_html(self.html, top_n=5)
        result_10 = parse_arena_html(self.html, top_n=10)
        assert len(result_5) == 5
        assert len(result_10) == 10
    
    def test_default_keeps_complete_leaderboard(self):
        """Default parsing must not truncate fixed request evidence."""
        result = parse_arena_html(self.html)
        assert len(result) >= len(parse_arena_html(self.html, top_n=100))
    
    def test_invalid_html_raises(self):
        """Invalid HTML should raise ValueError."""
        invalid_html = "<html><body>No entries here</body></html>"
        try:
            parse_arena_html(invalid_html, top_n=10)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "entries data" in str(e)
    
    def test_context_defaults_to_dash(self):
        """context should be '-' if not provided."""
        result = parse_arena_html(self.html, top_n=50)
        for entry in result:
            assert entry['context'] is not None
            # If context was None in source, it should be '-'
            # (we can't easily test the None case without modifying the fixture)
    
    def test_effort_is_none_or_string(self):
        """effort should be None or a valid effort level string."""
        valid_efforts = {'max', 'xhigh', 'ultra', 'high', 'medium', 'low', None}
        result = parse_arena_html(self.html, top_n=50)
        for entry in result:
            assert entry['effort'] in valid_efforts


def test_unchanged_snapshot_preserves_timestamp(tmp_path):
    path = tmp_path / "arena.json"
    entries = [{"model_id": "model", "rating": 1500, "rank": 1}]
    old = build_arena_snapshot(entries, fetched_at="2026-01-01T00:00:00+00:00")
    write_json_atomic(path, old)
    new = build_arena_snapshot(entries, fetched_at="2026-01-02T00:00:00+00:00")

    stable = preserve_timestamp_when_unchanged(path, new)

    assert stable["source"]["fetched_at"] == "2026-01-01T00:00:00+00:00"


def _refuse_urlopen(*args, **kwargs):
    raise OSError("SSL: UNEXPECTED_EOF_WHILE_READING")


def test_fetch_url_falls_back_to_curl(monkeypatch):
    import watch_arena

    captured = {}

    def fake_run(cmd, capture_output, encoding):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="<html>ok</html>", stderr="")

    monkeypatch.setattr(watch_arena, "urlopen", _refuse_urlopen)
    monkeypatch.setattr(watch_arena.subprocess, "run", fake_run)

    assert fetch_url("https://example.test/leaderboard") == "<html>ok</html>"
    assert captured["cmd"][:4] == ["curl", "-fsSL", "--max-time", "30"]
    assert captured["cmd"][-1] == "https://example.test/leaderboard"


def test_fetch_url_raises_when_curl_also_fails(monkeypatch):
    import watch_arena

    failed = subprocess.CompletedProcess(
        [], 6, stdout="", stderr="curl: (6) Could not resolve host"
    )
    monkeypatch.setattr(watch_arena, "urlopen", _refuse_urlopen)
    monkeypatch.setattr(watch_arena.subprocess, "run", lambda *args, **kwargs: failed)

    with pytest.raises(OSError, match="curl fallback failed"):
        fetch_url("https://example.test/leaderboard")


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
