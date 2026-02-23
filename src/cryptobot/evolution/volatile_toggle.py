"""Volatile 策略自适应开关

累计 volatile 观望轮次 + 虚拟盘收益反馈 → 自动开启；
子状态策略持续亏损 → 自动关闭。

持久化: data/output/evolution/volatile_toggle_state.json
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from cryptobot.config import DATA_OUTPUT_DIR, load_settings

logger = logging.getLogger(__name__)

_STATE_PATH = DATA_OUTPUT_DIR / "evolution" / "volatile_toggle_state.json"


@dataclass(frozen=True)
class VolatileToggleState:
    """volatile 策略自适应开关状态"""

    enabled: bool = False
    consecutive_observe: int = 0
    virtual_pnl_positive_days: int = 0
    subtype_loss_streak: int = 0
    last_evaluated: str = ""
    toggle_history: list[dict] = field(default_factory=list)


def _load_state() -> VolatileToggleState:
    """加载持久化状态"""
    if not _STATE_PATH.exists():
        return VolatileToggleState()
    try:
        data = json.loads(_STATE_PATH.read_text())
        return VolatileToggleState(
            enabled=data.get("enabled", False),
            consecutive_observe=data.get("consecutive_observe", 0),
            virtual_pnl_positive_days=data.get("virtual_pnl_positive_days", 0),
            subtype_loss_streak=data.get("subtype_loss_streak", 0),
            last_evaluated=data.get("last_evaluated", ""),
            toggle_history=data.get("toggle_history", []),
        )
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("volatile_toggle_state.json 读取失败, 使用默认: %s", e)
        return VolatileToggleState()


def _save_state(state: VolatileToggleState) -> None:
    """原子写入状态"""
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2))
    tmp.rename(_STATE_PATH)


def record_volatile_cycle(regime: str, was_observe: bool) -> VolatileToggleState:
    """记录每轮工作流的 volatile 周期

    Args:
        regime: 当前 regime ("volatile" / "trending" / "ranging")
        was_observe: 本轮是否为 volatile 观望（策略未启用时 volatile = observe）
    """
    state = _load_state()

    if regime == "volatile" and was_observe:
        new_state = VolatileToggleState(
            enabled=state.enabled,
            consecutive_observe=state.consecutive_observe + 1,
            virtual_pnl_positive_days=state.virtual_pnl_positive_days,
            subtype_loss_streak=state.subtype_loss_streak,
            last_evaluated=state.last_evaluated,
            toggle_history=list(state.toggle_history),
        )
    else:
        # 非 volatile 或非 observe → 重置连续计数
        new_state = VolatileToggleState(
            enabled=state.enabled,
            consecutive_observe=0,
            virtual_pnl_positive_days=state.virtual_pnl_positive_days,
            subtype_loss_streak=state.subtype_loss_streak,
            last_evaluated=state.last_evaluated,
            toggle_history=list(state.toggle_history),
        )

    _save_state(new_state)
    return new_state


def _count_virtual_pnl_positive_days() -> int:
    """统计虚拟盘最近 7 天正收益天数"""
    try:
        from cryptobot.strategy.virtual_portfolio import load_portfolio
    except ImportError:
        return 0

    positive_days = 0
    for strategy in ("funding_arb", "grid"):
        try:
            portfolio = load_portfolio(strategy)
            # 统计最近 7 天的平仓交易正收益天数
            recent_trades = portfolio.closed_trades[-14:]  # 取最近一批
            daily_pnl: dict[str, float] = {}
            for trade in recent_trades:
                close_at = trade.get("closed_at", "")[:10]  # YYYY-MM-DD
                if close_at:
                    daily_pnl[close_at] = daily_pnl.get(close_at, 0) + trade.get("pnl", 0)
            # 最近 7 天
            sorted_days = sorted(daily_pnl.keys(), reverse=True)[:7]
            positive_days += sum(1 for d in sorted_days if daily_pnl[d] > 0)
        except Exception as e:
            logger.warning("统计虚拟盘 %s 收益天数失败: %s", strategy, e)
            continue

    return positive_days


def _count_subtype_loss_streak() -> int:
    """统计 volatile 子状态交易连续亏损笔数"""
    try:
        from cryptobot.journal.storage import get_all_records
    except ImportError:
        return 0

    try:
        records = get_all_records()
    except Exception as e:
        logger.warning("获取交易记录失败: %s", e)
        return 0

    # 筛选 volatile 子状态的已平仓记录，按时间倒序
    volatile_records = [
        r for r in records
        if r.status == "closed"
        and r.regime_name
        and r.regime_name.startswith("volatile_")
    ]
    volatile_records.sort(key=lambda r: r.timestamp, reverse=True)

    streak = 0
    for r in volatile_records:
        if r.actual_pnl_pct is not None and r.actual_pnl_pct < 0:
            streak += 1
        else:
            break
    return streak


def _check_14d_volatile_pnl_negative() -> bool:
    """检查最近 14 天 volatile 子状态净 PnL 是否为负"""
    try:
        from cryptobot.journal.storage import get_all_records
    except ImportError:
        return False

    try:
        records = get_all_records()
    except Exception as e:
        logger.warning("获取交易记录失败: %s", e)
        return False

    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    total_pnl = 0.0
    count = 0
    for r in records:
        if r.status != "closed":
            continue
        if not r.regime_name or not r.regime_name.startswith("volatile_"):
            continue
        try:
            ts = datetime.fromisoformat(r.timestamp[:19]).replace(tzinfo=timezone.utc)
            if ts < cutoff:
                continue
        except (ValueError, TypeError):
            continue
        if r.actual_pnl_pct is not None:
            total_pnl += r.actual_pnl_pct
            count += 1

    return count > 0 and total_pnl < 0


def evaluate_toggle(settings: dict | None = None) -> VolatileToggleState:
    """评估是否开启/关闭 volatile 策略 (每日调用)

    自动开启条件（满足任一）:
    1. consecutive_observe >= 3 — 连续 3 轮 volatile 全观望
    2. virtual_pnl_positive_days >= 3 — 虚拟盘最近 7 天有 >=3 天正收益

    自动关闭条件（满足任一）:
    1. subtype_loss_streak >= 5 — volatile 子状态交易连续 5 笔亏损
    2. 最近 14 天 volatile 子状态净 PnL 为负
    """
    if settings is None:
        settings = load_settings()

    cfg = settings.get("volatile_strategy", {})
    if not cfg.get("auto", False):
        return _load_state()

    enable_cycles = cfg.get("auto_enable_observe_cycles", 3)
    disable_streak = cfg.get("auto_disable_loss_streak", 5)

    state = _load_state()
    now = datetime.now(timezone.utc).isoformat()

    # 更新统计
    virtual_positive = _count_virtual_pnl_positive_days()
    loss_streak = _count_subtype_loss_streak()
    pnl_negative_14d = _check_14d_volatile_pnl_negative()

    action = None
    reason = ""

    if state.enabled:
        # 检查关闭条件
        if loss_streak >= disable_streak:
            action = "disabled"
            reason = f"连续 {loss_streak} 笔亏损 (阈值 {disable_streak})"
        elif pnl_negative_14d:
            action = "disabled"
            reason = "最近 14 天 volatile 子状态净 PnL 为负"
    else:
        # 检查开启条件
        if state.consecutive_observe >= enable_cycles:
            action = "enabled"
            reason = f"连续 {state.consecutive_observe} 轮观望 (阈值 {enable_cycles})"
        elif virtual_positive >= 3:
            action = "enabled"
            reason = f"虚拟盘 7 天内 {virtual_positive} 天正收益"

    new_enabled = state.enabled
    history = list(state.toggle_history)

    if action is not None:
        new_enabled = action == "enabled"
        history.append({"action": action, "reason": reason, "at": now})
        logger.info("[volatile_toggle] %s: %s", action, reason)

        # Telegram 通知
        try:
            from cryptobot.notify import send_message
            emoji = "\u2705" if new_enabled else "\u26d4"
            send_message(
                f"{emoji} Volatile 策略自适应: *{action}*\n{reason}"
            )
        except Exception as e:
            logger.warning("Telegram 通知发送失败: %s", e)

    new_state = VolatileToggleState(
        enabled=new_enabled,
        consecutive_observe=state.consecutive_observe,
        virtual_pnl_positive_days=virtual_positive,
        subtype_loss_streak=loss_streak,
        last_evaluated=now,
        toggle_history=history[-50:],  # 保留最近 50 条
    )
    _save_state(new_state)
    return new_state


def is_volatile_strategy_enabled(settings: dict | None = None) -> bool:
    """统一入口: 判断 volatile 策略是否启用

    auto=true → 读状态文件
    auto=false → 读 settings.yaml 的 enabled 字段
    """
    if settings is None:
        settings = load_settings()

    cfg = settings.get("volatile_strategy", {})

    if cfg.get("auto", False):
        state = _load_state()
        return state.enabled

    return cfg.get("enabled", False)
