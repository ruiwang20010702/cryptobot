"""调度器测试

覆盖: 4 个 job 函数、CLI 命令、调度器配置
"""

from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from cryptobot.cli.scheduler import (
    daemon,
    job_check_alerts,
    job_cleanup,
    job_re_review,
    job_workflow_run,
)


# ─── job_workflow_run ──────────────────────────────────────────────────────

class TestJobWorkflowRun:
    @patch("cryptobot.cli.scheduler.build_graph", create=True)
    def test_invokes_graph(self, mock_build):
        """应调用 build_graph().invoke({})"""
        mock_app = MagicMock()
        mock_app.invoke.return_value = {
            "executed": [{"symbol": "BTCUSDT"}],
            "errors": [],
        }
        # job_workflow_run 内部 lazy import，需要 patch 目标模块
        with patch("cryptobot.workflow.graph.build_graph", return_value=mock_app):
            job_workflow_run()
        mock_app.invoke.assert_called_once_with({})

    def test_handles_exception(self, caplog):
        """工作流异常不应 raise，只记 log"""
        with patch(
            "cryptobot.workflow.graph.build_graph",
            side_effect=RuntimeError("boom"),
        ):
            job_workflow_run()  # 不抛异常
        assert "工作流失败" in caplog.text


# ─── job_check_alerts ──────────────────────────────────────────────────────

class TestJobCheckAlerts:
    @patch("cryptobot.freqtrade_api.ft_api_get")
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    def test_no_alerts_when_no_data(self, mock_signals, mock_ft, caplog):
        """无持仓无信号时不报告警"""
        mock_ft.return_value = None
        import logging
        with caplog.at_level(logging.DEBUG):
            job_check_alerts()
        # 不应有 WARNING 级别日志
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 0

    @patch("cryptobot.freqtrade_api.ft_api_get")
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    def test_handles_exception(self, mock_signals, mock_ft, caplog):
        """异常时不 raise"""
        mock_ft.side_effect = Exception("连接失败")
        job_check_alerts()
        assert "告警检查失败" in caplog.text


# ─── job_re_review ─────────────────────────────────────────────────────────

class TestJobReReview:
    @patch("cryptobot.freqtrade_api.ft_api_get", return_value=None)
    def test_skip_when_no_positions(self, mock_ft, caplog):
        """无持仓时跳过复审"""
        import logging
        with caplog.at_level(logging.DEBUG):
            job_re_review()
        assert "跳过复审" in caplog.text

    @patch("cryptobot.freqtrade_api.ft_api_get")
    def test_handles_exception(self, mock_ft, caplog):
        """异常不 raise"""
        mock_ft.side_effect = Exception("boom")
        job_re_review()
        assert "复审失败" in caplog.text


# ─── job_cleanup ───────────────────────────────────────────────────────────

class TestJobCleanup:
    @patch("cryptobot.signal.bridge.cleanup_expired", return_value=3)
    def test_logs_removed_count(self, mock_cleanup, caplog):
        """清理成功时记录数量"""
        import logging
        with caplog.at_level(logging.INFO):
            job_cleanup()
        assert "3 个" in caplog.text

    @patch("cryptobot.signal.bridge.cleanup_expired", return_value=0)
    def test_no_log_when_zero(self, mock_cleanup, caplog):
        """无过期信号时不记录"""
        import logging
        with caplog.at_level(logging.INFO):
            job_cleanup()
        assert "清理过期信号" not in caplog.text


# ─── CLI 命令 ──────────────────────────────────────────────────────────────

class TestDaemonCLI:
    def test_daemon_help(self):
        runner = CliRunner()
        result = runner.invoke(daemon, ["--help"])
        assert result.exit_code == 0
        assert "后台调度服务" in result.output

    def test_start_help(self):
        runner = CliRunner()
        result = runner.invoke(daemon, ["start", "--help"])
        assert result.exit_code == 0
        assert "--run-now" in result.output
        assert "--verbose" in result.output

    @patch("cryptobot.cli.scheduler.job_check_alerts")
    def test_start_creates_scheduler(self, mock_alerts):
        """start 命令应创建并启动调度器"""
        runner = CliRunner()

        # Mock BlockingScheduler 使其不阻塞
        with patch("apscheduler.schedulers.blocking.BlockingScheduler") as MockScheduler:
            mock_sched = MagicMock()
            MockScheduler.return_value = mock_sched
            # start() 会阻塞，让它立即退出
            mock_sched.start.side_effect = KeyboardInterrupt()

            result = runner.invoke(daemon, ["start"])

        assert result.exit_code == 0
        assert "调度器启动" in result.output
        # 验证 5 个 job 被添加
        assert mock_sched.add_job.call_count == 5
        job_ids = {
            call.kwargs.get("id") or call[2].get("id")
            for call in mock_sched.add_job.call_args_list
        }
        assert "workflow_run" in job_ids
        assert "check_alerts" in job_ids
        assert "re_review" in job_ids
        assert "cleanup" in job_ids
        assert "journal_sync" in job_ids

    @patch("cryptobot.cli.scheduler.job_check_alerts")
    @patch("cryptobot.cli.scheduler.job_workflow_run")
    def test_run_now_flag(self, mock_workflow, mock_alerts):
        """--run-now 应立即执行一次完整分析"""
        runner = CliRunner()

        with patch("apscheduler.schedulers.blocking.BlockingScheduler") as MockScheduler:
            mock_sched = MagicMock()
            MockScheduler.return_value = mock_sched
            mock_sched.start.side_effect = KeyboardInterrupt()

            result = runner.invoke(daemon, ["start", "--run-now"])

        assert result.exit_code == 0
        mock_workflow.assert_called_once()
        mock_alerts.assert_called_once()
