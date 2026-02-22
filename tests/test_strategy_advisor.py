"""Tests for evolution.strategy_advisor"""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from cryptobot.evolution.strategy_advisor import (
    get_strategy_addon,
    run_advisor_cycle,
    _load_rules,
    _save_rules,
    _expire_old_rules,
    _evaluate_expired_rule,
    _create_rules,
    _call_advisor,
    MAX_ACTIVE_RULES,
    RULE_TTL_DAYS,
)


@pytest.fixture()
def rules_file(tmp_path, monkeypatch):
    """Use a temp directory for rules file"""
    rules_dir = tmp_path / "evolution"
    rules_dir.mkdir()
    rules_file = rules_dir / "strategy_rules.json"
    monkeypatch.setattr(
        "cryptobot.evolution.strategy_advisor._RULES_DIR", rules_dir,
    )
    monkeypatch.setattr(
        "cryptobot.evolution.strategy_advisor._RULES_FILE", rules_file,
    )
    return rules_file


def _make_rule(
    rule_id="rule_20260221_001",
    role="trader",
    expires_days=7,
    win_rate=0.35,
):
    now = datetime.now(timezone.utc)
    return {
        "id": rule_id,
        "created_at": (now - timedelta(days=RULE_TTL_DAYS - expires_days)).isoformat(),
        "expires_at": (now + timedelta(days=expires_days)).isoformat(),
        "rule_text": f"测试规则 {rule_id}",
        "target_role": role,
        "rationale": "测试原因",
        "perf_snapshot_before": {
            "win_rate": win_rate,
            "avg_pnl_pct": -1.8,
            "closed": 12,
        },
    }


def _make_expired_rule(rule_id="rule_20260207_001", role="trader", win_rate=0.35):
    now = datetime.now(timezone.utc)
    return {
        "id": rule_id,
        "created_at": (now - timedelta(days=RULE_TTL_DAYS + 1)).isoformat(),
        "expires_at": (now - timedelta(hours=1)).isoformat(),
        "rule_text": f"过期规则 {rule_id}",
        "target_role": role,
        "rationale": "测试原因",
        "perf_snapshot_before": {
            "win_rate": win_rate,
            "avg_pnl_pct": -1.8,
            "closed": 12,
        },
    }


# ── get_strategy_addon ────────────────────────────────────────────────

class TestGetStrategyAddon:
    def test_empty_returns_empty_string(self, rules_file):
        assert get_strategy_addon("trader") == ""

    def test_no_file_returns_empty_string(self, rules_file):
        # 文件不存在
        assert get_strategy_addon("risk_manager") == ""

    def test_filters_by_role(self, rules_file):
        data = {
            "active_rules": [
                _make_rule("rule_001", role="trader"),
                _make_rule("rule_002", role="risk_manager"),
                _make_rule("rule_003", role="trader"),
            ],
            "expired_rules": [],
            "evaluation_log": [],
        }
        _save_rules(data)

        trader_addon = get_strategy_addon("trader")
        assert "rule_001" in trader_addon or "测试规则 rule_001" in trader_addon
        assert "测试规则 rule_003" in trader_addon
        assert "测试规则 rule_002" not in trader_addon

        rm_addon = get_strategy_addon("risk_manager")
        assert "测试规则 rule_002" in rm_addon
        assert "测试规则 rule_001" not in rm_addon


# ── _create_rules ─────────────────────────────────────────────────────

class TestCreateRules:
    def test_creates_and_persists(self, rules_file):
        data = {"active_rules": [], "expired_rules": [], "evaluation_log": []}
        suggestions = [
            {"rule_text": "震荡市限制 DOGE 杠杆 2x", "target_role": "trader", "rationale": "DOGE 连亏"},
        ]
        perf = {"win_rate": 0.4, "avg_pnl_pct": -1.0, "closed": 15}

        new_data = _create_rules(suggestions, perf, data)

        assert len(new_data["active_rules"]) == 1
        rule = new_data["active_rules"][0]
        assert rule["rule_text"] == "震荡市限制 DOGE 杠杆 2x"
        assert rule["target_role"] == "trader"
        assert rule["perf_snapshot_before"]["win_rate"] == 0.4
        assert rule["id"].startswith("rule_")

    def test_id_uniqueness(self, rules_file):
        existing = _make_rule("rule_20260221_001")
        data = {"active_rules": [existing], "expired_rules": [], "evaluation_log": []}
        suggestions = [
            {"rule_text": "新规则", "target_role": "trader", "rationale": "原因"},
        ]

        new_data = _create_rules(suggestions, {"win_rate": 0.5, "avg_pnl_pct": 0, "closed": 10}, data)
        new_rule = new_data["active_rules"][-1]
        assert new_rule["id"] != "rule_20260221_001"


# ── _expire_old_rules ─────────────────────────────────────────────────

class TestExpireOldRules:
    @patch("cryptobot.evolution.strategy_advisor._evaluate_expired_rule")
    def test_moves_expired_to_expired_list(self, mock_eval, rules_file):
        mock_eval.return_value = {"verdict": "neutral", "improvement_pct": 0}
        expired_rule = _make_expired_rule()
        active_rule = _make_rule("rule_active")
        data = {
            "active_rules": [expired_rule, active_rule],
            "expired_rules": [],
            "evaluation_log": [],
        }

        result = _expire_old_rules(data)

        assert len(result["active_rules"]) == 1
        assert result["active_rules"][0]["id"] == "rule_active"
        assert len(result["expired_rules"]) == 1

    @patch("cryptobot.evolution.strategy_advisor._evaluate_expired_rule")
    def test_effective_rule_renewed(self, mock_eval, rules_file):
        mock_eval.return_value = {"verdict": "effective", "improvement_pct": 10}
        expired_rule = _make_expired_rule()
        data = {
            "active_rules": [expired_rule],
            "expired_rules": [],
            "evaluation_log": [],
        }

        result = _expire_old_rules(data)

        # 有效规则续期，仍在 active
        assert len(result["active_rules"]) == 1
        assert len(result["expired_rules"]) == 0
        # expires_at 被续期
        new_expires = datetime.fromisoformat(result["active_rules"][0]["expires_at"])
        assert new_expires > datetime.now(timezone.utc)

    @patch("cryptobot.evolution.strategy_advisor._evaluate_expired_rule")
    def test_harmful_rule_removed(self, mock_eval, rules_file):
        mock_eval.return_value = {"verdict": "harmful", "improvement_pct": -15}
        expired_rule = _make_expired_rule()
        data = {
            "active_rules": [expired_rule],
            "expired_rules": [],
            "evaluation_log": [],
        }

        result = _expire_old_rules(data)

        assert len(result["active_rules"]) == 0
        assert len(result["expired_rules"]) == 1
        assert len(result["evaluation_log"]) == 1


# ── _evaluate_expired_rule ────────────────────────────────────────────

class TestEvaluateExpiredRule:
    @patch("cryptobot.journal.regime_evaluator.evaluate_rule_effectiveness")
    @patch("cryptobot.journal.storage.get_all_records")
    def test_effective_when_improved(self, mock_records, mock_eval):
        rule = _make_rule(win_rate=0.35)
        created_at = rule["created_at"]
        before_ts = (datetime.fromisoformat(created_at) - timedelta(days=1)).isoformat()
        after_ts = (datetime.fromisoformat(created_at) + timedelta(days=1)).isoformat()
        mock_records.return_value = [
            MagicMock(status="closed", actual_pnl_pct=-1.0, timestamp=before_ts),
            MagicMock(status="closed", actual_pnl_pct=-2.0, timestamp=before_ts),
            MagicMock(status="closed", actual_pnl_pct=3.0, timestamp=after_ts),
            MagicMock(status="closed", actual_pnl_pct=2.0, timestamp=after_ts),
        ]
        mock_eval.return_value = {
            "overall_verdict": "effective",
            "by_regime": {
                "trending": {
                    "verdict": "effective",
                    "improvement_pct": 20.0,
                    "sample_size": 4,
                },
            },
        }

        result = _evaluate_expired_rule(rule)

        assert result["verdict"] == "effective"
        assert result["improvement_pct"] > 5

    @patch("cryptobot.journal.regime_evaluator.evaluate_rule_effectiveness")
    @patch("cryptobot.journal.storage.get_all_records")
    def test_harmful_when_declined(self, mock_records, mock_eval):
        rule = _make_rule(win_rate=0.35)
        created_at = rule["created_at"]
        before_ts = (datetime.fromisoformat(created_at) - timedelta(days=1)).isoformat()
        after_ts = (datetime.fromisoformat(created_at) + timedelta(days=1)).isoformat()
        mock_records.return_value = [
            MagicMock(status="closed", actual_pnl_pct=2.0, timestamp=before_ts),
            MagicMock(status="closed", actual_pnl_pct=3.0, timestamp=before_ts),
            MagicMock(status="closed", actual_pnl_pct=-5.0, timestamp=after_ts),
            MagicMock(status="closed", actual_pnl_pct=-3.0, timestamp=after_ts),
        ]
        mock_eval.return_value = {
            "overall_verdict": "harmful",
            "by_regime": {
                "trending": {
                    "verdict": "harmful",
                    "improvement_pct": -30.0,
                    "sample_size": 4,
                },
            },
        }

        result = _evaluate_expired_rule(rule)

        assert result["verdict"] == "harmful"
        assert result["improvement_pct"] < -5

    @patch("cryptobot.journal.regime_evaluator.evaluate_rule_effectiveness")
    @patch("cryptobot.journal.storage.get_all_records")
    def test_neutral_when_similar(self, mock_records, mock_eval):
        rule = _make_rule(win_rate=0.35)
        created_at = rule["created_at"]
        before_ts = (datetime.fromisoformat(created_at) - timedelta(days=1)).isoformat()
        after_ts = (datetime.fromisoformat(created_at) + timedelta(days=1)).isoformat()
        mock_records.return_value = [
            MagicMock(status="closed", actual_pnl_pct=1.0, timestamp=before_ts),
            MagicMock(status="closed", actual_pnl_pct=1.0, timestamp=before_ts),
            MagicMock(status="closed", actual_pnl_pct=1.0, timestamp=after_ts),
            MagicMock(status="closed", actual_pnl_pct=1.0, timestamp=after_ts),
        ]
        mock_eval.return_value = {
            "overall_verdict": "neutral",
            "by_regime": {
                "trending": {
                    "verdict": "neutral",
                    "improvement_pct": 1.0,
                    "sample_size": 4,
                },
            },
        }

        result = _evaluate_expired_rule(rule)

        assert result["verdict"] == "neutral"


# ── max active rules limit ───────────────────────────────────────────

class TestMaxActiveRulesLimit:
    def test_refuses_when_full(self, rules_file):
        rules = [_make_rule(f"rule_{i:03d}") for i in range(MAX_ACTIVE_RULES)]
        data = {"active_rules": rules, "expired_rules": [], "evaluation_log": []}
        _save_rules(data)

        with patch("cryptobot.evolution.strategy_advisor._expire_old_rules", return_value=data):
            result = run_advisor_cycle()

        assert result["triggered"] is False
        assert "已满" in result["reason"]


# ── run_advisor_cycle ────────────────────────────────────────────────

class TestRunAdvisorCycle:
    def test_insufficient_data_skips(self, rules_file):
        perf = {"closed": 5, "win_rate": 0.4, "avg_pnl_pct": -1.0}
        with patch("cryptobot.journal.analytics.calc_performance", return_value=perf), \
             patch("cryptobot.journal.analytics.calc_analyst_accuracy", return_value={}):
            result = run_advisor_cycle()

        assert result["triggered"] is False
        assert "数据不足" in result["reason"]

    def test_full_cycle(self, rules_file):
        perf_14 = {
            "closed": 55, "win_rate": 0.4, "avg_pnl_pct": -1.0,
            "by_symbol": {}, "by_direction": {}, "total_pnl_usdt": -100,
        }
        perf_30 = {
            "closed": 80, "win_rate": 0.45, "avg_pnl_pct": -0.5,
            "by_symbol": {}, "by_direction": {}, "total_pnl_usdt": -50,
        }
        accuracy = {"technical": {"total": 10, "correct": 7, "accuracy": 0.7}}

        llm_response = [
            {
                "rule_text": "震荡市中 DOGE 做多限制杠杆 2x",
                "target_role": "trader",
                "rationale": "DOGE 连亏 3 笔",
            }
        ]

        def mock_perf(days):
            return perf_14 if days == 14 else perf_30

        with patch("cryptobot.journal.analytics.calc_performance", side_effect=mock_perf), \
             patch("cryptobot.journal.analytics.calc_analyst_accuracy", return_value=accuracy), \
             patch("cryptobot.evolution.strategy_advisor._call_advisor", return_value=llm_response), \
             patch("cryptobot.evolution.strategy_advisor._notify_new_rules"):
            result = run_advisor_cycle()

        assert result["triggered"] is True
        assert result["new_rules"] == 1

        # 验证持久化
        data = _load_rules()
        assert len(data["active_rules"]) == 1
        assert data["active_rules"][0]["rule_text"] == "震荡市中 DOGE 做多限制杠杆 2x"

    def test_llm_returns_empty(self, rules_file):
        perf = {
            "closed": 55, "win_rate": 0.6, "avg_pnl_pct": 2.0,
            "by_symbol": {}, "by_direction": {}, "total_pnl_usdt": 200,
        }

        with patch("cryptobot.journal.analytics.calc_performance", return_value=perf), \
             patch("cryptobot.journal.analytics.calc_analyst_accuracy", return_value={}), \
             patch("cryptobot.evolution.strategy_advisor._call_advisor", return_value=[]), \
             patch("cryptobot.evolution.strategy_advisor._notify_new_rules"):
            result = run_advisor_cycle()

        assert result["triggered"] is False
        assert "无需新规则" in result["reason"]
