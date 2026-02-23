"""月度亏损熔断模块

根据连续亏损月数决定降仓或暂停交易：
- 连续 2 月亏损: 仓位缩放 50%, 禁止做多
- 连续 3 月亏损: 暂停交易 7 天
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MonthlyPnL:
    year_month: str       # "2026-02"
    pnl_usdt: float
    pnl_pct: float
    trade_count: int


@dataclass(frozen=True)
class CircuitBreakerState:
    consecutive_loss_months: int
    action: str           # "normal" | "reduce" | "suspend"
    position_scale: float  # 1.0 | 0.5 | 0.0
    block_long: bool
    resume_date: str | None  # 暂停恢复日期 (ISO)
    reason: str


def calc_monthly_pnl(months: int = 6) -> list[MonthlyPnL]:
    """计算最近 N 个自然月的月度盈亏

    只统计 status=="closed" 且 actual_pnl_usdt is not None 的记录。
    按 year_month 倒序返回（最近月在前）。
    """
    from cryptobot.journal.storage import get_all_records

    records = get_all_records()

    # 按自然月分组
    monthly: dict[str, list[float]] = defaultdict(list)
    for r in records:
        if r.status != "closed" or r.actual_pnl_usdt is None:
            continue
        if not r.timestamp:
            continue
        try:
            ts = datetime.fromisoformat(r.timestamp.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        ym = ts.strftime("%Y-%m")
        monthly[ym].append(r.actual_pnl_usdt)

    # 生成最近 N 个自然月的 key（逐月回退到上月最后一天）
    now = datetime.now(timezone.utc)
    recent_months: list[str] = []
    dt = now
    for _ in range(months):
        ym = dt.strftime("%Y-%m")
        recent_months.append(ym)
        # 回退到上月：当月1号减1天 = 上月最后一天
        dt = dt.replace(day=1) - timedelta(days=1)

    recent_months = sorted(set(recent_months), reverse=True)[:months]

    result: list[MonthlyPnL] = []
    for ym in recent_months:
        pnls = monthly.get(ym, [])
        total_pnl = sum(pnls)
        trade_count = len(pnls)
        # pnl_pct 无法精确计算（缺少月初余额），用 0.0 占位
        result.append(MonthlyPnL(
            year_month=ym,
            pnl_usdt=round(total_pnl, 2),
            pnl_pct=0.0,
            trade_count=trade_count,
        ))

    return result


def _load_cb_config() -> dict:
    """加载 circuit_breaker 配置，带默认值"""
    from cryptobot.config import load_settings

    settings = load_settings()
    cb = settings.get("risk", {}).get("circuit_breaker", {})
    return {
        "enabled": cb.get("enabled", True),
        "reduce_after_months": cb.get("reduce_after_months", 2),
        "suspend_after_months": cb.get("suspend_after_months", 3),
        "suspend_days": cb.get("suspend_days", 7),
        "position_scale": cb.get("position_scale", 0.5),
    }


def check_circuit_breaker() -> CircuitBreakerState:
    """检查月度熔断状态

    逻辑:
    1. 获取最近 N 个月盈亏（不含当月，只看已完结自然月）
    2. 从最近完结月向前数连续亏损月数
    3. 根据连续亏损月数决定 action
    """
    cfg = _load_cb_config()

    if not cfg["enabled"]:
        return CircuitBreakerState(
            consecutive_loss_months=0,
            action="normal",
            position_scale=1.0,
            block_long=False,
            resume_date=None,
            reason="月度熔断已禁用",
        )

    reduce_after = cfg["reduce_after_months"]
    suspend_after = cfg["suspend_after_months"]
    suspend_days = cfg["suspend_days"]
    scale = cfg["position_scale"]

    # 获取足够多的月度数据（多取一些以覆盖 suspend_after）
    monthly = calc_monthly_pnl(months=suspend_after + 1)

    # 排除当月（当月尚未结束）
    now = datetime.now(timezone.utc)
    current_ym = now.strftime("%Y-%m")
    completed = [m for m in monthly if m.year_month != current_ym]

    if not completed:
        return CircuitBreakerState(
            consecutive_loss_months=0,
            action="normal",
            position_scale=1.0,
            block_long=False,
            resume_date=None,
            reason="无已完结月度数据",
        )

    # 按时间倒序（最近月在前）计算连续亏损
    completed.sort(key=lambda m: m.year_month, reverse=True)
    consecutive = 0
    for m in completed:
        if m.trade_count == 0:
            break  # 无交易的月份中断连续计数
        if m.pnl_usdt < 0:
            consecutive += 1
        else:
            break

    # 判定 action
    if consecutive >= suspend_after:
        resume = (now + timedelta(days=suspend_days)).isoformat()
        return CircuitBreakerState(
            consecutive_loss_months=consecutive,
            action="suspend",
            position_scale=0.0,
            block_long=True,
            resume_date=resume,
            reason=f"连续 {consecutive} 个月亏损，暂停交易 {suspend_days} 天",
        )

    if consecutive >= reduce_after:
        return CircuitBreakerState(
            consecutive_loss_months=consecutive,
            action="reduce",
            position_scale=scale,
            block_long=True,
            resume_date=None,
            reason=f"连续 {consecutive} 个月亏损，仓位缩放至 {scale:.0%}，禁止做多",
        )

    return CircuitBreakerState(
        consecutive_loss_months=consecutive,
        action="normal",
        position_scale=1.0,
        block_long=False,
        resume_date=None,
        reason="月度盈亏正常" if consecutive == 0 else f"连续 {consecutive} 个月亏损，未达阈值",
    )
