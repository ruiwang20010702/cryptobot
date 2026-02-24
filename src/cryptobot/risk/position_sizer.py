"""仓位计算器 (风险约束凯利公式 + 动态优化)

核心原则:
- 单笔最大亏损 <= 总资金 2%
- 杠杆上限由 pairs.yaml 配置控制
- 仓位大小 = min(凯利最优, 风险约束上限)
- 高相关同向持仓自动缩减仓位
- ATR 暴涨自动降杠杆
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from cryptobot.config import get_pair_config, load_settings

logger = logging.getLogger(__name__)


# ─── Kelly 参数层级 Fallback ──────────────────────────────────────────


@dataclass(frozen=True)
class KellyParams:
    win_rate: float
    avg_win_loss_ratio: float
    kelly_fraction: float
    sample_size: int
    confidence_level: str  # "high"/"medium"/"low"
    source: str  # "journal"/"default"


def _calc_wr_ratio(records: list) -> tuple[float, float]:
    """从已平仓记录计算胜率和盈亏比"""
    if not records:
        return 0.5, 1.5

    wins = [r for r in records if (r.actual_pnl_pct or 0) > 0]
    wr = len(wins) / len(records) if records else 0.5

    gross_profit = sum(r.actual_pnl_pct for r in wins)
    gross_loss = abs(
        sum(r.actual_pnl_pct for r in records if (r.actual_pnl_pct or 0) < 0)
    )
    ratio = gross_profit / gross_loss if gross_loss > 0 else 1.5

    # clamp
    wr = max(0.1, min(wr, 0.9))
    ratio = max(0.5, min(ratio, 5.0))
    return wr, ratio


def _kelly_f(wr: float, ratio: float) -> float:
    """Kelly fraction: f* = wr - (1 - wr) / ratio"""
    if ratio <= 0:
        return 0.0
    f = wr - (1 - wr) / ratio
    return max(0.0, f)


def _confidence_level(n: int) -> str:
    if n >= 30:
        return "high"
    if n >= 15:
        return "medium"
    return "low"


def calc_kelly_params(
    symbol: str, action: str | None = None, days: int = 30,
) -> KellyParams:
    """层级 fallback 计算 Kelly 参数

    优先级:
    1. 币种+方向 (e.g. BTCUSDT long) -> >= 10 笔
    2. 币种 (e.g. BTCUSDT) -> >= 10 笔
    3. 方向 (e.g. long) -> >= 15 笔
    4. 全局 -> >= 20 笔
    5. 默认 (wr=0.5, ratio=1.5) -> 永远可用
    """
    try:
        from cryptobot.journal.storage import get_all_records

        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
        all_closed = []
        for r in get_all_records():
            if r.status != "closed" or not r.timestamp:
                continue
            try:
                ts = datetime.fromisoformat(r.timestamp.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                continue
            if ts >= cutoff_dt:
                all_closed.append(r)
    except Exception as e:
        logger.warning("Kelly 记录加载失败: %s, 使用默认值", e)
        f = _kelly_f(0.5, 1.5)
        return KellyParams(0.5, 1.5, f, 0, "low", "default")

    # 1) 币种+方向 → source="journal" (最精确)
    if action:
        sym_dir = [
            r for r in all_closed
            if r.symbol == symbol and r.action == action
        ]
        if len(sym_dir) >= 10:
            wr, ratio = _calc_wr_ratio(sym_dir)
            f = _kelly_f(wr, ratio)
            return KellyParams(
                wr, ratio, f, len(sym_dir),
                _confidence_level(len(sym_dir)), "journal",
            )

    # 2) 币种 → source="journal"
    sym_all = [r for r in all_closed if r.symbol == symbol]
    if len(sym_all) >= 10:
        wr, ratio = _calc_wr_ratio(sym_all)
        f = _kelly_f(wr, ratio)
        return KellyParams(
            wr, ratio, f, len(sym_all),
            _confidence_level(len(sym_all)), "journal",
        )

    # 3) 方向 → source="journal"
    if action:
        dir_all = [r for r in all_closed if r.action == action]
        if len(dir_all) >= 15:
            wr, ratio = _calc_wr_ratio(dir_all)
            f = _kelly_f(wr, ratio)
            return KellyParams(
                wr, ratio, f, len(dir_all),
                _confidence_level(len(dir_all)), "journal",
            )

    # 4) 全局 → source="journal"
    if len(all_closed) >= 20:
        wr, ratio = _calc_wr_ratio(all_closed)
        f = _kelly_f(wr, ratio)
        return KellyParams(
            wr, ratio, f, len(all_closed),
            _confidence_level(len(all_closed)), "journal",
        )

    # 5) 默认
    wr, ratio = 0.5, 1.5
    f = _kelly_f(wr, ratio)
    return KellyParams(wr, ratio, f, len(all_closed), "low", "default")


# ─── 旧接口保留 (向后兼容) ──────────────────────────────────────────


def _load_kelly_params(
    symbol: str, action: str | None = None,
) -> tuple[float, float]:
    """从 journal 历史数据加载 Kelly 参数 (兼容旧调用)

    内部委托给 calc_kelly_params，返回 (win_rate, avg_win_loss_ratio)。
    """
    kp = calc_kelly_params(symbol, action)
    return kp.win_rate, kp.avg_win_loss_ratio


# ─── 相关性仓位调整 ─────────────────────────────────────────────────


def calc_portfolio_adjusted_size(
    symbol: str,
    base_size_usdt: float,
    positions: list[dict],
    corr_matrix: object | None = None,
    high_corr_threshold: float = 0.7,
    reduction_factor: float = 0.5,
    action: str | None = None,
) -> dict:
    """高相关同向持仓 -> 仓位缩减

    positions: [{"symbol": "...", "action": "...", "size_usdt": ...}]
    action: 新信号方向 (long/short)，仅对同向高相关仓位缩减

    统计 positions 中与 symbol 相关性 > high_corr_threshold 且同方向的数量 n。
    n >= 3: base_size * reduction_factor^2
    n >= 2: base_size * reduction_factor
    否则不缩减。

    Returns:
        {"adjusted_size_usdt": float, "reduction_applied": bool,
         "n_correlated": int, "reason": str}
    """
    if not positions or corr_matrix is None:
        return {
            "adjusted_size_usdt": base_size_usdt,
            "reduction_applied": False,
            "n_correlated": 0,
            "reason": "无持仓或无相关性矩阵",
        }

    try:
        from cryptobot.risk.correlation import get_correlation
    except ImportError:
        return {
            "adjusted_size_usdt": base_size_usdt,
            "reduction_applied": False,
            "n_correlated": 0,
            "reason": "相关性模块不可用",
        }

    n_correlated = 0
    corr_details = []

    for pos in positions:
        pos_sym = pos.get("symbol", "")
        if pos_sym == symbol:
            continue
        # 仅对同向持仓缩减（做多和做多同向，做空和做空同向）
        if action is not None and pos.get("action") != action:
            continue
        corr = get_correlation(corr_matrix, symbol, pos_sym)
        if corr > high_corr_threshold:
            n_correlated += 1
            corr_details.append(f"{pos_sym}(r={corr:.2f})")

    if n_correlated >= 3:
        factor = reduction_factor ** 2
        adjusted = base_size_usdt * factor
        reason = f"与 {n_correlated} 个持仓高相关: {', '.join(corr_details)}"
    elif n_correlated >= 2:
        factor = reduction_factor
        adjusted = base_size_usdt * factor
        reason = f"与 {n_correlated} 个持仓高相关: {', '.join(corr_details)}"
    else:
        adjusted = base_size_usdt
        reason = "无高相关持仓"

    return {
        "adjusted_size_usdt": round(adjusted, 2),
        "reduction_applied": n_correlated >= 2,
        "n_correlated": n_correlated,
        "reason": reason,
    }


# ─── 波动率杠杆调整 ─────────────────────────────────────────────────


def calc_volatility_adjusted_leverage(
    symbol: str,
    base_leverage: int,
    current_atr_pct: float,
    hist_atr_pct: float,
) -> int:
    """ATR 暴涨 -> 降杠杆

    current_atr > hist_atr * 2.0: 降两级
    current_atr > hist_atr * 1.5: 降一级
    最低 1
    """
    if hist_atr_pct <= 0:
        return max(1, base_leverage)

    ratio = current_atr_pct / hist_atr_pct

    if ratio > 2.0:
        adjusted = base_leverage - 2
    elif ratio > 1.5:
        adjusted = base_leverage - 1
    else:
        adjusted = base_leverage

    return max(1, adjusted)


# ─── 主入口 ──────────────────────────────────────────────────────────


def calc_position_size(
    symbol: str,
    account_balance: float,
    entry_price: float,
    stop_loss_price: float,
    leverage: int | None = None,
    win_rate: float | None = None,
    avg_win_loss_ratio: float | None = None,
    action: str | None = None,
    confidence: int | None = None,
    regime: str = "",
    *,
    positions: list[dict] | None = None,
    corr_matrix: object | None = None,
    current_atr_pct: float | None = None,
    hist_atr_pct: float | None = None,
) -> dict:
    """计算仓位大小

    Args:
        symbol: 交易对 (BTCUSDT)
        account_balance: 账户总资金 (USDT)
        entry_price: 入场价
        stop_loss_price: 止损价
        leverage: 杠杆倍数 (None=使用默认)
        win_rate: 历史胜率
        avg_win_loss_ratio: 平均盈亏比
        action: 方向 (long/short)
        confidence: 置信度
        regime: 市场状态
        positions: 当前持仓列表 (用于相关性调整)
        corr_matrix: 相关性矩阵
        current_atr_pct: 当前 ATR%
        hist_atr_pct: 历史 ATR%

    Returns:
        仓位计算结果
    """
    # 自动从 journal 加载 Kelly 参数
    kelly_params = None
    if win_rate is None or avg_win_loss_ratio is None:
        kelly_params = calc_kelly_params(symbol, action)
        if win_rate is None:
            win_rate = kelly_params.win_rate
        if avg_win_loss_ratio is None:
            avg_win_loss_ratio = kelly_params.avg_win_loss_ratio

    settings = load_settings()
    pair_cfg = get_pair_config(symbol)
    risk = settings.get("risk", {})

    # 杠杆
    if leverage is None:
        leverage = pair_cfg["default_leverage"] if pair_cfg else 3
    max_lev = pair_cfg["leverage_range"][1] if pair_cfg else 5
    leverage = min(leverage, max_lev)

    # 币种分级杠杆限制
    try:
        from cryptobot.risk.symbol_profile import get_symbol_grade
        sym_grade = get_symbol_grade(symbol)
        if sym_grade is not None and sym_grade.recommended_leverage < leverage:
            leverage = sym_grade.recommended_leverage
    except Exception:
        pass

    # 波动率杠杆调整
    if current_atr_pct is not None and hist_atr_pct is not None:
        leverage = calc_volatility_adjusted_leverage(
            symbol, leverage, current_atr_pct, hist_atr_pct,
        )
        leverage = min(leverage, max_lev)

    # 止损距离
    if entry_price <= 0 or stop_loss_price <= 0:
        raise ValueError("价格必须大于 0")

    sl_distance_pct = abs(entry_price - stop_loss_price) / entry_price * 100

    if sl_distance_pct == 0:
        raise ValueError("止损价不能等于入场价")

    # --- 方法 1: 固定风险法 ---
    max_loss_pct = risk.get("max_loss", {}).get("per_trade_pct", 2)
    max_loss_amount = account_balance * max_loss_pct / 100

    # 实际止损 = 止损距离% * 杠杆
    effective_sl_pct = sl_distance_pct * leverage / 100
    risk_position = (
        max_loss_amount / effective_sl_pct if effective_sl_pct > 0 else 0
    )

    # --- 方法 2: 凯利公式 (复用 _kelly_f) ---
    kelly_fraction = _kelly_f(win_rate, avg_win_loss_ratio)
    if kelly_fraction > 0:
        # P17-B3: 三维 Kelly 缩放 (regime, high_conf, is_long)
        # 做多缩放系数降低 ~30%，反映加密市场结构性做空优势
        _KELLY_SCALE = {
            ("trending", True, False): 0.6,
            ("trending", True, True): 0.4,
            ("trending", False, False): 0.5,
            ("trending", False, True): 0.35,
            ("ranging", True, False): 0.4,
            ("ranging", True, True): 0.25,
            ("ranging", False, False): 0.3,
            ("ranging", False, True): 0.2,
            ("volatile", True, False): 0.35,
            ("volatile", True, True): 0.2,
            ("volatile", False, False): 0.25,
            ("volatile", False, True): 0.15,
        }
        high_conf = confidence >= 85 if confidence is not None else False
        is_long = action == "long"
        kelly_scale = _KELLY_SCALE.get(
            (regime, high_conf, is_long),
            0.25 if is_long else 0.5,
        )
        kelly_fraction *= kelly_scale

    kelly_position = account_balance * kelly_fraction

    # --- 仓位上限约束 ---
    max_single_pct = risk.get("max_single_position_pct", 25)
    max_single_position = account_balance * max_single_pct / 100

    # 最终仓位 = min(风险法, 凯利法, 上限)，取保证金口径
    # 使用 1e-9 阈值避免浮点精度导致的近零凯利值
    if kelly_fraction > 1e-9:
        margin_amount = min(
            risk_position, kelly_position, max_single_position,
        )
    else:
        margin_amount = min(risk_position, max_single_position)

    # --- 相关性仓位缩减 ---
    portfolio_adj = None
    if positions is not None and corr_matrix is not None:
        portfolio_adj = calc_portfolio_adjusted_size(
            symbol, margin_amount, positions, corr_matrix, action=action,
        )
        margin_amount = portfolio_adj["adjusted_size_usdt"]

    # 最小金额：不足则不开仓（返回 0），由下游过滤
    min_amount = pair_cfg.get("min_amount_usdt", 50) if pair_cfg else 50
    if margin_amount < min_amount:
        margin_amount = 0

    # 名义仓位 = 保证金 * 杠杆
    notional = margin_amount * leverage

    result = {
        "symbol": symbol,
        "leverage": leverage,
        "margin_usdt": round(margin_amount, 2),
        "notional_usdt": round(notional, 2),
        "sl_distance_pct": round(sl_distance_pct, 2),
        "max_loss_usdt": round(margin_amount * effective_sl_pct, 2),
        "max_loss_pct_of_balance": round(
            margin_amount * effective_sl_pct / account_balance * 100, 2,
        ),
        "kelly_fraction": round(kelly_fraction, 4),
        "risk_position": round(risk_position, 2),
        "kelly_position": round(kelly_position, 2),
        "account_balance": account_balance,
    }

    # 附加 Kelly 元数据
    if kelly_params is not None:
        result["kelly_source"] = kelly_params.source
        result["kelly_confidence"] = kelly_params.confidence_level
        result["kelly_sample_size"] = kelly_params.sample_size

    # 附加组合调整元数据
    if portfolio_adj is not None:
        result["portfolio_reduction"] = portfolio_adj["reduction_applied"]
        result["portfolio_n_correlated"] = portfolio_adj["n_correlated"]

    return result
