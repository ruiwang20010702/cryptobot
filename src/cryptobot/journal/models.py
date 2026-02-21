"""信号记录数据模型

记录信号完整生命周期: pending → active → closed / expired
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from uuid import uuid4


@dataclass
class SignalRecord:
    """信号全生命周期记录"""

    # 身份
    signal_id: str = field(default_factory=lambda: uuid4().hex[:12])
    symbol: str = ""
    action: str = ""  # long / short

    # 信号生成时的参数
    timestamp: str = ""          # 信号生成时间
    confidence: int = 0
    entry_price_range: list = field(default_factory=list)
    stop_loss: float | None = None
    take_profit: list = field(default_factory=list)
    leverage: int = 1
    position_size_usdt: float | None = None
    reasoning: str = ""
    risk_score: int | None = None

    # 执行结果 (交易结束后填充)
    actual_entry_price: float | None = None
    actual_exit_price: float | None = None
    actual_pnl_pct: float | None = None
    actual_pnl_usdt: float | None = None
    exit_reason: str | None = None   # tp_hit / sl_hit / manual / expired
    duration_hours: float | None = None

    # AI 分析元数据
    analyst_votes: dict | None = None    # {"technical": "bullish", "onchain": "bearish", ...}
    prompt_version: str | None = None    # "v1.0"
    model_id: str | None = None          # 竞赛模式下的模型 ID

    # 状态
    status: str = "pending"  # pending / active / closed / expired

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SignalRecord":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    @classmethod
    def from_signal(cls, signal: dict) -> "SignalRecord":
        """从 execute 节点的信号 dict 创建记录"""
        summary = signal.get("analysis_summary", {})
        return cls(
            symbol=signal.get("symbol", ""),
            action=signal.get("action", ""),
            timestamp=signal.get("timestamp", datetime.now(timezone.utc).isoformat()),
            confidence=signal.get("confidence", 0),
            entry_price_range=signal.get("entry_price_range", []),
            stop_loss=signal.get("stop_loss"),
            take_profit=signal.get("take_profit", []),
            leverage=signal.get("leverage", 1),
            position_size_usdt=signal.get("position_size_usdt"),
            reasoning=summary.get("reasoning", ""),
            risk_score=summary.get("risk_score"),
            analyst_votes=signal.get("analyst_votes"),
            prompt_version=signal.get("prompt_version"),
            status="pending",
        )
