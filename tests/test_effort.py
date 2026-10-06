"""Reasoning-effort resolution.

The contract these tests pin down:
* "default" writes nothing, so a model nobody touches behaves exactly as it did
  before the feature existed.
* An unknown model id gets NO dropdown. The first version offered the OpenAI
  levels on every unrecognised id, which put the selector on all ~500 models in
  the live config — including image/video generators where picking a level is a
  guaranteed provider rejection.
* A gateway connection is not evidence: one OpenRouter base_url serves both
  reasoning text models and image generators, so this is decided per model id.
"""

from __future__ import annotations

import pytest

from cptr.utils.effort import (
    effort_request_param,
    effort_request_params,
    is_non_text_model,
    known_reasoning_models,
    openrouter_model_takes_effort,
    resolve_effort_levels,
    set_openrouter_reasoning_models,
)

OPENAI = {"provider": "openai", "base_url": "https://api.openai.com/v1"}
OPENROUTER = {"provider": "openai_compatible", "base_url": "https://openrouter.ai/api/v1"}
OLLAMA = {"provider": "ollama", "base_url": "http://localhost:11434"}


class TestResolveEffortLevels:
    @pytest.mark.parametrize(
        "model_id",
        [
            "openrouter/x-ai/grok-imagine-image",
            "grok-imagine-image-quality",
            "grok-imagine-video",
            "grok-imagine-video-1.5-lite",
            "text-embedding-3-large",
            "text-embedding-3-small",
            "whisper-1",
            "tts-1-hd",
        ],
    )
    def test_non_text_models_get_no_dropdown(self, model_id):
        assert resolve_effort_levels(model_id, OPENROUTER) == ["default"]

    def test_non_text_check_ignores_plain_text_ids(self):
        assert not is_non_text_model("openrouter/openai/gpt-6-sol")
        assert not is_non_text_model("claude-opus-5.5")

    @pytest.mark.parametrize(
        "model_id,conn",
        [
            ("ollama/qwen2.5:14b-instruct", OLLAMA),
            ("ollama/llama3.2:latest", OLLAMA),
            ("some-unknown-model", OPENROUTER),
        ],
    )
    def test_unknown_models_get_no_dropdown(self, model_id, conn):
        # The regression this guards: these were all offered the full level list.
        assert resolve_effort_levels(model_id, conn) == ["default"]

    def test_always_on_reasoners_have_no_knob(self):
        assert resolve_effort_levels("deepseek-reasoner", {"provider": "deepseek"}) == [
            "default"
        ]

    def test_agent_models_have_no_knob(self):
        assert resolve_effort_levels("agent:codex", {}) == ["default"]

    @pytest.mark.parametrize(
        "model_id",
        ["gpt-6-sol", "o3-mini", "o1-preview", "gpt-5.1"],
    )
    def test_openai_reasoning_families_get_levels(self, model_id):
        assert resolve_effort_levels(model_id, OPENAI) == [
            "default",
            "minimal",
            "low",
            "medium",
            "high",
        ]

    def test_openrouter_text_model_gets_levels(self):
        assert resolve_effort_levels("openrouter/anthropic/claude-opus-5.5", OPENROUTER) == [
            "default",
            "minimal",
            "low",
            "medium",
            "high",
        ]

    @pytest.mark.parametrize(
        "model_id",
        [
            # Serving a mix of capable and incapable models on one base_url is
            # the whole point of the vendor rule.
            "openrouter/meta-llama/llama-guard-4-12b",
            "openrouter/meta-llama/llama-4-scout",
            "openrouter/openrouter/auto-beta",
            "openrouter/thedrummer/unslopnemo-12b",
            # A bare id on an OpenRouter connection is not a namespaced proxy.
            "grok-4.7",
            "some-unknown-model",
        ],
    )
    def test_openrouter_models_without_a_known_vendor_get_nothing(self, model_id):
        assert resolve_effort_levels(model_id, OPENROUTER) == ["default"]

    def test_openrouter_gpt6_line_is_recognised(self):
        # The substring test for "gpt-5" missed the whole gpt-6 line.
        assert resolve_effort_levels("openrouter/openai/gpt-6.1-sol-pro", OPENROUTER) != [
            "default"
        ]
        assert resolve_effort_levels("gpt-6-sol", OPENAI) != ["default"]

    def test_o_series_regex_respects_token_boundaries(self):
        # `-` is a genuine token boundary, so an o-series id after a vendor
        # prefix still matches. What must not match is `o3` buried mid-token,
        # which the earlier plain substring test could not distinguish.
        assert resolve_effort_levels("microsoft/phi-o3", {"provider": "custom"}) != ["default"]
        for not_a_match in ("microsoft/video-o3-preview", "cohere-a1-compact"):
            assert resolve_effort_levels(not_a_match, {"provider": "custom"}) == ["default"]

    def test_default_is_always_first(self):
        levels = resolve_effort_levels("gpt-6-sol", OPENAI)
        assert levels[0] == "default"


class TestConfigOverrides:
    def test_admin_can_declare_levels_for_an_unknown_model(self):
        levels = resolve_effort_levels(
            "mystery-model-9",
            {"provider": "custom"},
            {"mystery-model-9": {"effort_levels": ["high", "low"]}},
        )
        # Ascending whatever order config listed them in.
        assert levels == ["default", "low", "high"]

    def test_admin_can_disable_the_dropdown(self):
        assert (
            resolve_effort_levels("gpt-6-sol", OPENAI, {"gpt-6-sol": {"effort_levels": []}})
            == ["default"]
        )

    def test_empty_config_entry_is_unset_not_a_disable(self):
        # `{}` declares nothing, so the heuristic still applies; only an explicit
        # `[]` is a disable.
        assert resolve_effort_levels("gpt-6-sol", OPENAI, {"gpt-6-sol": {}}) == [
            "default",
            "minimal",
            "low",
            "medium",
            "high",
        ]

    def test_global_star_applies(self):
        assert resolve_effort_levels("gpt-6-sol", OPENAI, {"*": {"effort_levels": []}}) == [
            "default"
        ]

    def test_model_entry_beats_global_star(self):
        levels = resolve_effort_levels(
            "gpt-6-sol",
            OPENAI,
            {"*": {"effort_levels": []}, "gpt-6-sol": {"effort_levels": ["low"]}},
        )
        assert levels == ["default", "low"]

    def test_config_beats_the_non_text_guard(self):
        # An admin who declares a level on an image model has said they know
        # better than the id does; config is consulted before the heuristics.
        levels = resolve_effort_levels(
            "grok-imagine-image",
            OPENROUTER,
            {"grok-imagine-image": {"effort_levels": ["low", "high"]}},
        )
        assert levels == ["default", "low", "high"]

    def test_config_ignores_unknown_level_names(self):
        levels = resolve_effort_levels(
            "gpt-6-sol", OPENAI, {"gpt-6-sol": {"effort_levels": ["low", "turbo"]}}
        )
        assert levels == ["default", "low"]


class TestOpenRouterPublishedCapability:
    """OpenRouter's /models publishes `supported_parameters` per model.

    That is authoritative, so it overrides the vendor fallback. It matters
    because vendor granularity is provably wrong: 18 of OpenRouter's vendors are
    mixed, and `openai` alone is 76 models with reasoning support against 26
    without.
    """

    @pytest.fixture(autouse=True)
    def _clear_metadata(self):
        set_openrouter_reasoning_models(None)
        yield
        set_openrouter_reasoning_models(None)

    @pytest.fixture
    def metadata(self):
        set_openrouter_reasoning_models(
            [
                "openai/gpt-6-sol",
                "google/gemini-3.6-flash",
                # gpt-4o-mini is deliberately NOT here: it is OpenRouter's
                # clearest "right vendor, no reasoning support" case.
            ]
        )

    def test_no_metadata_falls_back_to_the_vendor_list(self):
        # Before/without the fetch, an unlisted vendor still gets nothing.
        assert resolve_effort_levels("openrouter/thedrummer/unslopnemo-12b", OPENROUTER) == [
            "default"
        ]

    def test_declared_model_gets_levels(self, metadata):
        assert resolve_effort_levels("openrouter/openai/gpt-6-sol", OPENROUTER) == [
            "default",
            "minimal",
            "low",
            "medium",
            "high",
        ]

    def test_undeclared_model_from_a_capable_vendor_is_excluded(self, metadata):
        # The case a vendor list cannot get right: right vendor, no support.
        assert resolve_effort_levels("openrouter/openai/gpt-4o-mini", OPENROUTER) == ["default"]

    def test_metadata_cannot_grant_an_unlisted_id(self, metadata):
        # thedrummer is absent from both the vendor list and the metadata, so it
        # stays excluded: metadata can only confirm a model, never invent one.
        assert resolve_effort_levels("openrouter/thedrummer/unslopnemo-12b", OPENROUTER) == [
            "default"
        ]

    def test_metadata_grants_a_vendor_outside_the_fallback_list(self):
        set_openrouter_reasoning_models(["stealth/space-bunny-alpha"])
        assert resolve_effort_levels("openrouter/stealth/space-bunny-alpha", OPENROUTER) == [
            "default",
            "minimal",
            "low",
            "medium",
            "high",
        ]

    def test_lookup_is_by_unprefixed_id(self, metadata):
        # cptr keys connections with prefix_id, OpenRouter publishes without it.
        assert openrouter_model_takes_effort("openrouter/openai/gpt-6-sol") is True
        assert openrouter_model_takes_effort("openai/gpt-6-sol") is True

    def test_unknown_model_is_false_not_none_once_metadata_exists(self, metadata):
        # False = confidently excluded; None = no opinion, use the fallback.
        assert openrouter_model_takes_effort("openrouter/openai/gpt-4o-mini") is False

    def test_clearing_metadata_restores_the_fallback(self, metadata):
        set_openrouter_reasoning_models([])
        assert known_reasoning_models() == frozenset()
        assert openrouter_model_takes_effort("openrouter/openai/gpt-4o-mini") is None

    def test_non_text_guard_still_applies_over_metadata(self, metadata):
        set_openrouter_reasoning_models(["x-ai/grok-imagine-image"])
        assert resolve_effort_levels("openrouter/x-ai/grok-imagine-image", OPENROUTER) == [
            "default"
        ]

    def test_config_still_overrides_metadata(self, metadata):
        levels = resolve_effort_levels(
            "openrouter/openai/gpt-6-sol", OPENROUTER, {"openrouter/openai/gpt-6-sol": {}}
        )
        assert levels == ["default", "minimal", "low", "medium", "high"]


class TestEffortRequestParam:
    @pytest.mark.parametrize("level", ["default", "", None, "  "])
    def test_default_writes_nothing(self, level):
        assert effort_request_param(level, "gpt-6-sol", OPENAI) is None

    def test_unknown_level_writes_nothing(self):
        # A level that never came from the dropdown must not reach the provider.
        assert effort_request_param("turbo", "gpt-6-sol", OPENAI) is None

    def test_openai_shape(self):
        assert effort_request_param("high", "gpt-6-sol", OPENAI) == {"reasoning_effort": "high"}

    def test_openrouter_shape_is_nested(self):
        assert effort_request_param("low", "openrouter/x-ai/grok-4.7", OPENROUTER) == {
            "reasoning": {"effort": "low"}
        }

    def test_gemini_collapses_minimal_onto_low(self):
        gemini = {"provider": "gemini", "base_url": "https://generativelanguage.googleapis.com"}
        assert effort_request_param("minimal", "gemini-3.6-flash", gemini) == {
            "reasoning_effort": "low"
        }
        assert effort_request_param("high", "gemini-3.6-flash", gemini) == {
            "reasoning_effort": "high"
        }

    def test_anthropic_shape_is_a_token_budget(self):
        anthropic = {"provider": "anthropic", "base_url": "https://api.anthropic.com"}
        body = effort_request_param("high", "claude-opus-4-1", anthropic)
        assert body["thinking"]["type"] == "enabled"
        assert body["thinking"]["budget_tokens"] == 8192
        # Anthropic requires budget_tokens < max_tokens, so the cap is raised.
        assert body["max_tokens"] > body["thinking"]["budget_tokens"]

    def test_anthropic_honours_a_configured_budget(self):
        anthropic = {"provider": "anthropic", "base_url": "https://api.anthropic.com"}
        body = effort_request_param(
            "high",
            "claude-opus-4-1",
            anthropic,
            {"claude-opus-4-1": {"anthropic_thinking_budget": 2048}},
        )
        assert body["thinking"]["budget_tokens"] == 2048

    def test_config_param_renames_the_wire_key(self):
        body = effort_request_param(
            "high",
            "mystery-model-9",
            {"provider": "custom"},
            {"mystery-model-9": {"reasoning_effort_param": "thinking_level"}},
        )
        assert body == {"thinking_level": "high"}

    def test_config_param_wins_over_the_detected_shape(self):
        body = effort_request_param(
            "high",
            "gpt-6-sol",
            OPENAI,
            {"gpt-6-sol": {"reasoning_effort_param": "reasoning.effort"}},
        )
        assert body == {"reasoning.effort": "high"}


class TestEffortRequestParams:
    def test_default_returns_empty_so_the_caller_drops_it(self):
        assert effort_request_params("default", "gpt-6-sol", OPENAI) == {}

    def test_level_is_merged(self):
        assert effort_request_params("low", "gpt-6-sol", OPENAI) == {"reasoning_effort": "low"}
