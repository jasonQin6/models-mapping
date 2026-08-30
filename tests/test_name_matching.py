#!/usr/bin/env python3
"""Tests for name_matching module."""

import sys
from pathlib import Path

# Add scripts/ to path so we can import name_matching
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from name_matching import normalize, normalize_arena_name, find_best_match


class TestNormalize:
    """Tests for normalize() function."""
    
    def test_basic_name(self):
        assert normalize("Claude Opus 5") == "claude-opus-5"
    
    def test_removes_parenthetical_suffix(self):
        assert normalize("GPT-5.4 Mini (20250320)") == "gpt-5.4-mini"
        assert normalize("Qwen3.7-Plus (256K context)") == "qwen3.7-plus"
    
    def test_lowercase(self):
        assert normalize("GLM-5.3") == "glm-5.3"
    
    def test_spaces_to_hyphens(self):
        assert normalize("DeepSeek V4 Pro") == "deepseek-v4-pro"
    
    def test_collapses_consecutive_hyphens(self):
        assert normalize("model--name") == "model-name"
        assert normalize("model - name") == "model-name"
    
    def test_strips_whitespace(self):
        assert normalize("  Claude Opus 5  ") == "claude-opus-5"
    
    def test_complex_name(self):
        assert normalize("Kimi K2.7-Code") == "kimi-k2.7-code"


class TestNormalizeArenaName:
    """Tests for normalize_arena_name() function."""
    
    def test_basic_name_no_effort(self):
        normalized, effort = normalize_arena_name("Claude Opus 5")
        assert normalized == "claude-opus-5"
        assert effort is None
    
    def test_extracts_max_effort(self):
        # Effort is a suffix, not in parentheses
        normalized, effort = normalize_arena_name("Claude-Opus-5-max")
        assert normalized == "claude-opus-5"
        assert effort == "max"
    
    def test_extracts_high_effort(self):
        normalized, effort = normalize_arena_name("DeepSeek-V4-Pro-high")
        assert normalized == "deepseek-v4-pro"
        assert effort == "high"
    
    def test_extracts_xhigh_effort(self):
        normalized, effort = normalize_arena_name("GPT-5.6-Luna-xhigh")
        assert normalized == "gpt-5.6-luna"
        assert effort == "xhigh"
    
    def test_removes_date_suffix(self):
        normalized, effort = normalize_arena_name("GPT-5.4 Mini (20250320)")
        assert normalized == "gpt-5.4-mini"
        assert effort is None
    
    def test_removes_size_suffix_lowercase(self):
        normalized, effort = normalize_arena_name("Llama-70b")
        assert normalized == "llama"
        assert effort is None
    
    def test_removes_size_suffix_uppercase(self):
        normalized, effort = normalize_arena_name("Llama-70B")
        assert normalized == "llama"
        assert effort is None
    
    def test_removes_size_suffix_k(self):
        normalized, effort = normalize_arena_name("Model-70k")
        assert normalized == "model"
        assert effort is None
    
    def test_qwen_exempt_from_effort(self):
        # qwen uses "max" in model names, not effort level
        normalized, effort = normalize_arena_name("Qwen3.8-max")
        assert normalized == "qwen3.8-max"
        assert effort is None
    
    def test_qwen_exempt_case_insensitive(self):
        normalized, effort = normalize_arena_name("qwen3.8-max")
        assert normalized == "qwen3.8-max"
        assert effort is None
    
    def test_non_qwen_extracts_max(self):
        normalized, effort = normalize_arena_name("Claude-Opus-5-max")
        assert normalized == "claude-opus-5"
        assert effort == "max"

    def test_date_before_effort_is_removed(self):
        normalized, effort = normalize_arena_name(
            "claude-opus-4-5-20251101-high"
        )
        assert normalized == "claude-opus-4-5"
        assert effort == "high"
    
    def test_parenthetical_content_removed(self):
        # Parenthetical content is removed, so effort in parens is not extracted
        normalized, effort = normalize_arena_name("Claude Opus 5 (max)")
        assert normalized == "claude-opus-5"
        assert effort is None  # "(max)" is removed by normalize()


class TestFindBestMatch:
    """Tests for find_best_match() function."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.arena_lookup = {
            "claude-opus-5": {
                "rank": 1,
                "rating": 1500.0,
                "context": "200k",
                "organization": "Anthropic",
                "effort": "max",
            },
            "gpt-5.6-luna": {
                "rank": 2,
                "rating": 1480.0,
                "context": "128k",
                "organization": "OpenAI",
                "effort": "xhigh",
            },
            "qwen3.6-plus": {
                "rank": 10,
                "rating": 1400.0,
                "context": "128k",
                "organization": "Alibaba",
                "effort": None,
            },
            "muse-spark-1.2": {
                "rank": 5,
                "rating": 1450.0,
                "context": "64k",
                "organization": "Meta",
                "effort": None,
            },
            "claude-haiku-3.5": {
                "rank": 20,
                "rating": 1350.0,
                "context": "200k",
                "organization": "Anthropic",
                "effort": "low",
            },
        }
    
    def test_direct_match(self):
        result, match_type = find_best_match("claude-opus-5", self.arena_lookup)
        assert result is not None
        assert result["rank"] == 1
        assert result["rating"] == 1500.0
        assert match_type == "direct_match"
    
    def test_remove_contributor_suffix(self):
        result, match_type = find_best_match("muse-spark-1.2-contributor", self.arena_lookup)
        assert result is not None
        assert result["rank"] == 5
        assert match_type == "contributor_suffix"
    
    def test_version_downgrade(self):
        # qwen3.7-plus should match qwen3.6-plus
        result, match_type = find_best_match("qwen3.7-plus", self.arena_lookup)
        assert result is not None
        assert result["rank"] == 10
        assert match_type == "version_downgrade"
    
    def test_version_downgrade_no_match_when_minor_zero(self):
        # qwen3.0-plus should not downgrade (minor=0)
        result, match_type = find_best_match("qwen3.0-plus", self.arena_lookup)
        assert result is None
        assert match_type == "no_match"
    
    def test_prefix_match(self):
        # claude-haiku should match claude-haiku-3.5
        result, match_type = find_best_match("claude-haiku", self.arena_lookup)
        assert result is not None
        assert result["rank"] == 20
        assert match_type == "prefix_match"
    
    def test_prefix_match_picks_highest_rating(self):
        # Add another claude-haiku variant with higher rating
        self.arena_lookup["claude-haiku-3.0"] = {
            "rank": 25,
            "rating": 1300.0,
            "context": "200k",
            "organization": "Anthropic",
            "effort": "low",
        }
        result, match_type = find_best_match("claude-haiku", self.arena_lookup)
        assert result is not None
        assert result["rank"] == 20  # 3.5 has higher rating (1350 > 1300)
        assert match_type == "prefix_match"
    
    def test_free_model_default(self):
        result, match_type = find_best_match("ox-alpha-free", self.arena_lookup, is_free=True)
        assert result is not None
        assert result["rank"] == 0
        assert result["rating"] == 0
        assert result["organization"] == "Unknown"
        assert match_type == "free_default"
    
    def test_no_match_returns_none(self):
        result, match_type = find_best_match("nonexistent-model", self.arena_lookup)
        assert result is None
        assert match_type == "no_match"
    
    def test_no_match_free_false_returns_none(self):
        result, match_type = find_best_match("nonexistent-model", self.arena_lookup, is_free=False)
        assert result is None
        assert match_type == "no_match"
    
    def test_fallback_priority_direct_beats_contributor(self):
        # If both direct and contributor match exist, direct wins
        self.arena_lookup["test-model-contributor"] = {
            "rank": 99,
            "rating": 1000.0,
            "context": "32k",
            "organization": "Test",
            "effort": None,
        }
        result, match_type = find_best_match("test-model-contributor", self.arena_lookup)
        assert result["rank"] == 99  # direct match, not layer 2
        assert match_type == "direct_match"
    
    def test_fallback_layer_2_contributor_then_layer_3_version(self):
        # qwen3.7-plus-contributor: layer 2 tries qwen3.7-plus (not in lookup),
        # layer 3 tries qwen3.6-plus-contributor (not in lookup), returns None
        result, match_type = find_best_match("qwen3.7-plus-contributor", self.arena_lookup)
        assert result is None
        assert match_type == "no_match"
    
    def test_fallback_layer_3_version_downgrade(self):
        # Add qwen3.6-plus-contributor to test layer 3
        self.arena_lookup["qwen3.6-plus-contributor"] = {
            "rank": 15,
            "rating": 1380.0,
            "context": "128k",
            "organization": "Alibaba",
            "effort": None,
        }
        # qwen3.7-plus-contributor: layer 2 tries qwen3.7-plus (not in lookup),
        # layer 3 tries qwen3.6-plus-contributor (now in lookup)
        result, match_type = find_best_match("qwen3.7-plus-contributor", self.arena_lookup)
        assert result is not None
        assert result["rank"] == 15
        assert match_type == "version_downgrade"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])


class TestMatchEvidence:
    """Tests for the match type recorded in mapping reports."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.arena_lookup = {
            "claude-opus-5": {
                "rank": 1,
                "rating": 1500.0,
                "context": "200k",
                "organization": "Anthropic",
                "effort": "max",
            },
            "gpt-5.6-luna": {
                "rank": 2,
                "rating": 1480.0,
                "context": "128k",
                "organization": "OpenAI",
                "effort": "xhigh",
            },
        }
    
    def test_match_type_returned(self):
        """find_best_match should return match_type as second element."""
        result, match_type = find_best_match("claude-opus-5", self.arena_lookup)
        assert match_type == "direct_match"
        
        result, match_type = find_best_match("claude-opus-5-contributor", self.arena_lookup)
        assert match_type == "contributor_suffix"
        
        result, match_type = find_best_match("nonexistent", self.arena_lookup)
        assert match_type == "no_match"
    
    def test_all_match_types(self):
        """Should return all expected match types."""
        # direct_match
        _, match_type = find_best_match("claude-opus-5", self.arena_lookup)
        assert match_type == "direct_match"
        
        # contributor_suffix
        _, match_type = find_best_match("gpt-5.6-luna-contributor", self.arena_lookup)
        assert match_type == "contributor_suffix"
        
        # no_match
        _, match_type = find_best_match("nonexistent-model", self.arena_lookup)
        assert match_type == "no_match"
        
        # free_default
        _, match_type = find_best_match("free-model", self.arena_lookup, is_free=True)
        assert match_type == "free_default"
