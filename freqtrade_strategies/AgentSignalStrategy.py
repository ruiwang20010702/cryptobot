"""
AgentSignalStrategy - 读取 Agent 团队生成的信号，由 Freqtrade 执行交易

工作模式:
1. Live/Dry-run: 读取 signal.json 中的 Agent 信号 → 执行交易
2. 回测: 使用简单技术指标规则生成信号 (模拟 Agent 决策)

核心回调:
- populate_indicators: 计算基础指标
- populate_entry_trend: 入场信号 (Agent 信号 或 回测规则)
- populate_exit_trend: 出场信号
- custom_stoploss: 动态止损 + 移动止盈
- custom_exit: 最终止盈级别全仓平仓
- adjust_trade_position: 分批止盈减仓
- custom_stake_amount: Agent 仓位控制
- leverage: 杠杆控制
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas_ta  # noqa: F401
from freqtrade.strategy import IStrategy, DecimalParameter
from pandas import DataFrame

logger = logging.getLogger(__name__)

SIGNAL_FILE = Path("data/output/signals/signal.json")


def _ensure_utc(dt: datetime) -> datetime:
    """确保 datetime 带 UTC 时区信息"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class AgentSignalStrategy(IStrategy):
    """读取 Agent 团队生成的信号，由 Freqtrade 执行交易"""

    INTERFACE_VERSION = 3
    can_short = True

    # ROI (回测用，实盘由 Agent 信号控制)
    minimal_roi = {"0": 0.20, "120": 0.10, "240": 0.05, "480": 0.02}
    stoploss = -0.05
    trailing_stop = False
    timeframe = "5m"
    process_only_new_candles = True
    startup_candle_count = 100  # 需要 100 根 K 线热身

    # 移动止盈参数 (可 Hyperopt 优化)
    ts_profit_1 = DecimalParameter(0.03, 0.08, default=0.05, space="sell")
    ts_profit_2 = DecimalParameter(0.08, 0.15, default=0.10, space="sell")
    ts_profit_3 = DecimalParameter(0.15, 0.30, default=0.20, space="sell")

    def __init__(self, config: dict | None = None) -> None:
        if config is not None:
            super().__init__(config)
        # 信号缓存（基于文件 mtime，避免每根 K 线重复读磁盘）
        self._signal_cache: list[dict] = []
        self._signal_mtime: float = 0.0

    def _read_signals(self) -> list[dict]:
        """读取并过滤有效信号（带 mtime 缓存）"""
        if not SIGNAL_FILE.exists():
            return []
        mtime = SIGNAL_FILE.stat().st_mtime
        if mtime == self._signal_mtime and self._signal_cache is not None:
            return self._signal_cache
        try:
            data = json.loads(SIGNAL_FILE.read_text())
            now = datetime.now(timezone.utc)
            self._signal_cache = [
                s for s in data.get("signals", [])
                if _ensure_utc(datetime.fromisoformat(s["expires_at"])) > now
            ]
            self._signal_mtime = mtime
            return self._signal_cache
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"读取信号失败: {e}")
            return []

    def _get_signal_for_pair(self, pair: str) -> dict | None:
        """获取指定交易对的信号"""
        symbol = pair.replace("/", "").replace(":USDT", "")
        for s in self._read_signals():
            if s["symbol"] == symbol:
                return s
        return None

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """计算指标 (回测和实盘通用)"""
        # EMA
        dataframe["ema_7"] = dataframe.ta.ema(length=7)
        dataframe["ema_25"] = dataframe.ta.ema(length=25)
        dataframe["ema_99"] = dataframe.ta.ema(length=99)

        # RSI
        dataframe["rsi"] = dataframe.ta.rsi(length=14)

        # MACD
        macd = dataframe.ta.macd(fast=12, slow=26, signal=9)
        if macd is not None:
            dataframe["macd"] = macd.iloc[:, 0]
            dataframe["macd_signal"] = macd.iloc[:, 1]
            dataframe["macd_hist"] = macd.iloc[:, 2]

        # Bollinger Bands
        bbands = dataframe.ta.bbands(length=20, std=2)
        if bbands is not None:
            dataframe["bb_lower"] = bbands.iloc[:, 0]
            dataframe["bb_mid"] = bbands.iloc[:, 1]
            dataframe["bb_upper"] = bbands.iloc[:, 2]

        # ATR
        dataframe["atr"] = dataframe.ta.atr(length=14)

        # Volume EMA
        dataframe["volume_ema"] = dataframe.ta.ema(close=dataframe["volume"], length=20)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """入场信号"""
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0

        # --- 模式 1: 读取 Agent 信号 (实盘/模拟盘) ---
        signal = self._get_signal_for_pair(metadata["pair"])
        if signal is not None:
            # P8: 入场价格范围检查
            entry_range = signal.get("entry_price_range")
            if entry_range and len(entry_range) == 2:
                current_price = dataframe["close"].iloc[-1]
                low, high = entry_range
                tolerance = (high - low) * 0.15  # 15% 容差
                if not (low - tolerance <= current_price <= high + tolerance):
                    logger.info(
                        "[Agent] %s 价格 %.2f 不在范围 [%.2f, %.2f]±15%%, 跳过",
                        metadata["pair"], current_price, low, high,
                    )
                    return dataframe

            if signal["action"] == "long":
                dataframe.loc[dataframe.index[-1], "enter_long"] = 1
                logger.info(
                    f"[Agent] {metadata['pair']} LONG "
                    f"置信度={signal.get('confidence')} "
                    f"杠杆={signal.get('leverage')}"
                )
            elif signal["action"] == "short":
                dataframe.loc[dataframe.index[-1], "enter_short"] = 1
                logger.info(
                    f"[Agent] {metadata['pair']} SHORT "
                    f"置信度={signal.get('confidence')} "
                    f"杠杆={signal.get('leverage')}"
                )
            return dataframe

        # --- 模式 2: 规则化信号 (回测用) ---
        # 做多条件: EMA 多头排列 + RSI 超卖回升 + MACD 金叉
        dataframe.loc[
            (
                (dataframe["ema_7"] > dataframe["ema_25"])
                & (dataframe["ema_25"] > dataframe["ema_99"])
                & (dataframe["rsi"] > 30)
                & (dataframe["rsi"] < 70)
                & (dataframe["macd_hist"] > 0)
                & (dataframe["macd_hist"].shift(1) <= 0)
                & (dataframe["volume"] > dataframe["volume_ema"])
            ),
            "enter_long",
        ] = 1

        # 做空条件: EMA 空头排列 + RSI 超买回落 + MACD 死叉
        dataframe.loc[
            (
                (dataframe["ema_7"] < dataframe["ema_25"])
                & (dataframe["ema_25"] < dataframe["ema_99"])
                & (dataframe["rsi"] < 70)
                & (dataframe["rsi"] > 30)
                & (dataframe["macd_hist"] < 0)
                & (dataframe["macd_hist"].shift(1) >= 0)
                & (dataframe["volume"] > dataframe["volume_ema"])
            ),
            "enter_short",
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """出场信号"""
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0

        # Agent 平仓信号
        signal = self._get_signal_for_pair(metadata["pair"])
        if signal is not None:
            if signal["action"] == "close_long":
                dataframe.loc[dataframe.index[-1], "exit_long"] = 1
            elif signal["action"] == "close_short":
                dataframe.loc[dataframe.index[-1], "exit_short"] = 1
            return dataframe

        # 回测规则出场
        # 多单出场: RSI 超买 或 EMA 死叉
        dataframe.loc[
            (dataframe["rsi"] > 75) | (dataframe["ema_7"] < dataframe["ema_25"]),
            "exit_long",
        ] = 1

        # 空单出场: RSI 超卖 或 EMA 金叉
        dataframe.loc[
            (dataframe["rsi"] < 25) | (dataframe["ema_7"] > dataframe["ema_25"]),
            "exit_short",
        ] = 1

        return dataframe

    def custom_stoploss(
        self,
        pair: str,
        trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float:
        """动态止损 + 移动止盈

        优先级:
        1. Agent trailing_stop_pct (AI 推荐的尾随比例，盈利 > 2% 后激活)
        2. Agent 固定止损价
        3. 三档移动止盈
        4. 默认止损
        """
        signal = self._get_signal_for_pair(pair)

        # 优先级 1: Agent 尾随止盈（盈利 > 2% 后激活）
        if signal and signal.get("trailing_stop_pct") and current_profit > 0.02:
            trail_pct = signal["trailing_stop_pct"] / 100
            return -(current_profit - trail_pct)

        # 优先级 2: Agent 固定止损价
        if signal and signal.get("stop_loss") and trade:
            sl_price = signal["stop_loss"]
            if trade.is_short:
                sl_pct = (sl_price - current_rate) / current_rate
            else:
                sl_pct = (current_rate - sl_price) / current_rate
            if sl_pct > 0:
                return -sl_pct
            else:
                # 价格已穿越止损价，立即触发止损
                return self.stoploss

        # 优先级 3: 移动止盈（三档尾随）
        # 若有 AI take_profit 且未全部执行完，跳过固定尾随（避免与分批止盈冲突）
        if signal and signal.get("take_profit") and trade:
            tp_list = signal["take_profit"]
            filled_orders = trade.orders or []
            all_tp_filled = all(
                any(
                    o.ft_order_tag == f"tp_{i}_{tp.get('price')}"
                    and o.ft_is_open is False
                    for o in filled_orders
                )
                for i, tp in enumerate(tp_list[:-1])
            ) if len(tp_list) >= 2 else True
            if not all_tp_filled:
                return self.stoploss
            # 全部 TP 完成后，保护尾随
            if current_profit > 0.03:
                return -(current_profit - 0.02)

        if current_profit > self.ts_profit_3.value:      # > 20%: 尾随 3%（收紧保护）
            return -(current_profit - 0.03)
        elif current_profit > self.ts_profit_2.value:     # > 10%: 尾随 5%
            return -(current_profit - 0.05)
        elif current_profit > self.ts_profit_1.value:     # > 5%: 移至成本线
            return -0.001

        return self.stoploss

    def custom_exit(
        self,
        pair: str,
        trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> str | None:
        """最终止盈级别全仓平仓

        分批止盈的中间级别由 adjust_trade_position 减仓处理，
        最后一级在这里触发全部平仓。
        """
        signal = self._get_signal_for_pair(pair)
        if not signal or not signal.get("take_profit"):
            return None

        tp_list = signal["take_profit"]
        if not tp_list:
            return None

        # 最后一级止盈 → 全仓平仓
        last_tp = tp_list[-1]
        tp_price = last_tp.get("price")
        if tp_price is None:
            return None

        hit = (not trade.is_short and current_rate >= tp_price) or \
              (trade.is_short and current_rate <= tp_price)

        if hit:
            return f"take_profit_full_{tp_price}"

        return None

    def adjust_trade_position(
        self,
        trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        min_stake: float,
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        current_exit_profit: float,
        **kwargs,
    ) -> float | None:
        """分批止盈：按 AI 信号的 take_profit 列表逐级减仓

        返回负数表示减仓金额。最后一级由 custom_exit 全平。
        """
        signal = self._get_signal_for_pair(trade.pair)
        if not signal or not signal.get("take_profit"):
            return None

        tp_list = signal["take_profit"]
        if len(tp_list) < 2:
            # 只有一级止盈，由 custom_exit 全平处理
            return None

        filled_orders = trade.orders or []

        # 遍历除最后一级外的所有止盈级别
        for i, tp in enumerate(tp_list[:-1]):
            tp_price = tp.get("price")
            tp_pct = tp.get("pct", 50)
            if tp_price is None:
                continue

            hit = (not trade.is_short and current_rate >= tp_price) or \
                  (trade.is_short and current_rate <= tp_price)

            # 检查是否已对该级别执行过减仓
            tag = f"tp_{i}_{tp_price}"
            already_filled = any(
                o.ft_order_tag == tag
                for o in filled_orders
                if o.ft_is_open is False
            )

            if hit and not already_filled:
                # P4: 基于剩余仓位计算减仓量
                total_tp_pct_filled = sum(
                    tp_list[j].get("pct", 0)
                    for j in range(i)
                    if any(
                        o.ft_order_tag == f"tp_{j}_{tp_list[j].get('price')}"
                        and o.ft_is_open is False
                        for o in filled_orders
                    )
                )
                remaining_pct = 100 - total_tp_pct_filled
                reduce_pct = min(tp_pct, remaining_pct)
                reduce_amount = trade.amount * current_rate / trade.leverage * reduce_pct / 100
                logger.info(
                    "[Agent] %s 分批止盈 TP%d @ %.2f, 减仓 %.1f%% (剩余 %.1f%%)",
                    trade.pair, i + 1, tp_price, reduce_pct, remaining_pct,
                )
                return -reduce_amount

        return None

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        """控制初始仓位大小"""
        signal = self._get_signal_for_pair(pair)
        if signal and signal.get("position_size_usdt"):
            return min(signal["position_size_usdt"], max_stake)
        return proposed_stake

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        """杠杆控制

        优先读取 Agent 信号杠杆，否则使用默认值。
        硬上限 5x。
        """
        signal = self._get_signal_for_pair(pair)
        if signal and signal.get("leverage"):
            lev = float(signal["leverage"])
            return min(lev, max_leverage, 5.0)
        return min(3.0, max_leverage)
