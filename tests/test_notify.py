"""Telegram 通知测试

覆盖: 配置读取、消息发送、模板函数、未配置静默跳过
"""

from unittest.mock import patch, MagicMock

import pytest

from cryptobot.notify import (
    _get_config,
    send_message,
    notify_new_signal,
    notify_risk_rejected,
    notify_stop_loss_adjusted,
    notify_alert,
    notify_workflow_error,
)


class TestGetConfig:
    @patch("cryptobot.notify.load_settings")
    def test_returns_none_when_disabled(self, mock_settings):
        mock_settings.return_value = {"telegram": {"enabled": False}}
        assert _get_config() is None

    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}, clear=False)
    @patch("cryptobot.notify.load_settings")
    def test_returns_none_when_no_token(self, mock_settings):
        mock_settings.return_value = {
            "telegram": {"enabled": True, "bot_token": "", "chat_id": "123"},
        }
        assert _get_config() is None

    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}, clear=False)
    @patch("cryptobot.notify.load_settings")
    def test_returns_tuple_when_configured(self, mock_settings):
        mock_settings.return_value = {
            "telegram": {"enabled": True, "bot_token": "tok", "chat_id": "123"},
        }
        result = _get_config()
        assert result == ("tok", "123")

    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "env_tok", "TELEGRAM_CHAT_ID": "env_id"})
    @patch("cryptobot.notify.load_settings")
    def test_env_vars_override(self, mock_settings):
        """环境变量优先于配置文件"""
        mock_settings.return_value = {
            "telegram": {"enabled": True, "bot_token": "file_tok", "chat_id": "file_id"},
        }
        result = _get_config()
        assert result == ("env_tok", "env_id")


class TestSendMessage:
    @patch("cryptobot.notify._get_config", return_value=None)
    def test_returns_false_when_not_configured(self, mock_cfg):
        assert send_message("test") is False

    @patch("cryptobot.notify.httpx.post")
    @patch("cryptobot.notify._get_config", return_value=("tok", "123"))
    def test_sends_to_telegram_api(self, mock_cfg, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        result = send_message("hello")

        assert result is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "bot" in call_kwargs[0][0]  # URL 包含 bot token
        assert call_kwargs[1]["json"]["text"] == "hello"
        assert call_kwargs[1]["json"]["chat_id"] == "123"

    @patch("cryptobot.notify.httpx.post")
    @patch("cryptobot.notify._get_config", return_value=("tok", "123"))
    def test_returns_false_on_http_error(self, mock_cfg, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad Request"
        mock_post.return_value = mock_resp

        assert send_message("test") is False

    @patch("cryptobot.notify.httpx.post", side_effect=Exception("timeout"))
    @patch("cryptobot.notify._get_config", return_value=("tok", "123"))
    def test_returns_false_on_exception(self, mock_cfg, mock_post):
        assert send_message("test") is False


class TestNotifyTemplates:
    """测试各模板函数生成正确消息"""

    @patch("cryptobot.notify.send_message", return_value=True)
    def test_notify_new_signal(self, mock_send):
        signal = {
            "action": "long",
            "symbol": "BTCUSDT",
            "leverage": 3,
            "confidence": 75,
            "entry_price_range": [94000, 96000],
            "stop_loss": 91000,
            "position_size_usdt": 2000,
        }
        result = notify_new_signal(signal)
        assert result is True
        text = mock_send.call_args[0][0]
        assert "LONG" in text
        assert "BTCUSDT" in text
        assert "~94.0k" in text  # 价格模糊化

    @patch("cryptobot.notify.send_message", return_value=True)
    def test_notify_risk_rejected(self, mock_send):
        result = notify_risk_rejected("ETHUSDT", "杠杆过高")
        assert result is True
        text = mock_send.call_args[0][0]
        assert "ETHUSDT" in text
        assert "杠杆过高" in text

    @patch("cryptobot.notify.send_message", return_value=True)
    def test_notify_stop_loss_adjusted(self, mock_send):
        result = notify_stop_loss_adjusted("BTCUSDT", 92000.0, 93500.0)
        assert result is True
        text = mock_send.call_args[0][0]
        assert "BTCUSDT" in text
        assert "93500" in text

    @patch("cryptobot.notify.send_message", return_value=True)
    def test_notify_alert(self, mock_send):
        result = notify_alert("CRITICAL", "爆仓距离 15%")
        assert result is True
        text = mock_send.call_args[0][0]
        assert "CRITICAL" in text
        assert "爆仓距离" in text

    @patch("cryptobot.notify.send_message", return_value=True)
    def test_notify_workflow_error(self, mock_send):
        result = notify_workflow_error(3, ["err1", "err2", "err3"])
        assert result is True
        text = mock_send.call_args[0][0]
        assert "3" in text
        assert "err1" in text


class TestSilentWhenNotConfigured:
    """未配置 Telegram 时所有通知静默返回 False"""

    @patch("cryptobot.notify._get_config", return_value=None)
    def test_all_templates_silent(self, mock_cfg):
        assert notify_new_signal({"action": "long", "symbol": "X"}) is False
        assert notify_risk_rejected("X", "reason") is False
        assert notify_stop_loss_adjusted("X", None, 100.0) is False
        assert notify_alert("WARNING", "msg") is False
        assert notify_workflow_error(1, ["e"]) is False
