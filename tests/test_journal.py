"""交易记录测试

覆盖: models、storage CRUD、analytics 计算、CLI 命令
"""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from cryptobot.journal.models import SignalRecord
from cryptobot.journal.storage import (
    save_record,
    get_record,
    get_all_records,
    get_records_by_status,
    get_records_by_symbol,
    update_record,
    find_active_record_for_symbol,
    RECORDS_FILE,
    JOURNAL_DIR,
)
from cryptobot.journal.analytics import calc_performance, build_performance_summary, calc_analyst_accuracy
from cryptobot.cli.journal import journal


# ─── Models ────────────────────────────────────────────────────────────────

class TestSignalRecord:
    def test_from_signal(self):
        """从 execute 信号 dict 创建记录"""
        signal = {
            "symbol": "BTCUSDT",
            "action": "long",
            "timestamp": "2026-01-01T00:00:00",
            "confidence": 75,
            "entry_price_range": [94000, 96000],
            "stop_loss": 91000,
            "take_profit": [{"price": 100000, "close_pct": 100}],
            "leverage": 3,
            "position_size_usdt": 2000,
            "analysis_summary": {"reasoning": "看多", "risk_score": 35},
        }
        record = SignalRecord.from_signal(signal)
        assert record.symbol == "BTCUSDT"
        assert record.action == "long"
        assert record.confidence == 75
        assert record.status == "pending"
        assert record.reasoning == "看多"
        assert record.risk_score == 35
        assert len(record.signal_id) == 12

    def test_to_dict_and_from_dict(self):
        """序列化/反序列化"""
        record = SignalRecord(
            signal_id="abc123",
            symbol="ETHUSDT",
            action="short",
            confidence=80,
        )
        d = record.to_dict()
        restored = SignalRecord.from_dict(d)
        assert restored.signal_id == "abc123"
        assert restored.symbol == "ETHUSDT"
        assert restored.action == "short"

    def test_from_dict_ignores_unknown_fields(self):
        """未知字段不报错"""
        d = {"signal_id": "x", "unknown_field": 42, "symbol": "BTC"}
        record = SignalRecord.from_dict(d)
        assert record.signal_id == "x"
        assert record.symbol == "BTC"

    def test_analyst_votes_roundtrip(self):
        """analyst_votes 序列化/反序列化"""
        votes = {"technical": "bullish", "onchain": "bearish", "sentiment": "neutral"}
        record = SignalRecord(signal_id="v1", analyst_votes=votes)
        d = record.to_dict()
        assert d["analyst_votes"] == votes

        restored = SignalRecord.from_dict(d)
        assert restored.analyst_votes == votes

    def test_prompt_version_roundtrip(self):
        """prompt_version 序列化/反序列化"""
        record = SignalRecord(signal_id="p1", prompt_version="v1.0")
        d = record.to_dict()
        assert d["prompt_version"] == "v1.0"

        restored = SignalRecord.from_dict(d)
        assert restored.prompt_version == "v1.0"

    def test_new_fields_default_none(self):
        """新字段默认 None，向后兼容"""
        record = SignalRecord(signal_id="n1")
        assert record.analyst_votes is None
        assert record.prompt_version is None

    def test_from_signal_with_new_fields(self):
        """from_signal 透传 analyst_votes 和 prompt_version"""
        signal = {
            "symbol": "ETHUSDT",
            "action": "short",
            "analyst_votes": {"technical": "bearish"},
            "prompt_version": "v1.0",
            "analysis_summary": {},
        }
        record = SignalRecord.from_signal(signal)
        assert record.analyst_votes == {"technical": "bearish"}
        assert record.prompt_version == "v1.0"


# ─── Storage ──────────────────────────────────────────────────────────────

class TestStorage:
    @pytest.fixture(autouse=True)
    def clean_journal(self, tmp_path, monkeypatch):
        """每个测试用 tmp 目录"""
        journal_dir = tmp_path / "journal"
        journal_dir.mkdir()
        records_file = journal_dir / "records.json"
        monkeypatch.setattr("cryptobot.journal.storage.JOURNAL_DIR", journal_dir)
        monkeypatch.setattr("cryptobot.journal.storage.RECORDS_FILE", records_file)

    def test_save_and_get(self):
        record = SignalRecord(signal_id="r1", symbol="BTCUSDT", action="long")
        save_record(record)

        found = get_record("r1")
        assert found is not None
        assert found.symbol == "BTCUSDT"

    def test_save_replaces_same_id(self):
        r1 = SignalRecord(signal_id="r1", symbol="BTCUSDT", confidence=50)
        save_record(r1)

        r1_updated = SignalRecord(signal_id="r1", symbol="BTCUSDT", confidence=80)
        save_record(r1_updated)

        all_records = get_all_records()
        assert len(all_records) == 1
        assert all_records[0].confidence == 80

    def test_get_nonexistent(self):
        assert get_record("nonexistent") is None

    def test_get_by_status(self):
        save_record(SignalRecord(signal_id="r1", status="pending"))
        save_record(SignalRecord(signal_id="r2", status="active"))
        save_record(SignalRecord(signal_id="r3", status="closed"))

        pending = get_records_by_status("pending")
        assert len(pending) == 1
        assert pending[0].signal_id == "r1"

    def test_get_by_symbol(self):
        save_record(SignalRecord(signal_id="r1", symbol="BTCUSDT"))
        save_record(SignalRecord(signal_id="r2", symbol="ETHUSDT"))
        save_record(SignalRecord(signal_id="r3", symbol="BTCUSDT"))

        btc = get_records_by_symbol("BTCUSDT")
        assert len(btc) == 2

    def test_update_record(self):
        save_record(SignalRecord(signal_id="r1", status="pending"))

        result = update_record("r1", status="active", actual_entry_price=95000.0)
        assert result is True

        found = get_record("r1")
        assert found.status == "active"
        assert found.actual_entry_price == 95000.0

    def test_update_nonexistent(self):
        assert update_record("nonexistent", status="closed") is False

    def test_find_active_record(self):
        save_record(SignalRecord(
            signal_id="r1", symbol="BTCUSDT", status="active",
            timestamp="2026-01-01T00:00:00",
        ))
        save_record(SignalRecord(
            signal_id="r2", symbol="BTCUSDT", status="active",
            timestamp="2026-01-02T00:00:00",
        ))

        found = find_active_record_for_symbol("BTCUSDT")
        assert found is not None
        assert found.signal_id == "r2"  # 最新的

    def test_find_active_none(self):
        save_record(SignalRecord(signal_id="r1", symbol="BTCUSDT", status="closed"))
        assert find_active_record_for_symbol("BTCUSDT") is None


# ─── Analytics ─────────────────────────────────────────────────────────────

class TestAnalytics:
    @pytest.fixture(autouse=True)
    def setup_records(self, tmp_path, monkeypatch):
        journal_dir = tmp_path / "journal"
        journal_dir.mkdir()
        records_file = journal_dir / "records.json"
        monkeypatch.setattr("cryptobot.journal.storage.JOURNAL_DIR", journal_dir)
        monkeypatch.setattr("cryptobot.journal.storage.RECORDS_FILE", records_file)

    def test_empty_performance(self):
        perf = calc_performance(30)
        assert perf["total_signals"] == 0
        assert perf["win_rate"] == 0
        assert perf["closed"] == 0

    def test_win_rate_calculation(self):
        # 3 closed: 2 wins, 1 loss
        save_record(SignalRecord(
            signal_id="w1", status="closed", action="long",
            timestamp="2026-02-15T00:00:00",
            actual_pnl_pct=5.0, actual_pnl_usdt=500,
            confidence=75,
        ))
        save_record(SignalRecord(
            signal_id="w2", status="closed", action="long",
            timestamp="2026-02-15T01:00:00",
            actual_pnl_pct=3.0, actual_pnl_usdt=300,
            confidence=85,
        ))
        save_record(SignalRecord(
            signal_id="l1", status="closed", action="short",
            timestamp="2026-02-15T02:00:00",
            actual_pnl_pct=-2.0, actual_pnl_usdt=-200,
            confidence=70,
        ))

        perf = calc_performance(30)
        assert perf["closed"] == 3
        assert perf["win_rate"] == pytest.approx(0.667, abs=0.01)
        assert perf["avg_pnl_pct"] == 2.0
        assert perf["total_pnl_usdt"] == 600.0
        assert perf["by_direction"]["long"]["count"] == 2
        assert perf["by_direction"]["short"]["count"] == 1

    def test_confidence_calibration(self):
        save_record(SignalRecord(
            signal_id="c1", status="closed", confidence=75,
            timestamp="2026-02-15T00:00:00",
            actual_pnl_pct=5.0,
        ))
        save_record(SignalRecord(
            signal_id="c2", status="closed", confidence=75,
            timestamp="2026-02-15T01:00:00",
            actual_pnl_pct=-1.0,
        ))

        perf = calc_performance(30)
        cal = perf["confidence_calibration"]
        assert cal["70-80"]["count"] == 2
        assert cal["70-80"]["actual_win_rate"] == 0.5

    def test_profit_factor(self):
        save_record(SignalRecord(
            signal_id="p1", status="closed",
            timestamp="2026-02-15T00:00:00",
            actual_pnl_pct=10.0,
        ))
        save_record(SignalRecord(
            signal_id="p2", status="closed",
            timestamp="2026-02-15T01:00:00",
            actual_pnl_pct=-5.0,
        ))

        perf = calc_performance(30)
        assert perf["profit_factor"] == 2.0


# ─── Analyst Accuracy ─────────────────────────────────────────────────────

class TestAnalystAccuracy:
    @pytest.fixture(autouse=True)
    def setup_records(self, tmp_path, monkeypatch):
        journal_dir = tmp_path / "journal"
        journal_dir.mkdir()
        records_file = journal_dir / "records.json"
        monkeypatch.setattr("cryptobot.journal.storage.JOURNAL_DIR", journal_dir)
        monkeypatch.setattr("cryptobot.journal.storage.RECORDS_FILE", records_file)

    def test_correct_and_incorrect_votes(self):
        """正确/错误投票计数"""
        # long + bullish + 盈利 → 正确
        save_record(SignalRecord(
            signal_id="a1", status="closed", action="long",
            timestamp="2026-02-15T00:00:00",
            actual_pnl_pct=5.0,
            analyst_votes={"technical": "bullish", "onchain": "bearish"},
        ))
        # short + bearish + 盈利 → 正确
        save_record(SignalRecord(
            signal_id="a2", status="closed", action="short",
            timestamp="2026-02-15T01:00:00",
            actual_pnl_pct=3.0,
            analyst_votes={"technical": "bearish", "onchain": "bearish"},
        ))
        # long + bullish + 亏损 → 错误
        save_record(SignalRecord(
            signal_id="a3", status="closed", action="long",
            timestamp="2026-02-15T02:00:00",
            actual_pnl_pct=-2.0,
            analyst_votes={"technical": "bullish", "onchain": "neutral"},
        ))

        result = calc_analyst_accuracy(30)
        # technical: a1 正确, a2 正确, a3 错误 → 2/3
        assert result["technical"]["total"] == 3
        assert result["technical"]["correct"] == 2
        assert result["technical"]["accuracy"] == pytest.approx(0.667, abs=0.01)
        # onchain: a1 bearish+long+win → 错误, a2 bearish+short+win → 正确,
        #          a3 neutral+long+loss → neutral≠bullish, not agree, loss → 正确
        assert result["onchain"]["total"] == 3
        assert result["onchain"]["correct"] == 2

    def test_no_votes_skipped(self):
        """无 analyst_votes 的记录被跳过"""
        save_record(SignalRecord(
            signal_id="n1", status="closed", action="long",
            timestamp="2026-02-15T00:00:00",
            actual_pnl_pct=5.0,
            analyst_votes=None,
        ))
        result = calc_analyst_accuracy(30)
        assert result == {}

    def test_empty_records(self):
        """空记录返回空 dict"""
        result = calc_analyst_accuracy(30)
        assert result == {}


# ─── By Symbol ────────────────────────────────────────────────────────────

class TestBySymbol:
    @pytest.fixture(autouse=True)
    def setup_records(self, tmp_path, monkeypatch):
        journal_dir = tmp_path / "journal"
        journal_dir.mkdir()
        records_file = journal_dir / "records.json"
        monkeypatch.setattr("cryptobot.journal.storage.JOURNAL_DIR", journal_dir)
        monkeypatch.setattr("cryptobot.journal.storage.RECORDS_FILE", records_file)

    def test_by_symbol_grouping(self):
        """按币种分组统计"""
        save_record(SignalRecord(
            signal_id="s1", status="closed", symbol="BTCUSDT",
            timestamp="2026-02-15T00:00:00",
            actual_pnl_pct=5.0, actual_pnl_usdt=500,
        ))
        save_record(SignalRecord(
            signal_id="s2", status="closed", symbol="BTCUSDT",
            timestamp="2026-02-15T01:00:00",
            actual_pnl_pct=-2.0, actual_pnl_usdt=-200,
        ))
        save_record(SignalRecord(
            signal_id="s3", status="closed", symbol="ETHUSDT",
            timestamp="2026-02-15T02:00:00",
            actual_pnl_pct=3.0, actual_pnl_usdt=300,
        ))

        perf = calc_performance(30)
        by_sym = perf["by_symbol"]
        assert "BTCUSDT" in by_sym
        assert by_sym["BTCUSDT"]["count"] == 2
        assert by_sym["BTCUSDT"]["win_rate"] == 0.5
        assert by_sym["BTCUSDT"]["avg_pnl_pct"] == 1.5
        assert "ETHUSDT" in by_sym
        assert by_sym["ETHUSDT"]["count"] == 1
        assert by_sym["ETHUSDT"]["win_rate"] == 1.0


# ─── Performance Summary ──────────────────────────────────────────────────

class TestPerformanceSummary:
    @pytest.fixture(autouse=True)
    def setup_records(self, tmp_path, monkeypatch):
        journal_dir = tmp_path / "journal"
        journal_dir.mkdir()
        records_file = journal_dir / "records.json"
        monkeypatch.setattr("cryptobot.journal.storage.JOURNAL_DIR", journal_dir)
        monkeypatch.setattr("cryptobot.journal.storage.RECORDS_FILE", records_file)

    def test_empty_returns_empty_string(self):
        """无记录时返回空字符串"""
        assert build_performance_summary(30) == ""

    def test_insufficient_data_returns_empty(self):
        """不足 3 笔平仓时返回空字符串"""
        save_record(SignalRecord(
            signal_id="w1", status="closed", action="long",
            timestamp="2026-02-15T00:00:00",
            actual_pnl_pct=5.0, confidence=75,
        ))
        save_record(SignalRecord(
            signal_id="w2", status="closed", action="long",
            timestamp="2026-02-15T01:00:00",
            actual_pnl_pct=3.0, confidence=80,
        ))
        assert build_performance_summary(30) == ""

    def test_summary_with_enough_data(self):
        """3 笔以上平仓时生成摘要"""
        for i, (pnl, action) in enumerate([
            (5.0, "long"), (3.0, "long"), (-2.0, "short"), (1.5, "long"),
        ]):
            save_record(SignalRecord(
                signal_id=f"r{i}", status="closed", action=action,
                symbol="BTCUSDT",
                timestamp=f"2026-02-15T0{i}:00:00",
                actual_pnl_pct=pnl, actual_pnl_usdt=pnl * 100,
                confidence=75,
            ))

        summary = build_performance_summary(30)
        assert "近期表现参考" in summary
        assert "胜率" in summary
        assert "Profit Factor" in summary
        assert "多单" in summary
        assert "空单" in summary
        assert "最近" in summary
        assert "BTCUSDT" in summary

    def test_summary_includes_calibration_bias(self):
        """置信度偏差超过 10% 时提示"""
        # 4 笔 confidence=75，全部亏损 → 实际胜率 0% vs 预期 75%
        for i in range(4):
            save_record(SignalRecord(
                signal_id=f"c{i}", status="closed", confidence=75,
                timestamp=f"2026-02-15T0{i}:00:00",
                actual_pnl_pct=-1.0,
            ))

        summary = build_performance_summary(30)
        assert "偏乐观" in summary

    def test_old_records_excluded(self):
        """超出 days 范围的记录不计入"""
        for i in range(4):
            save_record(SignalRecord(
                signal_id=f"old{i}", status="closed",
                timestamp="2025-01-01T00:00:00",  # 很久以前
                actual_pnl_pct=5.0, confidence=80,
            ))
        assert build_performance_summary(30) == ""


# ─── CLI ───────────────────────────────────────────────────────────────────

class TestJournalCLI:
    def test_journal_help(self):
        runner = CliRunner()
        result = runner.invoke(journal, ["--help"])
        assert result.exit_code == 0
        assert "交易记录与绩效" in result.output

    def test_show_help(self):
        runner = CliRunner()
        result = runner.invoke(journal, ["show", "--help"])
        assert result.exit_code == 0
        assert "--status" in result.output

    def test_stats_help(self):
        runner = CliRunner()
        result = runner.invoke(journal, ["stats", "--help"])
        assert result.exit_code == 0
        assert "--days" in result.output

    def test_stats_json_empty(self, tmp_path, monkeypatch):
        journal_dir = tmp_path / "journal"
        journal_dir.mkdir()
        monkeypatch.setattr("cryptobot.journal.storage.JOURNAL_DIR", journal_dir)
        monkeypatch.setattr("cryptobot.journal.storage.RECORDS_FILE", journal_dir / "records.json")

        runner = CliRunner()
        result = runner.invoke(journal, ["stats", "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["total_signals"] == 0
