"""init 环境初始化测试"""

import importlib
from unittest.mock import patch

from click.testing import CliRunner

from cryptobot.cli.init_cmd import init_cmd

# 获取模块对象（避免和 Click Command 同名冲突）
_init_mod = importlib.import_module("cryptobot.cli.init_cmd")


class TestInitCmd:
    def _patch_paths(self, monkeypatch, tmp_path, dirs=None):
        """统一 monkeypatch 路径到 tmp_path"""
        monkeypatch.setattr(_init_mod, "DATA_OUTPUT_DIR", tmp_path / "data" / "output")
        monkeypatch.setattr(_init_mod, "PROJECT_ROOT", tmp_path)
        if dirs is not None:
            monkeypatch.setattr(_init_mod, "DIRS_TO_CREATE", dirs)
        else:
            monkeypatch.setattr(_init_mod, "DIRS_TO_CREATE", [
                tmp_path / "data" / "output" / "signals",
                tmp_path / "data" / "output" / "journal",
                tmp_path / "data" / "output" / ".cache",
                tmp_path / "logs",
            ])
        monkeypatch.setattr(_init_mod, "ENV_FILE", tmp_path / ".env")
        monkeypatch.setattr(_init_mod, "ENV_EXAMPLE", tmp_path / ".env.example")

    @patch("cryptobot.cli.doctor.run_checks", return_value=[])
    @patch("cryptobot.cli.doctor.print_results")
    def test_creates_directories(self, mock_print, mock_checks, tmp_path, monkeypatch):
        """应创建必要的目录"""
        self._patch_paths(monkeypatch, tmp_path)
        (tmp_path / ".env.example").write_text("# example")

        runner = CliRunner()
        result = runner.invoke(init_cmd, input="\n\nN\n")
        assert result.exit_code == 0
        assert (tmp_path / "data" / "output" / "signals").is_dir()
        assert (tmp_path / "data" / "output" / "journal").is_dir()
        assert (tmp_path / "logs").is_dir()

    @patch("cryptobot.cli.doctor.run_checks", return_value=[])
    @patch("cryptobot.cli.doctor.print_results")
    def test_idempotent(self, mock_print, mock_checks, tmp_path, monkeypatch):
        """重复运行不报错（幂等性）"""
        self._patch_paths(monkeypatch, tmp_path, dirs=[
            tmp_path / "data" / "output" / "signals", tmp_path / "logs",
        ])
        (tmp_path / ".env.example").write_text("# example")

        runner = CliRunner()
        runner.invoke(init_cmd, input="\n\nN\n")
        result = runner.invoke(init_cmd, input="\n\nN\n")
        assert result.exit_code == 0

    @patch("cryptobot.cli.doctor.run_checks", return_value=[])
    @patch("cryptobot.cli.doctor.print_results")
    def test_env_not_overwritten(self, mock_print, mock_checks, tmp_path, monkeypatch):
        """.env 已存在时不覆盖"""
        self._patch_paths(monkeypatch, tmp_path, dirs=[])
        (tmp_path / ".env").write_text("EXISTING=yes")
        (tmp_path / ".env.example").write_text("# template")

        runner = CliRunner()
        result = runner.invoke(init_cmd, input="\n\nN\n")
        assert result.exit_code == 0
        assert "EXISTING=yes" in (tmp_path / ".env").read_text()

    @patch("cryptobot.cli.doctor.run_checks", return_value=[])
    @patch("cryptobot.cli.doctor.print_results")
    def test_interactive_api_keys(self, mock_print, mock_checks, tmp_path, monkeypatch):
        """交互输入 API key 应追加到 .env"""
        self._patch_paths(monkeypatch, tmp_path, dirs=[])
        (tmp_path / ".env.example").write_text("# template")

        runner = CliRunner()
        result = runner.invoke(init_cmd, input="my_key\nmy_secret\nN\n")
        assert result.exit_code == 0

        env_content = (tmp_path / ".env").read_text()
        assert "BINANCE_API_KEY=my_key" in env_content
        assert "BINANCE_API_SECRET=my_secret" in env_content
