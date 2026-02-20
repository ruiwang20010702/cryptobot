"""JSON API 路由"""

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/dashboard")
def get_dashboard():
    """聚合仪表盘数据"""
    from cryptobot.freqtrade_api import ft_api_get
    from cryptobot.signal.bridge import read_signals, read_pending_signals
    from cryptobot.journal.analytics import calc_performance

    positions = ft_api_get("/status") or []
    signals = read_signals(filter_expired=False)
    pending = read_pending_signals(filter_expired=False)
    perf = calc_performance(30)

    balance_data = ft_api_get("/balance")
    account_balance = 0.0
    if balance_data:
        for cur in balance_data.get("currencies", []):
            if cur.get("currency") == "USDT":
                account_balance = float(cur.get("balance", 0))
                break

    return {
        "account_balance": account_balance,
        "positions": positions,
        "signals": signals,
        "pending_signals": pending,
        "performance": perf,
    }


@router.get("/signals")
def get_signals():
    """当前信号列表"""
    from cryptobot.signal.bridge import read_signals, read_pending_signals

    return {
        "signals": read_signals(filter_expired=False),
        "pending": read_pending_signals(filter_expired=False),
    }


@router.get("/positions")
def get_positions():
    """当前持仓"""
    from cryptobot.freqtrade_api import ft_api_get

    positions = ft_api_get("/status") or []
    return {"positions": positions}


@router.get("/alerts")
def get_alerts():
    """当前告警"""
    from cryptobot.cli.monitor import _build_position_alerts, _build_signal_only_alerts
    from cryptobot.freqtrade_api import ft_api_get
    from cryptobot.signal.bridge import read_signals

    signals = read_signals(filter_expired=False)
    positions = ft_api_get("/status")

    if positions:
        alerts = _build_position_alerts(positions, signals)
    else:
        alerts = _build_signal_only_alerts(signals)

    return {"alerts": alerts}


@router.get("/journal/stats")
def get_journal_stats():
    """绩效统计"""
    from cryptobot.journal.analytics import calc_performance

    return calc_performance(30)


@router.patch("/signals/{symbol}")
def update_signal(symbol: str, updates: dict):
    """更新信号字段"""
    from cryptobot.signal.bridge import update_signal_field

    if not updates:
        raise HTTPException(status_code=400, detail="无更新字段")

    results = {}
    for field_name, value in updates.items():
        ok = update_signal_field(symbol, field_name, value)
        results[field_name] = "updated" if ok else "not_found"

    return {"symbol": symbol, "results": results}
