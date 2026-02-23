"""调度器测试

覆盖: 4 个 job 函数、CLI 命令、调度器配置
"""

from unittest.mock import patch, MagicMock

from click.testing import CliRunner

from cryptobot.cli.scheduler import (
    daemon,
    job_check_alerts,
    job_cleanup,
    job_re_review,
    job_urgent_review,
    job_workflow_run,
    _maybe_reload_config,
    _maybe_reschedule,
    _format_daily_report,
    _symbol_to_ft_pair,
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
        """工作流异常不应 raise，只记 log + 重试 + 通知"""
        with (
            patch(
                "cryptobot.workflow.graph.build_graph",
                side_effect=RuntimeError("boom"),
            ),
            patch("cryptobot.notify.send_message"),
        ):
            job_workflow_run()  # 不抛异常
        assert "最终失败" in caplog.text


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
        """异常时不 raise，重试后通知"""
        mock_ft.side_effect = Exception("连接失败")
        with patch("cryptobot.notify.send_message"):
            job_check_alerts()
        assert "最终失败" in caplog.text


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


# ─── 配置热更新 ───────────────────────────────────────────────────────────

class TestConfigReload:
    def test_mtime_unchanged_no_parse(self, tmp_path, monkeypatch):
        """mtime 未变时不解析 yaml"""
        import cryptobot.cli.scheduler as sched_mod

        config_file = tmp_path / "settings.yaml"
        config_file.write_text("schedule:\n  full_cycle_hours: 2\n")

        monkeypatch.setattr(sched_mod, "_CONFIG_PATH", str(config_file))
        monkeypatch.setattr(sched_mod, "_last_mtime", config_file.stat().st_mtime)
        monkeypatch.setattr(sched_mod, "_last_config", {"schedule": {"full_cycle_hours": 2}})

        mock_sched = MagicMock()
        _maybe_reload_config(mock_sched)

        # 不应 reschedule
        mock_sched.reschedule_job.assert_not_called()

    def test_interval_change_triggers_reschedule(self, tmp_path, monkeypatch):
        """interval 变更触发 reschedule_job"""
        import cryptobot.cli.scheduler as sched_mod

        config_file = tmp_path / "settings.yaml"
        config_file.write_text("schedule:\n  full_cycle_minutes: 15\n")

        monkeypatch.setattr(sched_mod, "_CONFIG_PATH", str(config_file))
        monkeypatch.setattr(sched_mod, "_last_mtime", 0.0)  # 强制触发
        monkeypatch.setattr(sched_mod, "_last_config", {"schedule": {"full_cycle_minutes": 30}})

        mock_sched = MagicMock()
        _maybe_reload_config(mock_sched)

        mock_sched.reschedule_job.assert_called_once_with(
            "workflow_run", trigger="interval", minutes=15,
        )

    def test_file_not_exist_no_error(self, tmp_path, monkeypatch):
        """文件不存在不报错"""
        import cryptobot.cli.scheduler as sched_mod

        monkeypatch.setattr(sched_mod, "_CONFIG_PATH", str(tmp_path / "nonexistent.yaml"))
        monkeypatch.setattr(sched_mod, "_last_mtime", 0.0)

        mock_sched = MagicMock()
        _maybe_reload_config(mock_sched)  # 不应抛异常
        mock_sched.reschedule_job.assert_not_called()

    def test_reschedule_multiple_keys(self):
        """多个配置项同时变更"""
        mock_sched = MagicMock()
        old = {"schedule": {"full_cycle_minutes": 30, "monitor_interval_minutes": 5}}
        new = {"schedule": {"full_cycle_minutes": 15, "monitor_interval_minutes": 10}}

        _maybe_reschedule(mock_sched, new, old)

        assert mock_sched.reschedule_job.call_count == 2


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
        # 验证 12 个 job 被添加 (含 ml_retrain + config_reload + daily_report + prompt_optimization + urgent_review + strategy_advisor + overfit_check)
        assert mock_sched.add_job.call_count == 12
        job_ids = {
            call.kwargs.get("id") or call[2].get("id")
            for call in mock_sched.add_job.call_args_list
        }
        assert "workflow_run" in job_ids
        assert "check_alerts" in job_ids
        assert "re_review" in job_ids
        assert "cleanup" in job_ids
        assert "journal_sync" in job_ids
        assert "daily_report" in job_ids

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
        # M18: --run-now 现在通过 scheduler.add_job 调度，而非直接调用
        mock_sched.add_job.assert_any_call(mock_workflow, "date", id="run_now")
        mock_alerts.assert_called_once()


# ─── 每日绩效日报 ────────────────────────────────────────────────────────

class TestDailyReport:
    def test_format_daily_report_with_trades(self):
        """有交易数据时应包含统计信息"""
        today = {
            "closed": 3,
            "win_rate": 0.667,
            "avg_pnl_pct": 0.77,
            "total_pnl_usdt": 230,
        }
        weekly = {
            "closed": 10,
            "win_rate": 0.65,
            "profit_factor": 1.9,
            "total_pnl_usdt": 1200,
        }
        positions = [
            {"pair": "BTC/USDT:USDT", "is_short": False, "leverage": 3, "profit_ratio": 0.015},
            {"pair": "ETH/USDT:USDT", "is_short": True, "leverage": 2, "profit_ratio": -0.003},
        ]
        accuracy = {
            "technical": {"total": 20, "correct": 14, "accuracy": 0.72},
            "onchain": {"total": 15, "correct": 10, "accuracy": 0.65},
            "sentiment": {"total": 10, "correct": 5, "accuracy": 0.48},
            "fundamental": {"total": 12, "correct": 7, "accuracy": 0.60},
        }

        text = _format_daily_report(today, weekly, positions, accuracy)

        assert "日报" in text
        assert "3 笔" in text
        assert "BTCUSDT LONG 3x" in text
        assert "ETHUSDT SHORT 2x" in text
        assert "technical 72%" in text
        assert "持仓 2 个" in text

    def test_format_daily_report_empty(self):
        """无交易数据时输出简洁格式"""
        today = {"closed": 0, "win_rate": 0, "avg_pnl_pct": 0, "total_pnl_usdt": 0}
        weekly = {"closed": 0, "win_rate": 0, "profit_factor": 0, "total_pnl_usdt": 0}

        text = _format_daily_report(today, weekly, [], {})

        assert "日报" in text
        assert "今日无交易记录" in text
        assert "持仓: 0 个" in text


# ─── H11: 交易对拼接 ────────────────────────────────────────────────────────

class TestSymbolToFtPair:
    def test_btc(self):
        assert _symbol_to_ft_pair("BTCUSDT") == "BTC/USDT:USDT"

    def test_doge(self):
        assert _symbol_to_ft_pair("DOGEUSDT") == "DOGE/USDT:USDT"

    def test_avax(self):
        assert _symbol_to_ft_pair("AVAXUSDT") == "AVAX/USDT:USDT"

    def test_link(self):
        assert _symbol_to_ft_pair("LINKUSDT") == "LINK/USDT:USDT"

    def test_sui(self):
        assert _symbol_to_ft_pair("SUIUSDT") == "SUI/USDT:USDT"

    def test_eth(self):
        assert _symbol_to_ft_pair("ETHUSDT") == "ETH/USDT:USDT"


# ─── job_urgent_review ────────────────────────────────────────────────────

class TestJobUrgentReview:
    @patch("cryptobot.freqtrade_api.ft_api_get", return_value=[])
    def test_no_positions(self, mock_ft, caplog):
        """无持仓时不触发复审"""
        import logging
        with caplog.at_level(logging.INFO):
            job_urgent_review()
        assert "紧急复审触发" not in caplog.text

    @patch("cryptobot.freqtrade_api.ft_api_get", return_value=None)
    def test_none_positions(self, mock_ft, caplog):
        """API 返回 None 不报错"""
        job_urgent_review()
        assert "紧急复审触发" not in caplog.text

    @patch("cryptobot.freqtrade_api.ft_api_get")
    def test_triggers_on_large_loss(self, mock_ft, caplog):
        """亏损>3% 时触发紧急复审"""
        import logging
        mock_ft.return_value = [
            {"pair": "BTC/USDT:USDT", "profit_ratio": -0.05},  # -5%
        ]
        with (
            caplog.at_level(logging.INFO),
            patch("cryptobot.workflow.graph.collect_data_for_symbols", return_value={}),
            patch("cryptobot.workflow.graph.re_review", return_value=[]),
        ):
            job_urgent_review()
        assert "紧急复审触发" in caplog.text
        assert "BTCUSDT" in caplog.text

    @patch("cryptobot.freqtrade_api.ft_api_get")
    def test_triggers_on_large_profit(self, mock_ft, caplog):
        """盈利>10% 时触发紧急复审"""
        import logging
        mock_ft.return_value = [
            {"pair": "ETH/USDT:USDT", "profit_ratio": 0.12},  # +12%
        ]
        with (
            caplog.at_level(logging.INFO),
            patch("cryptobot.workflow.graph.collect_data_for_symbols", return_value={}),
            patch("cryptobot.workflow.graph.re_review", return_value=[]),
        ):
            job_urgent_review()
        assert "紧急复审触发" in caplog.text

    @patch("cryptobot.freqtrade_api.ft_api_get")
    def test_skips_normal_pnl(self, mock_ft, caplog):
        """P&L 在正常范围内不触发"""
        import logging
        mock_ft.return_value = [
            {"pair": "BTC/USDT:USDT", "profit_ratio": 0.02},  # +2%
        ]
        with caplog.at_level(logging.INFO):
            job_urgent_review()
        assert "紧急复审触发" not in caplog.text

    @patch("cryptobot.freqtrade_api.ft_api_get")
    def test_handles_review_exception(self, mock_ft, caplog):
        """复审异常不中断"""
        mock_ft.return_value = [
            {"pair": "BTC/USDT:USDT", "profit_ratio": -0.05},
        ]
        with patch(
            "cryptobot.workflow.graph.collect_data_for_symbols",
            side_effect=RuntimeError("boom"),
        ):
            job_urgent_review()
        assert "紧急复审失败" in caplog.text
