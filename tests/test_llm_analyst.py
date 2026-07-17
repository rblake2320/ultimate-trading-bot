"""LLM analyst safety contract — offline and deterministic.

These used to hide in the integration-marked module, which CI runs only
advisorily: a regression that made the analyst veto (or raise) when Ollama
is unreachable would have merged green. A refused connection on a closed
local port is real behavior, not a mock — no network dependency here.
"""

import pytest

from src.trading_bot.ai.llm_analyst import LLMAnalyst
from src.trading_bot.models import Signal, SignalAction


def make_signal(price=100.0):
    return Signal(
        symbol="BTC/USDT",
        action=SignalAction.BUY,
        confidence=0.7,
        price=price,
        strategy="test",
    )


class TestFailOpen:
    async def test_fails_open_when_unreachable(self, btc_df):
        analyst = LLMAnalyst(
            {
                "enabled": True,
                "provider": "ollama",
                "ollama_url": "http://localhost:59999",  # nothing there
                "timeout_seconds": 3,
            }
        )
        verdict = await analyst.review(make_signal(), btc_df)
        assert verdict.verdict == "unavailable"
        assert not verdict.is_veto

    async def test_disabled_analyst_approves_immediately(self, btc_df):
        analyst = LLMAnalyst({"enabled": False})
        verdict = await analyst.review(make_signal(), btc_df)
        assert verdict.verdict == "approve"
        assert not verdict.is_veto


class TestParse:
    def test_parse_verdict_json(self):
        raw = 'Here you go: {"verdict": "veto", "confidence": 0.8, "reasoning": "RSI 92, parabolic"}'
        verdict = LLMAnalyst._parse(raw)
        assert verdict.is_veto
        assert verdict.confidence == pytest.approx(0.8)

    def test_parse_garbage_raises_and_review_fails_open(self):
        with pytest.raises(ValueError, match="no JSON"):
            LLMAnalyst._parse("the model rambled with no JSON at all")

    def test_unknown_verdict_normalized_to_approve(self):
        verdict = LLMAnalyst._parse('{"verdict": "maybe", "confidence": 0.9}')
        assert verdict.verdict == "approve"

    async def test_low_confidence_veto_not_applied(self, btc_df):
        """A veto below min_veto_confidence must be downgraded to approve."""

        class CannedAnalyst(LLMAnalyst):
            async def _complete(self, context: str) -> str:
                return '{"verdict": "veto", "confidence": 0.2, "reasoning": "meh"}'

        analyst = CannedAnalyst({"enabled": True, "min_veto_confidence": 0.6})
        verdict = await analyst.review(make_signal(), btc_df)
        assert verdict.verdict == "approve"
        assert "confidence bar" in verdict.reasoning
