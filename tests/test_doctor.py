"""doctor 健康检查测试"""

import json
from unittest.mock import patch

from click.testing import CliRunner

from cryptobot.cli.doctor import doctor, run_checks, _CHECKER_MAP


class TestRunChecks:
    def test_all_pass_when_env_set(self, monkeypatch):
        """环境变量齐全时应全部通过"""
        monkeypatch.setenv("BINANCE_API_KEY", "test_key")
        monkeypatch.setenv("BINANCE_API_SECRET", "test_secret")
        monkeypatch.setenv("COINGLASS_API_KEY", "test_cg")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "test_chat")
        monkeypatch.setitem(_CHECKER_MAP, "talib_import", lambda: ("OK", "TA-Lib 已安装"))
        monkeypatch.setitem(_CHECKER_MAP, "binance_ping", lambda: ("OK", "连通"))
        monkeypatch.setitem(_CHECKER_MAP, "freqtrade_ping", lambda: ("OK", "连通"))

        results = run_checks()
        statuses = {r["name"]: r["status"] for r in results}

        assert statuses["Python 3.12"] == "OK"
        assert statuses["TA-Lib C 库"] == "OK"
        assert statuses["BINANCE_API_KEY"] == "OK"
        assert statuses["BINANCE_API_SECRET"] == "OK"

    def test_missing_env_vars(self, monkeypatch):
        """缺少环境变量应报 FAIL"""
        monkeypatch.delenv("BINANCE_API_KEY", raising=False)
        monkeypatch.delenv("BINANCE_API_SECRET", raising=False)

        results = run_checks()
        by_name = {r["name"]: r for r in results}

        assert by_name["BINANCE_API_KEY"]["status"] == "FAIL"
        assert by_name["BINANCE_API_SECRET"]["status"] == "FAIL"

    def test_talib_fail(self, monkeypatch):
        """TA-Lib 未安装应报 FAIL"""
        monkeypatch.setitem(_CHECKER_MAP, "talib_import", lambda: ("FAIL", "TA-Lib 未安装"))
        results = run_checks()
        by_name = {r["name"]: r for r in results}
        assert by_name["TA-Lib C 库"]["status"] == "FAIL"

    def test_claude_cli_missing(self, monkeypatch):
        """Claude CLI 不存在应报 WARN"""
        monkeypatch.setattr("shutil.which", lambda x: None)
        from cryptobot.cli.doctor import _check_claude_cli
        status, _ = _check_claude_cli()
        assert status == "WARN"

    def test_telegram_partial(self, monkeypatch):
        """Telegram 只配一半应报 WARN"""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

        results = run_checks()
        by_name = {r["name"]: r for r in results}
        assert by_name["TELEGRAM 配置"]["status"] == "WARN"


class TestDoctorCLI:
    @patch("cryptobot.cli.doctor.run_checks")
    def test_exit_code_1_on_fail(self, mock_checks):
        """有 FAIL 时退出码应为 1"""
        mock_checks.return_value = [
            {"name": "test", "status": "FAIL", "detail": "bad"},
        ]
        runner = CliRunner()
        result = runner.invoke(doctor, catch_exceptions=False)
        assert result.exit_code == 1

    @patch("cryptobot.cli.doctor.run_checks")
    def test_exit_code_0_on_warn_only(self, mock_checks):
        """仅 WARN 时退出码应为 0"""
        mock_checks.return_value = [
            {"name": "test", "status": "WARN", "detail": "meh"},
        ]
        runner = CliRunner()
        result = runner.invoke(doctor, catch_exceptions=False)
        assert result.exit_code == 0

    @patch("cryptobot.cli.doctor.run_checks")
    def test_exit_code_0_all_ok(self, mock_checks):
        """全部 OK 时退出码应为 0"""
        mock_checks.return_value = [
            {"name": "test", "status": "OK", "detail": "good"},
        ]
        runner = CliRunner()
        result = runner.invoke(doctor, catch_exceptions=False)
        assert result.exit_code == 0

    @patch("cryptobot.cli.doctor.run_checks")
    def test_json_output(self, mock_checks):
        """--json-output 应返回合法 JSON"""
        mock_checks.return_value = [
            {"name": "Python 3.12", "status": "OK", "detail": "3.12.8"},
            {"name": "TA-Lib", "status": "FAIL", "detail": "未安装"},
        ]
        runner = CliRunner()
        result = runner.invoke(doctor, ["--json-output"])
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["name"] == "Python 3.12"
