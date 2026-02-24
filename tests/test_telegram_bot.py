"""Telegram Bot 长轮询测试"""

import threading
from unittest.mock import patch, MagicMock

from cryptobot.telegram.bot import start_bot_thread, _poll_loop, _send_reply


class TestStartBotThread:
    @patch("cryptobot.telegram.bot._get_config", return_value=None)
    def test_returns_none_when_not_configured(self, mock_cfg):
        result = start_bot_thread()
        assert result is None

    @patch("cryptobot.telegram.bot._poll_loop")
    @patch("cryptobot.telegram.bot._get_config", return_value=("tok", "123"))
    def test_returns_thread_when_configured(self, mock_cfg, mock_poll):
        t = start_bot_thread()
        assert isinstance(t, threading.Thread)
        assert t.daemon is True
        assert t.name == "telegram-bot"
        t.join(timeout=1)


class TestPollLoop:
    @patch("cryptobot.telegram.bot._send_reply")
    @patch("cryptobot.telegram.bot.httpx.get")
    def test_processes_command_from_correct_chat(self, mock_get, mock_reply):
        """正确 chat_id 的命令应被处理"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "result": [{
                "update_id": 100,
                "message": {
                    "chat": {"id": 123},
                    "text": "/help",
                },
            }],
        }
        # 第二次调用抛异常以退出循环
        mock_get.side_effect = [mock_resp, KeyboardInterrupt]

        try:
            _poll_loop("tok", "123")
        except KeyboardInterrupt:
            pass

        mock_reply.assert_called_once()
        args = mock_reply.call_args[0]
        assert args[0] == "tok"
        assert args[1] == "123"
        assert "命令" in args[2] or "CryptoBot" in args[2]  # /help 回复

    @patch("cryptobot.telegram.bot._send_reply")
    @patch("cryptobot.telegram.bot.httpx.get")
    def test_ignores_wrong_chat_id(self, mock_get, mock_reply):
        """错误 chat_id 的消息应被忽略"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "result": [{
                "update_id": 100,
                "message": {
                    "chat": {"id": 999},
                    "text": "/help",
                },
            }],
        }
        mock_get.side_effect = [mock_resp, KeyboardInterrupt]

        try:
            _poll_loop("tok", "123")
        except KeyboardInterrupt:
            pass

        mock_reply.assert_not_called()

    @patch("cryptobot.telegram.bot._send_reply")
    @patch("cryptobot.telegram.bot.httpx.get")
    def test_ignores_non_command_text(self, mock_get, mock_reply):
        """非命令文本应被忽略"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "result": [{
                "update_id": 100,
                "message": {
                    "chat": {"id": 123},
                    "text": "hello world",
                },
            }],
        }
        mock_get.side_effect = [mock_resp, KeyboardInterrupt]

        try:
            _poll_loop("tok", "123")
        except KeyboardInterrupt:
            pass

        mock_reply.assert_not_called()


class TestSendReply:
    @patch("cryptobot.telegram.bot.httpx.post")
    def test_sends_with_markdown(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        _send_reply("tok", "123", "hello")
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["json"]["parse_mode"] == "Markdown"
        assert call_kwargs[1]["json"]["text"] == "hello"

    @patch("cryptobot.telegram.bot.httpx.post", side_effect=Exception("network"))
    def test_handles_send_failure(self, mock_post):
        """发送失败不应抛异常"""
        _send_reply("tok", "123", "hello")  # should not raise
