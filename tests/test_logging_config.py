"""Tests for cryptobot.logging_config"""

import json
import logging
import logging.handlers

import pytest

from cryptobot.logging_config import JsonFormatter, setup_logging


@pytest.fixture(autouse=True)
def _cleanup_logging():
    yield
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)


class TestJsonFormatter:
    def test_format_outputs_valid_json(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        result = json.loads(formatter.format(record))
        assert result["level"] == "INFO"
        assert result["logger"] == "test.logger"
        assert result["msg"] == "hello world"
        assert "ts" in result

    def test_format_includes_exception(self):
        formatter = JsonFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname="test.py",
                lineno=1,
                msg="error occurred",
                args=(),
                exc_info=True,
            )
            import sys

            record.exc_info = sys.exc_info()

        result = json.loads(formatter.format(record))
        assert "exception" in result
        assert "ValueError" in result["exception"]

    def test_format_includes_extra_fields(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="trade",
            args=(),
            exc_info=None,
        )
        record.symbol = "BTCUSDT"
        record.node = "analyze"
        record.duration = 1.5
        result = json.loads(formatter.format(record))
        assert result["symbol"] == "BTCUSDT"
        assert result["node"] == "analyze"
        assert result["duration"] == 1.5


class TestSetupLogging:
    def test_json_format_true_uses_json_formatter(self):
        setup_logging(json_format=True)
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)

    def test_json_format_false_uses_text_formatter(self):
        setup_logging(json_format=False)
        root = logging.getLogger()
        assert len(root.handlers) == 1
        formatter = root.handlers[0].formatter
        assert not isinstance(formatter, JsonFormatter)
        assert "%(asctime)s" in formatter._fmt

    def test_noisy_loggers_set_to_warning(self):
        setup_logging()
        for name in ("httpx", "httpcore", "urllib3"):
            assert logging.getLogger(name).level == logging.WARNING

    def test_log_file_adds_file_handler(self, tmp_path):
        log_file = str(tmp_path / "test.log")
        setup_logging(log_file=log_file)
        root = logging.getLogger()
        assert len(root.handlers) == 2
        file_handlers = [
            h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(file_handlers) == 1
        assert file_handlers[0].maxBytes == 10 * 1024 * 1024
        assert file_handlers[0].backupCount == 5

    def test_repeated_calls_do_not_duplicate_handlers(self):
        setup_logging()
        setup_logging()
        setup_logging()
        root = logging.getLogger()
        assert len(root.handlers) == 1

    def test_level_is_applied(self):
        setup_logging(level="DEBUG")
        assert logging.getLogger().level == logging.DEBUG

        setup_logging(level="ERROR")
        assert logging.getLogger().level == logging.ERROR
