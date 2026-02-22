"""跨币种相关性风控

纯 Python 实现 Pearson 相关系数，检测高相关同向仓位集中风险。
"""

import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptobot.config import DATA_OUTPUT_DIR

logger = logging.getLogger(__name__)

_CACHE_PATH = DATA_OUTPUT_DIR / "evolution" / "correlation.json"
_CACHE_TTL = 4 * 3600  # 4 小时缓存


@dataclass(frozen=True)
class CorrelationMatrix:
    symbols: list[str]
    matrix: dict[str, float]  # key = "SYM1:SYM2", value = correlation
    computed_at: str  # ISO timestamp


@dataclass(frozen=True)
class PortfolioRiskCheck:
    passed: bool
    violations: list[str]
    effective_positions: float  # 相关性折算后独立仓位数


# ─── 纯 Python 统计工具 ───────────────────────────────────────────────


def _mean(xs: list[float]) -> float:
    """算术平均值"""
    if not xs:
        return 0.0
    return sum(xs) / len(xs)


def _pearson(xs: list[float], ys: list[float]) -> float:
    """纯 Python Pearson 相关系数

    Returns:
        相关系数 [-1, 1]，数据不足或方差为 0 时返回 0.0
    """
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0

    xs, ys = xs[:n], ys[:n]
    mx, my = _mean(xs), _mean(ys)

    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    var_x = sum((x - mx) ** 2 for x in xs)
    var_y = sum((y - my) ** 2 for y in ys)

    denom = math.sqrt(var_x * var_y)
    if denom < 1e-12:
        return 0.0

    r = cov / denom
    # 数值稳定性：clamp 到 [-1, 1]
    return max(-1.0, min(1.0, r))


def _returns_from_closes(closes: list[float]) -> list[float]:
    """收盘价序列 -> 收益率序列"""
    if len(closes) < 2:
        return []
    return [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
        if closes[i - 1] != 0
    ]


# ─── 核心函数 ────────────────────────────────────────────────────────


def _make_key(sym_a: str, sym_b: str) -> str:
    """生成对称 key：按字母序排列"""
    a, b = sorted([sym_a, sym_b])
    return f"{a}:{b}"


def _load_closes(symbol: str, timeframe: str, limit: int) -> list[float]:
    """加载 K 线收盘价列表，失败返回空列表"""
    try:
        from cryptobot.indicators.calculator import load_klines

        df = load_klines(symbol, timeframe)
        closes = df["close"].astype(float).tolist()
        return closes[-limit:] if len(closes) > limit else closes
    except Exception as e:
        logger.warning("加载 %s K 线失败: %s", symbol, e)
        return []


def calc_correlation_matrix(
    symbols: list[str],
    timeframe: str = "4h",
    lookback_bars: int = 30,
) -> CorrelationMatrix:
    """两两 Pearson 相关性

    1. 对每对 (sym_a, sym_b)，加载 lookback_bars 根 K 线收盘价
    2. 计算收益率序列 returns = (close[i] - close[i-1]) / close[i-1]
    3. Pearson 相关系数 = cov(a,b) / (std(a) * std(b))
    4. 自相关 = 1.0
    5. 对称: corr(a,b) = corr(b,a)

    K 线加载失败时，该对相关性设为 0.0。
    """
    # 尝试读缓存
    cached = _load_cache(symbols)
    if cached is not None:
        return cached

    # 加载所有收益率
    returns_map: dict[str, list[float]] = {}
    for sym in symbols:
        closes = _load_closes(sym, timeframe, lookback_bars + 1)
        returns_map[sym] = _returns_from_closes(closes)

    matrix: dict[str, float] = {}
    for i, sym_a in enumerate(symbols):
        # 自相关
        matrix[_make_key(sym_a, sym_a)] = 1.0
        for sym_b in symbols[i + 1:]:
            r_a = returns_map.get(sym_a, [])
            r_b = returns_map.get(sym_b, [])
            corr = _pearson(r_a, r_b) if r_a and r_b else 0.0
            key = _make_key(sym_a, sym_b)
            matrix[key] = round(corr, 4)

    now = datetime.now(timezone.utc).isoformat()
    result = CorrelationMatrix(symbols=symbols, matrix=matrix, computed_at=now)

    _save_cache(result)
    return result


def get_correlation(
    matrix: CorrelationMatrix, sym_a: str, sym_b: str,
) -> float:
    """从矩阵中查询两个币种的相关性"""
    if sym_a == sym_b:
        return 1.0
    key = _make_key(sym_a, sym_b)
    return matrix.matrix.get(key, 0.0)


def check_portfolio_correlation(
    positions: list[dict],
    new_signal: dict,
    matrix: CorrelationMatrix,
    max_correlated_same_direction: int = 3,
    high_corr_threshold: float = 0.7,
) -> PortfolioRiskCheck:
    """检查新信号是否会导致高相关同向仓位过多

    规则: 已有持仓中，与 new_signal.symbol 相关性 > high_corr_threshold
    且同方向的数量如果 >= max_correlated_same_direction，则 passed=False
    """
    if not positions:
        return PortfolioRiskCheck(passed=True, violations=[], effective_positions=0.0)

    new_sym = new_signal.get("symbol", "")
    new_action = new_signal.get("action", "")

    violations: list[str] = []
    correlated_same_dir = 0

    for pos in positions:
        pos_sym = pos.get("symbol", "")
        # 判断持仓方向：is_short 或 action 字段
        if pos.get("is_short"):
            pos_action = "short"
        elif "action" in pos:
            pos_action = pos["action"]
        else:
            pos_action = "long"

        corr = get_correlation(matrix, new_sym, pos_sym)
        if corr > high_corr_threshold and pos_action == new_action:
            correlated_same_dir += 1

    if correlated_same_dir >= max_correlated_same_direction:
        violations.append(
            f"{new_sym} 与 {correlated_same_dir} 个同向持仓高度相关 "
            f"(>{high_corr_threshold})，上限 {max_correlated_same_direction}"
        )

    # 计算有效仓位数（含新信号）
    all_positions = [*positions, {"symbol": new_sym, "action": new_action}]
    eff = calc_effective_positions(all_positions, matrix)

    passed = len(violations) == 0
    return PortfolioRiskCheck(
        passed=passed, violations=violations, effective_positions=eff,
    )


def calc_effective_positions(
    positions: list[dict],
    matrix: CorrelationMatrix,
) -> float:
    """N_eff = N^2 / sum(|corr_ij|)

    N = 持仓数
    sum 对所有持仓对的绝对相关性求和（含对角线 = 1.0）
    """
    n = len(positions)
    if n == 0:
        return 0.0

    total_abs_corr = 0.0
    for i, pos_a in enumerate(positions):
        sym_a = pos_a.get("symbol", "")
        for j, pos_b in enumerate(positions):
            sym_b = pos_b.get("symbol", "")
            corr = get_correlation(matrix, sym_a, sym_b)
            total_abs_corr += abs(corr)

    if total_abs_corr < 1e-12:
        return float(n)

    return round(n * n / total_abs_corr, 2)


# ─── 缓存 ────────────────────────────────────────────────────────────


def _load_cache(symbols: list[str]) -> CorrelationMatrix | None:
    """读取缓存，symbols 必须完全匹配且未过期"""
    if not _CACHE_PATH.exists():
        return None
    try:
        data = json.loads(_CACHE_PATH.read_text())
        if time.time() - data.get("_updated_at", 0) > _CACHE_TTL:
            return None
        cached_symbols = sorted(data.get("symbols", []))
        if cached_symbols != sorted(symbols):
            return None
        return CorrelationMatrix(
            symbols=data["symbols"],
            matrix=data["matrix"],
            computed_at=data["computed_at"],
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _save_cache(matrix: CorrelationMatrix) -> None:
    """持久化相关性矩阵到 JSON"""
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "symbols": matrix.symbols,
            "matrix": matrix.matrix,
            "computed_at": matrix.computed_at,
            "_updated_at": time.time(),
        }
        tmp = _CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp.rename(_CACHE_PATH)
    except Exception as e:
        logger.warning("相关性矩阵缓存写入失败: %s", e)
