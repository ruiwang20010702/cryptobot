"""execute 节点测试

覆盖:
- 信号写入正常路径
- 无 approved_signals 时跳过
- realtime.enabled 分支
- pending_signals 写入
- 异常处理路径
- 每轮分析摘要通知
"""

from unittest.mock import patch, MagicMock

import pytest

from cryptobot.workflow.nodes.execute import execute


def _make_signal(symbol="BTCUSDT", action="long"):
    return {
        "symbol": symbol,
        "action": action,
        "leverage": 3,
        "entry_price_range": [60000, 61000],
        "stop_loss": 59000,
        "take_profit": [63000],
        "confidence": 75,
        "position_size_usdt": 100,
        "analysis_summary": {},
        "timestamp": "2026-01-01T00:00:00+00:00",
    }


def _base_state(**overrides):
    state = {
        "approved_signals": [],
        "errors": [],
        "screened_symbols": [],
        "decisions": [],
        "market_regime": {},
        "capital_tier": {},
        "fear_greed": {},
    }
    state.update(overrides)
    return state


# 所有测试共用的 lazy import 路径 (execute.py 内部 from ... import)
_PATCHES = {
    "settings": "cryptobot.config.load_settings",
    "write_signal": "cryptobot.signal.bridge.write_signal",
    "write_pending": "cryptobot.signal.bridge.write_pending_signal",
    "notify_signal": "cryptobot.notify.notify_new_signal",
    "notify_error": "cryptobot.notify.notify_workflow_error",
    "notify_summary": "cryptobot.notify.notify_workflow_summary",
    "save_record": "cryptobot.journal.storage.save_record",
    "signal_record": "cryptobot.journal.models.SignalRecord",
    "get_version": "cryptobot.workflow.prompts.get_prompt_version",
}


class TestExecuteNode:
    """execute 节点基本流程"""

    @patch(_PATCHES["notify_summary"])
    @patch(_PATCHES["notify_signal"])
    @patch(_PATCHES["save_record"])
    @patch(_PATCHES["signal_record"])
    @patch(_PATCHES["write_signal"])
    @patch(_PATCHES["settings"], return_value={"realtime": {"enabled": False}})
    def test_writes_signal_json(
        self, mock_settings, mock_write, mock_record_cls,
        mock_save, mock_notify, mock_summary,
    ):
        """正常路径: 写入 signal.json"""
        sig = _make_signal()
        mock_write.return_value = sig
        mock_record_cls.from_signal.return_value = MagicMock(signal_id="test-1")

        result = execute(_base_state(approved_signals=[sig]))

        assert len(result["executed"]) == 1
        mock_write.assert_called_once()
        mock_notify.assert_called_once_with(sig)
        mock_save.assert_called_once()

    @patch(_PATCHES["notify_summary"])
    @patch(_PATCHES["write_signal"])
    @patch(_PATCHES["settings"], return_value={"realtime": {"enabled": False}})
    def test_no_signals_skips(self, mock_settings, mock_write, mock_summary):
        """无 approved_signals 时不写入"""
        result = execute(_base_state())

        assert result["executed"] == []
        mock_write.assert_not_called()

    @patch(_PATCHES["notify_summary"])
    @patch(_PATCHES["notify_signal"])
    @patch(_PATCHES["save_record"])
    @patch(_PATCHES["signal_record"])
    @patch(_PATCHES["write_pending"])
    @patch(_PATCHES["settings"], return_value={"realtime": {"enabled": True}})
    def test_realtime_writes_pending(
        self, mock_settings, mock_write_pending, mock_record_cls,
        mock_save, mock_notify, mock_summary,
    ):
        """realtime.enabled=True 时写入 pending_signals.json"""
        sig = _make_signal()
        mock_write_pending.return_value = sig
        mock_record_cls.from_signal.return_value = MagicMock(signal_id="test-1")

        result = execute(_base_state(approved_signals=[sig]))

        assert len(result["executed"]) == 1
        mock_write_pending.assert_called_once()

    @patch(_PATCHES["notify_summary"])
    @patch(_PATCHES["notify_error"])
    @patch(_PATCHES["write_signal"], side_effect=IOError("disk full"))
    @patch(_PATCHES["settings"], return_value={"realtime": {"enabled": False}})
    def test_write_failure_records_error(
        self, mock_settings, mock_write, mock_err_notify, mock_summary,
    ):
        """写入失败时记录错误"""
        sig = _make_signal()
        result = execute(_base_state(approved_signals=[sig]))

        assert len(result["executed"]) == 0
        assert any("execute_BTCUSDT" in e for e in result["errors"])

    @patch(_PATCHES["notify_summary"])
    @patch(_PATCHES["notify_error"])
    @patch(_PATCHES["write_signal"], side_effect=IOError("fail"))
    @patch(_PATCHES["settings"], return_value={"realtime": {"enabled": False}})
    def test_many_errors_sends_notification(
        self, mock_settings, mock_write, mock_err_notify, mock_summary,
    ):
        """>=3 个错误时发送通知"""
        sigs = [_make_signal(f"SYM{i}USDT") for i in range(4)]
        result = execute(_base_state(approved_signals=sigs))

        assert len(result["errors"]) >= 3
        mock_err_notify.assert_called_once()

    @patch(_PATCHES["notify_summary"])
    @patch(_PATCHES["notify_signal"])
    @patch(_PATCHES["save_record"], side_effect=Exception("db error"))
    @patch(_PATCHES["signal_record"])
    @patch(_PATCHES["write_signal"])
    @patch(_PATCHES["settings"], return_value={"realtime": {"enabled": False}})
    def test_journal_failure_does_not_block(
        self, mock_settings, mock_write, mock_record_cls,
        mock_save, mock_notify, mock_summary,
    ):
        """交易日志记录失败不影响信号写入"""
        sig = _make_signal()
        mock_write.return_value = sig
        mock_record_cls.from_signal.return_value = MagicMock()

        result = execute(_base_state(approved_signals=[sig]))

        assert len(result["executed"]) == 1

    @patch(_PATCHES["notify_summary"])
    @patch(_PATCHES["notify_signal"])
    @patch(_PATCHES["save_record"])
    @patch(_PATCHES["signal_record"])
    @patch(_PATCHES["write_signal"])
    @patch(_PATCHES["settings"], return_value={"realtime": {"enabled": False}})
    def test_model_id_recorded(
        self, mock_settings, mock_write, mock_record_cls,
        mock_save, mock_notify, mock_summary,
    ):
        """竞赛模式: model_id 被记录到 journal"""
        sig = _make_signal()
        sig["model_id"] = "deepseek-chat"
        mock_write.return_value = sig
        mock_record = MagicMock(signal_id="test-1", symbol="BTCUSDT", action="long")
        mock_record_cls.from_signal.return_value = mock_record

        with patch("cryptobot.evolution.model_competition.record_competition_result"):
            result = execute(_base_state(approved_signals=[sig]))

        assert mock_record.model_id == "deepseek-chat"

    @patch(_PATCHES["notify_summary"], side_effect=Exception("tg fail"))
    @patch(_PATCHES["settings"], return_value={"realtime": {"enabled": False}})
    def test_summary_failure_does_not_crash(self, mock_settings, mock_summary):
        """分析摘要通知失败不影响主流程"""
        result = execute(_base_state())
        assert "errors" in result

    @patch(_PATCHES["notify_summary"])
    @patch(_PATCHES["notify_signal"])
    @patch(_PATCHES["save_record"])
    @patch(_PATCHES["signal_record"])
    @patch(_PATCHES["write_signal"])
    @patch(_PATCHES["get_version"], return_value="v1.2")
    @patch(_PATCHES["settings"], return_value={"realtime": {"enabled": False}})
    def test_prompt_version_injected(
        self, mock_settings, mock_version, mock_write, mock_record_cls,
        mock_save, mock_notify, mock_summary,
    ):
        """prompt_version 被注入到信号"""
        sig = _make_signal()
        mock_write.return_value = sig
        mock_record_cls.from_signal.return_value = MagicMock(signal_id="test-1")

        execute(_base_state(approved_signals=[sig]))

        # write_signal 调用时信号应包含 prompt_version
        call_args = mock_write.call_args[0][0]
        assert call_args["prompt_version"] == "v1.2"
