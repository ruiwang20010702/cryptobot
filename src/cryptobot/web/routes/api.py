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


@router.get("/klines/{symbol}")
def get_klines(symbol: str, interval: str = "4h", limit: int = 100):
    """K 线数据 (lightweight-charts 格式)"""
    from cryptobot.indicators.calculator import load_klines

    try:
        df = load_klines(symbol, interval)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # 取最近 limit 根 K 线
    df = df.tail(limit)

    klines = []
    for idx, row in df.iterrows():
        ts = int(idx.timestamp()) if hasattr(idx, "timestamp") else 0
        klines.append({
            "time": ts,
            "open": float(row.get("open", 0)),
            "high": float(row.get("high", 0)),
            "low": float(row.get("low", 0)),
            "close": float(row.get("close", 0)),
            "volume": float(row.get("volume", 0)),
        })

    return {"symbol": symbol, "interval": interval, "klines": klines}


@router.get("/journal/recent")
def get_journal_recent(limit: int = 20):
    """最近交易记录"""
    from cryptobot.journal.storage import get_all_records

    records = get_all_records()
    # 按时间倒序
    records.sort(key=lambda r: r.timestamp, reverse=True)
    records = records[:limit]

    return {
        "records": [r.to_dict() for r in records],
        "total": len(records),
    }


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
