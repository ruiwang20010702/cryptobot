"""Regime 感知评估模块测试"""

from cryptobot.journal.models import SignalRecord
from cryptobot.journal.regime_evaluator import (
    RegimeEvalResult,
    evaluate_by_regime,
    evaluate_rule_effectiveness,
)


def _make_record(pnl: float, regime: str = "trending") -> SignalRecord:
    """构造测试用 SignalRecord"""
    return SignalRecord(
        symbol="BTCUSDT",
        action="long",
        status="closed",
        actual_pnl_pct=pnl,
        regime_name=regime,
        timestamp="2026-01-15T00:00:00+00:00",
    )


class TestEvaluateByRegime:
    def test_basic_comparison(self):
        """基本的两期对比"""
        records_a = [
            _make_record(-2.0, "trending"),
            _make_record(-1.0, "trending"),
            _make_record(1.0, "trending"),
        ]
        records_b = [
            _make_record(3.0, "trending"),
            _make_record(2.0, "trending"),
            _make_record(1.0, "trending"),
        ]
        results = evaluate_by_regime(records_a, records_b)
        assert len(results) == 1
        r = results[0]
        assert r.regime == "trending"
        assert r.period_b["avg_pnl"] > r.period_a["avg_pnl"]
        assert r.improvement_pct > 0
        assert r.sample_size == 6

    def test_multiple_regimes(self):
        """多个 regime 分别评估"""
        records_a = [
            _make_record(1.0, "trending"),
            _make_record(-1.0, "ranging"),
        ]
        records_b = [
            _make_record(2.0, "trending"),
            _make_record(-2.0, "ranging"),
        ]
        results = evaluate_by_regime(records_a, records_b)
        regimes = {r.regime for r in results}
        assert regimes == {"trending", "ranging"}

    def test_empty_records(self):
        """空记录返回空列表"""
        results = evaluate_by_regime([], [])
        assert results == []

    def test_one_side_empty(self):
        """一侧有记录另一侧无"""
        records_b = [_make_record(2.0, "trending")]
        results = evaluate_by_regime([], records_b)
        assert len(results) == 1
        assert results[0].period_a["count"] == 0
        assert results[0].period_b["count"] == 1

    def test_regime_with_none_name(self):
        """regime_name 为 None 归入 unknown"""
        records_a = [_make_record(1.0, None)]
        results = evaluate_by_regime(records_a, [])
        assert results[0].regime == "unknown"

    def test_frozen_dataclass(self):
        """RegimeEvalResult 是不可变的"""
        r = RegimeEvalResult(
            regime="trending",
            period_a={"count": 1},
            period_b={"count": 1},
            improvement_pct=10.0,
            significant=False,
            sample_size=2,
        )
        try:
            r.regime = "other"
            assert False, "Should raise FrozenInstanceError"
        except AttributeError:
            pass


class TestEvaluateRuleEffectiveness:
    def test_effective_rule(self):
        """明显改善的规则判定为 effective"""
        before = [_make_record(-5.0, "trending") for _ in range(10)]
        after = [_make_record(5.0, "trending") for _ in range(10)]
        result = evaluate_rule_effectiveness("test_rule", before, after)
        assert result["rule_name"] == "test_rule"
        assert result["overall_verdict"] == "effective"
        assert result["effective_regimes"] >= 1

    def test_harmful_rule(self):
        """明显恶化的规则判定为 harmful"""
        before = [_make_record(5.0, "trending") for _ in range(10)]
        after = [_make_record(-5.0, "trending") for _ in range(10)]
        result = evaluate_rule_effectiveness("bad_rule", before, after)
        assert result["overall_verdict"] == "harmful"
        assert result["harmful_regimes"] >= 1

    def test_insufficient_data(self):
        """数据不足时判定为 neutral (insufficient_data)"""
        before = [_make_record(1.0, "trending")]
        after = [_make_record(2.0, "trending")]
        result = evaluate_rule_effectiveness("small_rule", before, after)
        # 样本不足，各 regime 都是 insufficient_data
        for regime_data in result["by_regime"].values():
            assert regime_data["verdict"] == "insufficient_data"

    def test_empty_records(self):
        """空记录返回 neutral"""
        result = evaluate_rule_effectiveness("empty_rule", [], [])
        assert result["overall_verdict"] == "neutral"
        assert result["by_regime"] == {}

    def test_mixed_regimes(self):
        """不同 regime 有不同结果"""
        before = (
            [_make_record(-5.0, "trending") for _ in range(10)]
            + [_make_record(5.0, "ranging") for _ in range(10)]
        )
        after = (
            [_make_record(5.0, "trending") for _ in range(10)]
            + [_make_record(-5.0, "ranging") for _ in range(10)]
        )
        result = evaluate_rule_effectiveness("mixed_rule", before, after)
        assert result["overall_verdict"] == "mixed"
        assert "trending" in result["by_regime"]
        assert "ranging" in result["by_regime"]
