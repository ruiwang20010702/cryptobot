"""JSON API 路由"""

from fastapi import APIRouter, HTTPException

from cryptobot.config import get_all_symbols

router = APIRouter()


def _validate_symbol(symbol: str) -> str:
    """验证 symbol 是否在白名单中，非法值抛 400"""
    valid = get_all_symbols()
    if symbol not in valid:
        raise HTTPException(status_code=400, detail=f"无效交易对: {symbol}")
    return symbol


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

    from cryptobot.capital_strategy import _extract_usdt_balance, detect_capital_tier
    account_balance = _extract_usdt_balance(ft_api_get("/balance"))

    # 余额脱敏: 返回层级 + 近似值 (精确到百位)
    tier_info = detect_capital_tier(account_balance)
    masked_balance = round(account_balance, -2) if account_balance >= 100 else round(account_balance)

    return {
        "account_balance": masked_balance,
        "balance_tier": tier_info.get("tier", "unknown"),
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


_VALID_KLINE_INTERVALS = {"5m", "15m", "1h", "4h", "1d"}


@router.get("/klines/{symbol}")
def get_klines(symbol: str, interval: str = "4h", limit: int = 100):
    """K 线数据 (lightweight-charts 格式)"""
    _validate_symbol(symbol)
    if interval not in _VALID_KLINE_INTERVALS:
        raise HTTPException(status_code=400, detail=f"无效 interval: {interval}")
    limit = max(1, min(limit, 200))

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
    limit = max(1, min(limit, 200))
    from cryptobot.journal.storage import get_all_records

    records = get_all_records()
    # 按时间倒序
    records.sort(key=lambda r: r.timestamp, reverse=True)
    records = records[:limit]

    return {
        "records": [r.to_dict() for r in records],
        "total": len(records),
    }


@router.get("/edge")
def get_edge(days: int = 30):
    """Edge 仪表盘数据"""
    from dataclasses import asdict

    from cryptobot.journal.edge import calc_edge, detect_edge_decay

    metrics = calc_edge(days)
    decay = detect_edge_decay()
    return {"metrics": asdict(metrics), "decay": decay}


@router.get("/correlation")
def get_correlation():
    """跨币种相关性矩阵"""
    from cryptobot.risk.correlation import calc_correlation_matrix

    symbols = get_all_symbols()
    matrix = calc_correlation_matrix(symbols)
    return {
        "matrix": matrix.matrix,
        "symbols": matrix.symbols,
        "computed_at": matrix.computed_at,
    }


_PATCH_ALLOWED_FIELDS = {"stop_loss", "take_profit", "leverage", "confidence"}


def _validate_patch_field(field_name: str, value) -> None:
    """校验 PATCH 字段类型"""
    if field_name in ("stop_loss", "take_profit"):
        if not isinstance(value, (int, float)):
            raise ValueError(f"{field_name} 必须为数字，收到 {type(value).__name__}")
    elif field_name == "confidence":
        if not isinstance(value, int) or not (0 <= value <= 100):
            raise ValueError("confidence 必须为 0-100 的整数")
    elif field_name == "leverage":
        if not isinstance(value, int) or value < 1 or value > 125:
            raise ValueError("leverage 必须为 1-125 的整数")


@router.patch("/signals/{symbol}")
def update_signal(symbol: str, updates: dict):
    """更新信号字段"""
    _validate_symbol(symbol)
    from cryptobot.signal.bridge import update_signal_field

    if not updates:
        raise HTTPException(status_code=400, detail="无更新字段")

    # 字段白名单
    disallowed = set(updates.keys()) - _PATCH_ALLOWED_FIELDS
    if disallowed:
        raise HTTPException(
            status_code=400,
            detail=f"不允许修改的字段: {', '.join(sorted(disallowed))}",
        )

    results = {}
    errors = {}
    for field_name, value in updates.items():
        try:
            _validate_patch_field(field_name, value)
            ok = update_signal_field(symbol, field_name, value)
            results[field_name] = "updated" if ok else "not_found"
        except ValueError as e:
            errors[field_name] = str(e)

    if errors and not results:
        raise HTTPException(status_code=400, detail=errors)

    return {"symbol": symbol, "results": results, "errors": errors}
