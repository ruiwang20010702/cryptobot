"""Tests for AI 决策归档系统"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from cryptobot.archive.writer import save_archive, ARCHIVE_BASE, _generate_run_id
from cryptobot.archive.reader import list_archives, get_archive, get_symbol_history


@pytest.fixture(autouse=True)
def _tmp_archive_dir(tmp_path, monkeypatch):
    """将归档目录重定向到临时目录"""
    archive_dir = tmp_path / "archive"
    monkeypatch.setattr("cryptobot.archive.writer.ARCHIVE_BASE", archive_dir)
    monkeypatch.setattr("cryptobot.archive.reader.ARCHIVE_BASE", archive_dir)
    return archive_dir


def _make_state(**overrides):
    """构造测试用 WorkflowState"""
    state = {
        "market_regime": {"regime": "trending", "confidence": 75, "description": "趋势市"},
        "capital_tier": {"tier": "small", "balance": 500},
        "fear_greed": {"current_value": 45, "current_classification": "Fear"},
        "screened_symbols": ["BTCUSDT", "ETHUSDT"],
        "screening_scores": [("BTCUSDT", 12.5), ("ETHUSDT", 10.0), ("SOLUSDT", 8.0)],
        "analyses": {
            "BTCUSDT": {"technical": "bullish", "onchain": "neutral"},
            "ETHUSDT": {"technical": "bearish", "sentiment": "neutral"},
        },
        "research": {
            "BTCUSDT": {"bull": "BTC momentum strong", "bear": "Overextended"},
        },
        "decisions": [
            {"symbol": "BTCUSDT", "action": "long", "confidence": 72, "leverage": 3},
            {"symbol": "ETHUSDT", "action": "no_trade", "confidence": 40},
        ],
        "approved_signals": [
            {"symbol": "BTCUSDT", "action": "long", "confidence": 72},
        ],
        "risk_details": {
            "hard_rule_results": [{"symbol": "BTCUSDT", "passed": True, "checks": []}],
            "ai_review_results": [{"symbol": "BTCUSDT", "verdict": "approved", "reasoning": "ok"}],
            "rejected_signals": [],
        },
        "errors": [],
    }
    state.update(overrides)
    return state


class TestSaveAndReadArchive:
    def test_save_and_read_roundtrip(self):
        """写入归档后能完整读取"""
        state = _make_state()
        run_id = save_archive(state)

        assert run_id.endswith("_trending")

        data = get_archive(run_id)
        assert data is not None
        assert data["run_id"] == run_id
        assert data["regime"]["regime"] == "trending"
        assert data["screened_symbols"] == ["BTCUSDT", "ETHUSDT"]
        assert len(data["screening_scores"]) == 3
        assert data["decisions"][0]["symbol"] == "BTCUSDT"
        assert data["approved_signals"][0]["action"] == "long"
        assert data["risk_details"]["hard_rule_results"][0]["passed"] is True

    def test_save_with_extra(self):
        """extra 字段合并到归档"""
        state = _make_state()
        run_id = save_archive(state, extra={"token_usage": {"total_tokens": 45000}})

        data = get_archive(run_id)
        assert data["token_usage"]["total_tokens"] == 45000

    def test_save_unknown_regime(self):
        """regime 为空时 run_id 使用 unknown"""
        state = _make_state(market_regime={})
        run_id = save_archive(state)
        assert run_id.endswith("_unknown")

    def test_atomic_write(self, _tmp_archive_dir):
        """验证原子写入：无 .tmp 残留"""
        state = _make_state()
        save_archive(state)

        # 不应有 .tmp 文件
        all_files = list(_tmp_archive_dir.rglob("*"))
        tmp_files = [f for f in all_files if f.suffix == ".tmp"]
        assert len(tmp_files) == 0


class TestListArchives:
    def test_list_empty(self):
        """空目录返回空列表"""
        assert list_archives() == []

    def test_list_with_data(self):
        """写入后能列出摘要"""
        save_archive(_make_state())
        save_archive(_make_state(market_regime={"regime": "ranging", "confidence": 60}))

        items = list_archives()
        assert len(items) == 2
        assert all("run_id" in item for item in items)
        assert all("regime" in item for item in items)

    def test_list_with_month_filter(self):
        """月份过滤"""
        save_archive(_make_state())
        from datetime import datetime, timezone
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")

        items = list_archives(month=current_month)
        assert len(items) >= 1

        items_wrong = list_archives(month="2020-01")
        assert len(items_wrong) == 0

    def test_list_limit(self, _tmp_archive_dir):
        """limit 参数限制返回条数"""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        month_str = now.strftime("%Y-%m")
        month_dir = _tmp_archive_dir / month_str
        month_dir.mkdir(parents=True, exist_ok=True)

        # 直接写入不同文件名避免同秒覆盖
        for i in range(5):
            data = {"run_id": f"test_{i}", "timestamp": now.isoformat(),
                    "regime": {"regime": "trending"}, "screened_symbols": [],
                    "decisions": [], "approved_signals": [], "errors": []}
            (month_dir / f"test_{i}.json").write_text(
                json.dumps(data), encoding="utf-8"
            )

        items = list_archives(limit=3)
        assert len(items) == 3


class TestGetSymbolHistory:
    def test_symbol_found(self):
        """能查到包含特定币种的归档"""
        save_archive(_make_state())

        results = get_symbol_history("BTCUSDT", days=1)
        assert len(results) >= 1
        assert results[0]["screened"] is True
        assert results[0]["decision"]["action"] == "long"
        assert results[0]["approved"] is True

    def test_symbol_not_found(self):
        """查不到的币种返回空"""
        save_archive(_make_state())
        results = get_symbol_history("XYZUSDT", days=1)
        assert len(results) == 0

    def test_days_filter(self):
        """days 参数过滤"""
        save_archive(_make_state())
        results = get_symbol_history("BTCUSDT", days=0)
        assert isinstance(results, list)


class TestScreeningScoressInState:
    def test_screen_returns_scores(self):
        """screen 节点应返回 screening_scores"""
        from cryptobot.workflow.nodes.screen import screen

        state = {
            "market_data": {
                "BTCUSDT": {
                    "tech": {
                        "signals": {"technical_score": 5},
                        "momentum": {"rsi_14": 65},
                        "trend": {"macd_cross": "none"},
                        "volatility": {"atr_pct": 2},
                    },
                    "crypto": {"composite": {"score": 3}},
                },
            },
            "errors": [],
            "capital_tier": {"tier": "small", "params": {"max_coins": 5}},
        }

        with patch("cryptobot.config.get_pair_config", return_value=None), \
             patch("cryptobot.data.news.get_coin_info", return_value=None), \
             patch("cryptobot.data.crypto_news.get_coin_specific_news", return_value=None), \
             patch("cryptobot.freqtrade_api.ft_api_get", return_value=[]):
            result = screen(state)

        assert "screening_scores" in result
        assert isinstance(result["screening_scores"], list)
        assert len(result["screening_scores"]) >= 1
        sym, score = result["screening_scores"][0]
        assert sym == "BTCUSDT"
        assert isinstance(score, (int, float))


class TestRiskDetailsInState:
    def test_risk_review_returns_details(self):
        """risk_review 应返回 risk_details (只有 no_trade 决策时)"""
        from cryptobot.workflow.nodes.risk import risk_review

        state = {
            "decisions": [{"symbol": "BTCUSDT", "action": "no_trade"}],
            "errors": [],
            "market_regime": {"regime": "trending"},
            "capital_tier": {},
            "analyses": {},
            "portfolio_context": "test context",
        }

        def _ft_api_side_effect(endpoint):
            if endpoint == "/status":
                return []
            if endpoint == "/balance":
                return {"currencies": [{"currency": "USDT", "balance": 1000, "free": 800, "used": 200}]}
            return None

        with patch("cryptobot.config.load_settings", return_value={"risk": {}}), \
             patch("cryptobot.signal.bridge.read_signals", return_value=[]), \
             patch("cryptobot.freqtrade_api.ft_api_get", side_effect=_ft_api_side_effect), \
             patch("cryptobot.capital_strategy._extract_usdt_balance", return_value=1000):
            result = risk_review(state)

        assert "risk_details" in result
        assert "hard_rule_results" in result["risk_details"]
        assert "ai_review_results" in result["risk_details"]
        assert "rejected_signals" in result["risk_details"]


class TestArchiveOnEarlyExit:
    def test_should_risk_review_end_archives(self, _tmp_archive_dir):
        """trade → END 路径也应触发归档"""
        from cryptobot.workflow.graph import should_risk_review

        state = _make_state(decisions=[{"action": "no_trade"}])

        with patch("cryptobot.notify.notify_workflow_summary"):
            result = should_risk_review(state)

        assert result == "__end__"
        json_files = list(_tmp_archive_dir.rglob("*.json"))
        assert len(json_files) == 1

    def test_should_execute_end_archives(self, _tmp_archive_dir):
        """risk_review → END 路径也应触发归档"""
        from cryptobot.workflow.graph import should_execute

        state = _make_state(approved_signals=[])

        with patch("cryptobot.notify.notify_workflow_summary"):
            result = should_execute(state)

        assert result == "__end__"
        json_files = list(_tmp_archive_dir.rglob("*.json"))
        assert len(json_files) == 1


class TestGenerateRunId:
    def test_format(self):
        run_id = _generate_run_id("trending")
        parts = run_id.split("_")
        assert len(parts) == 3
        assert parts[2] == "trending"
        assert len(parts[0]) == 8  # YYYYMMDD
        assert len(parts[1]) == 4  # HHMM

    def test_unknown_regime(self):
        run_id = _generate_run_id("")
        assert run_id.endswith("_unknown")
