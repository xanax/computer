"""Tests for the auto-continue follow-up mode.

Covers the safety envelope around a loop that re-prompts itself: the run cap,
the agent's own stop phrase, refusal to stack follow-ups, internal chats, and
the stop-detection helpers.
"""

from __future__ import annotations

import pytest

from cptr.utils import auto_continue as ac


def _config(**overrides) -> dict:
    config = {"enabled": True, "max_runs": 3}
    config.update(overrides)
    return config


# ── Pure helpers (no DB) ──────────────────────────────────────


class TestPrompt:
    def test_uses_default_when_blank(self):
        for blank in ("", "   ", None, 42):
            assert ac.auto_continue_prompt({"enabled": True, "prompt": blank}) == ac.DEFAULT_PROMPT

    def test_trims_user_prompt(self):
        assert ac.auto_continue_prompt(_config(prompt="  go on  ")) == "go on"

    def test_caps_prompt_length(self):
        long = "x" * 9999
        assert len(ac.auto_continue_prompt(_config(prompt=long))) == ac.MAX_PROMPT_CHARS


class TestMaxRuns:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (5, 5),
            (1, 1),
            (0, 1),  # never zero: an un-armed cap would loop forever
            (-3, 1),
            (9999, ac.MAX_RUNS_CEILING),
            ("12", 12),
            ("nonsense", ac.DEFAULT_MAX_RUNS),
            (None, ac.DEFAULT_MAX_RUNS),
        ],
    )
    def test_clamped(self, raw, expected):
        assert ac._max_runs(_config(max_runs=raw)) == expected

    def test_missing_key_uses_default(self):
        assert ac._max_runs({"enabled": True}) == ac.DEFAULT_MAX_RUNS


class TestStopDetection:
    def test_plain_text_prefers_content(self):
        assert ac.said_stop(ac.assistant_plain_text("nothing left to iterate", None)) is True

    def test_tool_only_turn_keeps_going(self):
        """A tool-heavy turn leaves content empty; a silent turn is not a stop."""
        output = [
            {"type": "message", "content": [{"type": "output_text", "text": "nothing left to iterate"}]},
        ]
        assert ac.assistant_plain_text("", output) == "nothing left to iterate"

    def test_ignores_non_message_items(self):
        output = [{"type": "reasoning", "content": [{"type": "output_text", "text": "nothing left to iterate"}]}]
        assert ac.assistant_plain_text("", output) == ""

    def test_stop_must_be_in_the_tail(self):
        early = "nothing left to iterate. " + ("more prose " * 200) + "actually, one more thing"
        assert ac.said_stop(early) is False

    def test_tolerates_a_closing_summary(self):
        text = "I did four rounds of work. " + ("detail " * 100) + "nothing left to iterate"
        assert ac.said_stop(text) is True

    def test_empty_text_is_not_a_stop(self):
        assert ac.said_stop("") is False
        assert ac.assistant_plain_text("", None) == ""

    def test_negative_when_summarising_only(self):
        assert ac.said_stop("All tests pass. Nothing further to iterate.") is True


class TestConfigReading:
    class _Chat:
        def __init__(self, meta):
            self.meta = meta

    def test_disabled_is_none(self):
        assert ac._config(self._Chat({"params": {"auto_continue": {"enabled": False}}})) is None

    def test_missing_is_none(self):
        assert ac._config(self._Chat({"params": {}})) is None
        assert ac._config(self._Chat(None)) is None
        assert ac._config(None) is None

    def test_wrong_shape_is_none(self):
        assert ac._config(self._Chat({"params": {"auto_continue": "yes"}})) is None
        assert ac._config(self._Chat({"params": {"auto_continue": None}})) is None

    def test_enabled_returns_config(self):
        config = _config()
        assert ac._config(self._Chat({"params": {"auto_continue": config}})) == config


class TestStateReading:
    class _Chat:
        def __init__(self, meta):
            self.meta = meta

    def test_zero_when_absent(self):
        assert ac._state(self._Chat({"params": {}})) == {"runs": 0}
        assert ac._state(None) == {"runs": 0}

    def test_reads_and_clamps(self):
        assert ac._state(self._Chat({ac.STATE_KEY: {"runs": 7}})) == {"runs": 7}
        assert ac._state(self._Chat({ac.STATE_KEY: {"runs": -4}})) == {"runs": 0}
        assert ac._state(self._Chat({ac.STATE_KEY: "junk"})) == {"runs": 0}
        assert ac._state(self._Chat({ac.STATE_KEY: {"runs": "nope"}})) == {"runs": 0}