"""多模型竞赛

交易决策阶段并行调用多模型，按策略选择最终结果。
持久化: data/output/evolution/competition.json
"""

import json
import logging
from datetime import datetime, timezone

from cryptobot.config import DATA_OUTPUT_DIR, load_settings

logger = logging.getLogger(__name__)

_COMP_DIR = DATA_OUTPUT_DIR / "evolution"
_COMP_FILE = _COMP_DIR / "competition.json"


def get_competition_config() -> dict | None:
    """读取竞赛配置，未启用返回 None"""
    settings = load_settings()
    comp = settings.get("llm", {}).get("competition", {})
    if not comp.get("enabled", False):
        return None
    models = comp.get("models", [])
    if len(models) < 2:
        return None
    return {
        "models": models,
        "strategy": comp.get("strategy", "consensus"),
        "min_records": comp.get("min_records", 20),
    }


def run_competition(tasks: list[dict], models: list[dict]) -> list[list[dict]]:
    """每个 task 用多个模型并行调用

    Args:
        tasks: call_claude 格式的任务列表
        models: [{"id": "deepseek-chat", "label": "DeepSeek Chat"}, ...]

    Returns:
        results[task_idx] = [{"model_id", "model_label", "result"}, ...]
    """
    # 展开: 每个 task × 每个 model = N*M 个调用
    flat_tasks = []
    flat_index = []  # (task_idx, model_info)

    for ti, task in enumerate(tasks):
        for model_info in models:
            # 复制 task 并覆盖 model 相关参数
            t = {**task}
            # 竞赛模式下: 用 role_override 覆盖模型
            t["_competition_model"] = model_info["id"]
            flat_tasks.append(t)
            flat_index.append((ti, model_info))

    # 并行调用 — 通过临时覆盖环境调用不同模型
    flat_results = _call_multi_model(flat_tasks)

    # 重组结果
    results: list[list[dict]] = [[] for _ in tasks]
    for fi, raw_result in enumerate(flat_results):
        ti, model_info = flat_index[fi]
        results[ti].append({
            "model_id": model_info["id"],
            "model_label": model_info.get("label", model_info["id"]),
            "result": raw_result,
        })

    return results


def _call_multi_model(flat_tasks: list[dict]) -> list:
    """调用多模型 — 临时修改 role_models 使每个 task 用指定模型"""
    from cryptobot.workflow.api_llm import call_api
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = [None] * len(flat_tasks)

    def _run(idx: int, task: dict):
        model_id = task.get("_competition_model")
        # 直接用 model_id 作为 actual model，绕过 role_models 映射
        return idx, call_api(
            task["prompt"],
            model=model_id or task.get("model", "sonnet"),
            system_prompt=task.get("system_prompt"),
            json_schema=task.get("json_schema"),
        )

    max_workers = min(len(flat_tasks), 10)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_run, i, t): i for i, t in enumerate(flat_tasks)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                _, result = future.result()
                results[idx] = result
            except Exception as e:
                logger.error("竞赛调用 #%d 失败: %s", idx, e)
                results[idx] = {"error": str(e)}

    return results


def select_winner(
    multi_results: list[dict],
    strategy: str,
    symbol: str,
) -> dict:
    """从多模型结果中选择最终结果

    Args:
        multi_results: [{"model_id", "result"}, ...]
        strategy: "consensus" 或 "best_performer"
        symbol: 交易对

    Returns:
        被选中的 result (dict) + model_id
    """
    valid = [
        r for r in multi_results
        if isinstance(r.get("result"), dict) and "error" not in r["result"]
    ]

    if not valid:
        return {"model_id": "none", "result": {"action": "no_trade",
                "confidence": 0, "reasoning": "所有模型调用失败"}}

    if strategy == "consensus":
        return _consensus_select(valid)
    elif strategy == "best_performer":
        return _best_performer_select(valid, symbol)
    else:
        return valid[0]


def _consensus_select(valid: list[dict]) -> dict:
    """共识策略: 多数模型同意的方向才执行"""
    actions = {}
    for r in valid:
        action = r["result"].get("action", "no_trade")
        actions[action] = actions.get(action, 0) + 1

    majority_action = max(actions, key=actions.get)
    majority_count = actions[majority_action]

    # 需要超半数同意 (> N/2)
    if majority_count <= len(valid) / 2 and majority_action != "no_trade":
        # 2 模型分歧: 选置信度更高的
        if len(valid) == 2:
            winner = max(valid, key=lambda r: r["result"].get("confidence", 0))
            return {"model_id": winner["model_id"], "result": winner["result"]}
        # 3+ 模型无共识 → no_trade
        best = max(valid, key=lambda r: r["result"].get("confidence", 0))
        return {
            "model_id": best["model_id"],
            "result": {
                **best["result"],
                "action": "no_trade",
                "reasoning": f"模型无共识 ({actions}), 原始: {best['result'].get('reasoning', '')}",
            },
        }

    # 从多数方向中选置信度最高的
    majority_results = [
        r for r in valid if r["result"].get("action") == majority_action
    ]
    winner = max(majority_results, key=lambda r: r["result"].get("confidence", 0))
    return {"model_id": winner["model_id"], "result": winner["result"]}


def _best_performer_select(valid: list[dict], symbol: str) -> dict:
    """最佳表现者策略: 按历史胜率选模型"""
    stats = get_model_stats()

    best_model = None
    best_wr = -1
    for r in valid:
        model_stat = stats.get(r["model_id"], {})
        wr = model_stat.get("win_rate", 0.45)
        if wr > best_wr:
            best_wr = wr
            best_model = r

    return best_model or valid[0]


def record_competition_result(
    symbol: str, model_id: str, action: str, signal_id: str,
) -> None:
    """记录竞赛结果"""
    data = _load_competition()
    data.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "model_id": model_id,
        "action": action,
        "signal_id": signal_id,
    })
    # 只保留最近 500 条
    if len(data) > 500:
        data = data[-500:]
    _save_competition(data)


def get_model_stats() -> dict:
    """计算各模型的历史胜率

    Returns:
        {model_id: {"total", "wins", "win_rate"}}
    """
    comp_records = _load_competition()
    if not comp_records:
        return {}

    from cryptobot.journal.storage import get_all_records
    journal = {r.signal_id: r for r in get_all_records() if r.status == "closed"}

    stats: dict[str, dict] = {}
    for cr in comp_records:
        mid = cr["model_id"]
        if mid not in stats:
            stats[mid] = {"total": 0, "wins": 0}

        sid = cr.get("signal_id", "")
        jr = journal.get(sid)
        if jr:
            stats[mid]["total"] += 1
            if (jr.actual_pnl_pct or 0) > 0:
                stats[mid]["wins"] += 1

    result = {}
    for mid, s in stats.items():
        result[mid] = {
            "total": s["total"],
            "wins": s["wins"],
            "win_rate": round(s["wins"] / s["total"], 3) if s["total"] > 0 else 0,
        }
    return result


def _load_competition() -> list:
    if not _COMP_FILE.exists():
        return []
    try:
        data = json.loads(_COMP_FILE.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_competition(data: list) -> None:
    _COMP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _COMP_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.rename(_COMP_FILE)
