"""工作流测试

覆盖: llm wrapper、screen 节点、条件路由、CLI 命令
"""

import json
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from cryptobot.workflow.llm import call_claude, call_claude_parallel
from cryptobot.workflow.graph import (
    screen,
    should_risk_review,
    should_execute,
    _decision_to_signal,
    _data_quality_score,
    _detect_market_regime,
    WorkflowState,
)
from cryptobot.cli.workflow import workflow


# ─── llm wrapper ─────────────────────────────────────────────────────────

class TestCallClaude:
    def setup_method(self):
        """固定 provider 为 claude，避免路由到 API 后端"""
        from cryptobot.workflow import llm as llm_mod
        self._original_cache = llm_mod._provider_cache
        llm_mod._provider_cache = "claude"

    def teardown_method(self):
        from cryptobot.workflow import llm as llm_mod
        llm_mod._provider_cache = self._original_cache

    @patch("cryptobot.workflow.llm.subprocess.run")
    def test_returns_parsed_json(self, mock_run):
        """claude 返回 JSON 时应解析为 dict"""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "type": "result",
                "result": json.dumps({"direction": "bullish", "confidence": 75}),
            }),
            stderr="",
        )
        result = call_claude("test prompt")
        assert isinstance(result, dict)
        assert result["direction"] == "bullish"
        assert result["confidence"] == 75

    @patch("cryptobot.workflow.llm.subprocess.run")
    def test_returns_raw_text_on_non_json(self, mock_run):
        """claude 返回非 JSON 时应返回原始文本"""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"type": "result", "result": "这是纯文本回复"}),
            stderr="",
        )
        result = call_claude("test prompt")
        assert isinstance(result, str)
        assert "纯文本" in result

    @patch("cryptobot.workflow.llm.subprocess.run")
    def test_raises_on_nonzero_exit(self, mock_run):
        """非零退出码应抛出 RuntimeError"""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="error message",
        )
        with pytest.raises(RuntimeError, match="调用失败"):
            call_claude("test")

    @patch("cryptobot.workflow.llm.subprocess.run")
    def test_passes_correct_cli_args(self, mock_run):
        """验证 CLI 参数正确传递"""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"type":"result","result":"ok"}',
            stderr="",
        )
        call_claude(
            "test",
            model="sonnet",
            system_prompt="你是助手",
            max_budget=0.10,
        )
        cmd = mock_run.call_args[0][0]
        assert "--model" in cmd
        assert "sonnet" in cmd
        assert "--system-prompt" in cmd
        assert "--max-budget-usd" in cmd
        assert "0.1" in cmd

    @patch("cryptobot.workflow.llm.subprocess.run")
    def test_json_schema_passed(self, mock_run):
        """json_schema 应作为 --json-schema 传递"""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"type":"result","result":"{}"}',
            stderr="",
        )
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        call_claude("test", json_schema=schema)
        cmd = mock_run.call_args[0][0]
        assert "--json-schema" in cmd


class TestCallClaudeParallel:
    @patch("cryptobot.workflow.llm.call_claude")
    def test_parallel_preserves_order(self, mock_call):
        """并行调用应保持结果顺序"""
        mock_call.side_effect = lambda prompt, **kw: {"idx": prompt}

        tasks = [
            {"prompt": "task_0"},
            {"prompt": "task_1"},
            {"prompt": "task_2"},
        ]
        results = call_claude_parallel(tasks, max_workers=3)

        assert len(results) == 3
        assert results[0]["idx"] == "task_0"
        assert results[1]["idx"] == "task_1"
        assert results[2]["idx"] == "task_2"

    @patch("cryptobot.workflow.llm.call_claude")
    def test_parallel_handles_errors(self, mock_call):
        """某个任务失败不应影响其他任务"""
        def side_effect(prompt, **kw):
            if prompt == "fail":
                raise RuntimeError("boom")
            return {"ok": True}

        mock_call.side_effect = side_effect

        tasks = [
            {"prompt": "ok1"},
            {"prompt": "fail"},
            {"prompt": "ok2"},
        ]
        results = call_claude_parallel(tasks, max_workers=3)

        assert results[0] == {"ok": True}
        assert "error" in results[1]
        assert results[2] == {"ok": True}


# ─── screen 节点 ──────────────────────────────────────────────────────────

class TestScreenNode:
    def _make_market_data(self, symbols_scores: dict) -> dict:
        """构造 market_data，symbols_scores: {symbol: (rsi, tech_score, macd_cross)}"""
        market_data = {}
        for symbol, (rsi, tech_score, macd_cross) in symbols_scores.items():
            market_data[symbol] = {
                "symbol": symbol,
                "tech": {
                    "momentum": {"rsi_14": rsi},
                    "trend": {"macd_cross": macd_cross},
                    "volatility": {"atr_pct": 2.0},
                    "signals": {"technical_score": tech_score},
                },
                "crypto": {
                    "composite": {"score": 0},
                },
            }
        return market_data

    @patch("cryptobot.config.get_pair_config", return_value={"category": "layer1"})
    def test_selects_top_5(self, mock_cfg):
        """应选出得分最高的 5 个币种"""
        market_data = self._make_market_data({
            "BTCUSDT": (25, 5, "golden_cross"),   # 高分: RSI极端+高技术分+金叉
            "ETHUSDT": (75, 4, "death_cross"),     # 高分
            "SOLUSDT": (50, 1, "none"),            # 低分
            "XRPUSDT": (50, 0.5, "none"),          # 低分
            "BNBUSDT": (35, 3, "golden_cross"),    # 中分
            "ADAUSDT": (50, 0.5, "none"),          # 低分
            "DOGEUSDT": (50, 0.3, "none"),         # 最低分
        })

        state: WorkflowState = {"market_data": market_data}
        result = screen(state)
        screened = result["screened_symbols"]

        assert len(screened) <= 5
        assert "BTCUSDT" in screened
        assert "ETHUSDT" in screened

    @patch("cryptobot.config.get_pair_config", return_value=None)
    def test_skips_missing_tech(self, mock_cfg):
        """缺少技术数据的币种应被跳过"""
        market_data = {
            "BTCUSDT": {"symbol": "BTCUSDT", "tech": None},
            "ETHUSDT": {
                "symbol": "ETHUSDT",
                "tech": {
                    "momentum": {"rsi_14": 50},
                    "trend": {"macd_cross": "none"},
                    "volatility": {"atr_pct": 2.0},
                    "signals": {"technical_score": 3},
                },
                "crypto": {"composite": {"score": 0}},
            },
        }
        state: WorkflowState = {"market_data": market_data}
        result = screen(state)
        assert "BTCUSDT" not in result["screened_symbols"]
        assert "ETHUSDT" in result["screened_symbols"]


# ─── 条件路由 ─────────────────────────────────────────────────────────────

class TestConditionalEdges:
    def test_should_risk_review_with_decisions(self):
        state: WorkflowState = {
            "decisions": [{"action": "long", "symbol": "BTCUSDT"}]
        }
        assert should_risk_review(state) == "risk_review"

    def test_should_risk_review_no_trade(self):
        state: WorkflowState = {
            "decisions": [{"action": "no_trade", "symbol": "BTCUSDT"}]
        }
        assert should_risk_review(state) == "__end__"

    def test_should_risk_review_empty(self):
        state: WorkflowState = {"decisions": []}
        assert should_risk_review(state) == "__end__"

    def test_should_execute_with_approved(self):
        state: WorkflowState = {
            "approved_signals": [{"symbol": "BTCUSDT", "action": "long"}]
        }
        assert should_execute(state) == "execute"

    def test_should_execute_empty(self):
        state: WorkflowState = {"approved_signals": []}
        assert should_execute(state) == "__end__"


# ─── CLI 命令 ─────────────────────────────────────────────────────────────

class TestCLI:
    def test_workflow_group_exists(self):
        runner = CliRunner()
        result = runner.invoke(workflow, ["--help"])
        assert result.exit_code == 0
        assert "自动化分析工作流" in result.output

    def test_run_help(self):
        runner = CliRunner()
        result = runner.invoke(workflow, ["run", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output
        assert "--json-output" in result.output

    @patch("cryptobot.workflow.graph.collect_data")
    @patch("cryptobot.workflow.graph.screen")
    def test_dry_run_json(self, mock_screen, mock_collect):
        """dry-run --json-output 应输出 JSON"""
        mock_collect.return_value = {
            "market_data": {"BTCUSDT": {}},
            "market_overview": {},
            "fear_greed": {"current_value": 50},
            "errors": [],
        }
        mock_screen.return_value = {"screened_symbols": ["BTCUSDT", "ETHUSDT"]}

        runner = CliRunner()
        result = runner.invoke(workflow, ["run", "--dry-run", "--json-output"])
        assert result.exit_code == 0

        output = json.loads(result.output)
        assert output["mode"] == "dry_run"
        assert "BTCUSDT" in output["screened_symbols"]


# ─── _build_portfolio_context ─────────────────────────────────────────────

class TestBuildPortfolioContext:
    """测试持仓上下文构建"""

    @patch("cryptobot.freqtrade_api.ft_api_get")
    def test_returns_empty_when_ft_offline(self, mock_ft):
        """Freqtrade 未运行时返回空字符串"""
        from cryptobot.workflow.graph import _build_portfolio_context
        mock_ft.return_value = None
        ctx = _build_portfolio_context()
        assert ctx == ""

    @patch("cryptobot.freqtrade_api.ft_api_get")
    def test_includes_balance_and_positions(self, mock_ft):
        """有持仓时返回包含余额和持仓信息的字符串"""
        from cryptobot.workflow.graph import _build_portfolio_context

        def _ft_get(endpoint):
            if endpoint == "/status":
                return [
                    {"pair": "BTC/USDT:USDT", "is_short": False, "leverage": 3,
                     "profit_pct": 0.05, "stake_amount": 1000},
                ]
            if endpoint == "/balance":
                return {"currencies": [{"currency": "USDT", "balance": 10000,
                                        "free": 9000, "used": 1000}]}
            return None

        mock_ft.side_effect = _ft_get
        ctx = _build_portfolio_context()

        assert "USDT 余额: 10000" in ctx
        assert "BTC/USDT:USDT" in ctx
        assert "LONG" in ctx
        assert "同方向总仓位上限" in ctx
        assert "风控规则" in ctx

    @patch("cryptobot.freqtrade_api.ft_api_get")
    def test_calculates_direction_ratios(self, mock_ft):
        """正确计算多空仓位占比"""
        from cryptobot.workflow.graph import _build_portfolio_context

        def _ft_get(endpoint):
            if endpoint == "/status":
                return [
                    {"pair": "BTC/USDT:USDT", "is_short": False, "leverage": 3,
                     "profit_pct": 0.05, "stake_amount": 2000},
                    {"pair": "ETH/USDT:USDT", "is_short": True, "leverage": 2,
                     "profit_pct": -0.03, "stake_amount": 1000},
                ]
            if endpoint == "/balance":
                return {"currencies": [{"currency": "USDT", "balance": 10000,
                                        "free": 7000, "used": 3000}]}
            return None

        mock_ft.side_effect = _ft_get
        ctx = _build_portfolio_context()

        # 多头 2000/10000 = 20%, 空头 1000/10000 = 10%
        assert "多头仓位占比: 20.0%" in ctx
        assert "空头仓位占比: 10.0%" in ctx


# ─── _decision_to_signal 仓位计算 ────────────────────────────────────────

class TestDecisionToSignal:
    """测试交易决策 → 信号转换（含仓位计算）"""

    def _make_decision(self, **overrides):
        base = {
            "symbol": "BTCUSDT",
            "action": "long",
            "leverage": 3,
            "entry_price_range": [94000, 96000],
            "stop_loss": 91000,
            "take_profit": [{"price": 100000, "close_pct": 50}, {"price": 105000, "close_pct": 100}],
            "confidence": 75,
            "position_size_pct": 10,
            "reasoning": "技术面看多",
        }
        base.update(overrides)
        return base

    def _make_risk_result(self, **overrides):
        base = {"risk_score": 35, "warnings": []}
        base.update(overrides)
        return base

    def test_position_size_calculated(self):
        """有余额+入场价+止损价时，应调用 position_sizer 计算仓位"""
        decision = self._make_decision()
        risk_result = self._make_risk_result()
        signal = _decision_to_signal(decision, risk_result, account_balance=10000)

        # 不应该是硬编码 1000
        assert signal["position_size_usdt"] is not None
        assert signal["position_size_usdt"] != 1000
        # 应该 > 0 且合理 (10000 余额, 3x 杠杆, ~3.3% 止损距离)
        assert 0 < signal["position_size_usdt"] <= 2500  # max_single_position_pct=25%

    def test_fallback_to_pct_when_no_stop_loss(self):
        """无止损价时，fallback 用 AI 建议的 pct × 余额"""
        decision = self._make_decision(stop_loss=None, position_size_pct=10)
        signal = _decision_to_signal(decision, self._make_risk_result(), account_balance=10000)

        # 10% × 10000 = 1000
        assert signal["position_size_usdt"] == 1000

    def test_fallback_to_pct_when_no_balance(self):
        """无法获取余额(=0)时，返回 0（由硬性规则拦截开仓）"""
        decision = self._make_decision(position_size_pct=10)
        signal = _decision_to_signal(decision, self._make_risk_result(), account_balance=0)

        assert signal["position_size_usdt"] == 0

    def test_fallback_to_pct_when_no_entry_range(self):
        """无入场区间时，fallback 用百分比"""
        decision = self._make_decision(entry_price_range=None, position_size_pct=15)
        signal = _decision_to_signal(decision, self._make_risk_result(), account_balance=10000)

        # 15% × 10000 = 1500
        assert signal["position_size_usdt"] == 1500

    def test_signal_fields_complete(self):
        """验证信号包含所有必要字段"""
        decision = self._make_decision()
        signal = _decision_to_signal(decision, self._make_risk_result(), account_balance=10000)

        assert signal["symbol"] == "BTCUSDT"
        assert signal["action"] == "long"
        assert signal["leverage"] == 3
        assert signal["entry_price_range"] == [94000, 96000]
        assert signal["stop_loss"] == 91000
        assert len(signal["take_profit"]) == 2
        assert signal["confidence"] == 75
        assert signal["position_size_usdt"] > 0
        assert "reasoning" in signal["analysis_summary"]
        assert "risk_score" in signal["analysis_summary"]
        assert "timestamp" in signal

    def test_large_balance_capped(self):
        """大余额时仓位不超过 max_single_position_pct 上限"""
        decision = self._make_decision()
        signal = _decision_to_signal(decision, self._make_risk_result(), account_balance=100000)

        # max_single_position_pct=25% of 100000 = 25000
        assert signal["position_size_usdt"] <= 25000


# ─── 数据质量评分 ────────────────────────────────────────────────────────

class TestDataQualityScore:
    def test_full_data_100(self):
        """所有数据完整 → 100"""
        data = {
            "tech": {"rsi": 50}, "crypto": {"funding_rate": 0.01},
            "multi_tf": {"aligned_direction": "bullish"},
            "volume_analysis": {"vwap": 95000},
            "support_resistance": {"pivot": 95000},
            "liquidation": {"intensity": "low"},
            "btc_correlation": {"corr": 0.8},
        }
        assert _data_quality_score(data) == 100

    def test_only_tech_30(self):
        """仅 tech → 30"""
        data = {"tech": {"rsi": 50}}
        assert _data_quality_score(data) == 30

    def test_empty_0(self):
        """空数据 → 0"""
        assert _data_quality_score({}) == 0

    def test_error_dict_not_counted(self):
        """含 error 的 dict 不计分"""
        data = {"tech": {"rsi": 50}, "crypto": {"error": "timeout"}}
        assert _data_quality_score(data) == 30

    def test_none_not_counted(self):
        """None 值不计分"""
        data = {"tech": {"rsi": 50}, "crypto": None}
        assert _data_quality_score(data) == 30


class TestScreenQualityFilter:
    @patch("cryptobot.config.get_pair_config", return_value=None)
    def test_low_quality_filtered(self, mock_cfg):
        """数据质量 < 40 的币种应被过滤"""
        market_data = {
            # 仅 tech=30 → 质量 30 < 40，应被过滤
            "LOWUSDT": {
                "symbol": "LOWUSDT",
                "tech": {
                    "momentum": {"rsi_14": 25},
                    "trend": {"macd_cross": "golden_cross"},
                    "volatility": {"atr_pct": 5.0},
                    "signals": {"technical_score": 8},
                },
            },
            # tech(30) + crypto(15) = 45 ≥ 40，应保留
            "GOODUSDT": {
                "symbol": "GOODUSDT",
                "tech": {
                    "momentum": {"rsi_14": 50},
                    "trend": {"macd_cross": "none"},
                    "volatility": {"atr_pct": 2.0},
                    "signals": {"technical_score": 3},
                },
                "crypto": {"composite": {"score": 2}},
            },
        }
        state: WorkflowState = {"market_data": market_data}
        result = screen(state)
        assert "LOWUSDT" not in result["screened_symbols"]
        assert "GOODUSDT" in result["screened_symbols"]


# ─── 市场状态检测 ────────────────────────────────────────────────────────

def _passthrough_smoother(regime, confirm_cycles=2, *, is_volatile_upgrade=False, is_simulation=False):
    """测试用: smoother 直接透传, 不做平滑"""
    return regime, False


@patch("cryptobot.regime_smoother.smooth_regime_transition", side_effect=_passthrough_smoother)
class TestDetectMarketRegime:
    """测试 _detect_market_regime (多 TF regime 检测 + 恐惧贪婪升级)"""

    def _mock_regime(self, regime="trending", direction="bullish", strength="strong"):
        return {
            "regime": regime,
            "trend_direction": direction,
            "trend_strength": strength,
            "volatility_state": "normal",
            "timeframe_details": {
                "1h": {"trend": direction, "strength": strength, "adx": 30.0},
                "4h": {"trend": direction, "strength": strength, "adx": 28.0},
                "1d": {"trend": direction, "strength": strength, "adx": 32.0},
            },
            "description": f"{regime}市",
        }

    @patch("cryptobot.indicators.regime.detect_regime")
    def test_trending(self, mock_detect, _mock_smoother):
        """多 TF 一致看多 + 强 ADX → trending"""
        mock_detect.return_value = self._mock_regime("trending", "bullish", "strong")
        result = _detect_market_regime({}, {"current_value": 55})
        assert result["regime"] == "trending"
        assert result["params"]["max_leverage"] == 5

    @patch("cryptobot.indicators.regime.detect_regime")
    def test_ranging(self, mock_detect, _mock_smoother):
        """detect_regime 返回 ranging → ranging"""
        mock_detect.return_value = self._mock_regime("ranging", "neutral", "weak")
        result = _detect_market_regime({}, {"current_value": 50})
        assert result["regime"] == "ranging"
        assert result["params"]["max_leverage"] == 2  # P13.7: 震荡市杠杆 3→2

    @patch("cryptobot.indicators.regime.detect_regime")
    def test_volatile_from_fear_greed(self, mock_detect, _mock_smoother):
        """恐惧贪婪极端值 → 升级为 volatile"""
        mock_detect.return_value = self._mock_regime("trending", "bullish", "strong")
        result = _detect_market_regime({}, {"current_value": 15})
        assert result["regime"] == "volatile"
        assert result["params"]["max_leverage"] == 2

    @patch("cryptobot.indicators.regime.detect_regime")
    def test_detect_regime_failure_fallback(self, mock_detect, _mock_smoother):
        """detect_regime 异常时回退为 ranging"""
        mock_detect.side_effect = Exception("load_klines failed")
        result = _detect_market_regime({}, {"current_value": 50})
        assert result["regime"] == "ranging"
        assert "params" in result
