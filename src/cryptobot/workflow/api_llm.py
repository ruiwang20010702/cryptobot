"""OpenAI 兼容 API 后端

通用 LLM 调用封装，支持 DeepSeek / OpenAI / Groq / Ollama 等兼容 API。
通过 config/settings.yaml 中的 llm.api 段配置。
内置 token 用量和费用追踪。
"""

import json
import logging
import os
import threading
import time

import httpx

from cryptobot.config import DATA_OUTPUT_DIR, load_settings

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 5

# ─── Token 用量追踪（内存 + 文件持久化）────────────────────────────

_USAGE_FILE = DATA_OUTPUT_DIR / "llm_usage.json"

_usage_lock = threading.Lock()
_usage_stats = {
    "total_calls": 0,
    "total_prompt_tokens": 0,
    "total_completion_tokens": 0,
    "total_cached_tokens": 0,
    "total_cost_yuan": 0.0,
    "by_model": {},  # model_name → {calls, prompt_tokens, completion_tokens, cost}
}
_stats_loaded = False

# DeepSeek 定价 (元/百万 tokens) — 2026-02
_PRICING = {
    "deepseek-chat": {"input": 1.0, "input_cached": 0.1, "output": 2.0},
    "deepseek-reasoner": {"input": 1.0, "input_cached": 0.1, "output": 2.0},
}
# 默认定价 (未知模型)
_DEFAULT_PRICING = {"input": 2.0, "input_cached": 0.5, "output": 4.0}


def _load_stats_from_disk() -> None:
    """首次访问时从文件加载历史统计"""
    global _stats_loaded
    if _stats_loaded:
        return
    _stats_loaded = True
    if not _USAGE_FILE.exists():
        return
    try:
        saved = json.loads(_USAGE_FILE.read_text())
        _usage_stats["total_calls"] = saved.get("total_calls", 0)
        _usage_stats["total_prompt_tokens"] = saved.get("total_prompt_tokens", 0)
        _usage_stats["total_completion_tokens"] = saved.get("total_completion_tokens", 0)
        _usage_stats["total_cached_tokens"] = saved.get("total_cached_tokens", 0)
        _usage_stats["total_cost_yuan"] = saved.get("total_cost_yuan", 0.0)
        _usage_stats["by_model"] = saved.get("by_model", {})
    except Exception:
        pass


def _save_stats_to_disk() -> None:
    """将统计写入文件"""
    try:
        _USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _USAGE_FILE.write_text(
            json.dumps(_usage_stats, indent=2, ensure_ascii=False)
        )
    except Exception:
        pass


def _track_usage(model: str, usage: dict) -> None:
    """记录单次调用的 token 用量和费用（内存 + 文件持久化）"""
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)

    # DeepSeek 返回 prompt_cache_hit_tokens
    cached_tokens = usage.get("prompt_cache_hit_tokens", 0)
    uncached_tokens = prompt_tokens - cached_tokens

    pricing = _PRICING.get(model, _DEFAULT_PRICING)
    cost = (
        uncached_tokens * pricing["input"] / 1_000_000
        + cached_tokens * pricing["input_cached"] / 1_000_000
        + completion_tokens * pricing["output"] / 1_000_000
    )

    with _usage_lock:
        _load_stats_from_disk()

        _usage_stats["total_calls"] += 1
        _usage_stats["total_prompt_tokens"] += prompt_tokens
        _usage_stats["total_completion_tokens"] += completion_tokens
        _usage_stats["total_cached_tokens"] += cached_tokens
        _usage_stats["total_cost_yuan"] += cost

        if model not in _usage_stats["by_model"]:
            _usage_stats["by_model"][model] = {
                "calls": 0, "prompt_tokens": 0,
                "completion_tokens": 0, "cost_yuan": 0.0,
            }
        m = _usage_stats["by_model"][model]
        m["calls"] += 1
        m["prompt_tokens"] += prompt_tokens
        m["completion_tokens"] += completion_tokens
        m["cost_yuan"] += cost

        _save_stats_to_disk()

    logger.info(
        "API 用量: model=%s, prompt=%d (cached=%d), completion=%d, cost=¥%.4f",
        model, prompt_tokens, cached_tokens, completion_tokens, cost,
    )


def get_usage_stats() -> dict:
    """获取累计 token 用量统计（从文件加载，线程安全副本）"""
    with _usage_lock:
        _load_stats_from_disk()
        stats = {
            **_usage_stats,
            "by_model": {k: {**v} for k, v in _usage_stats["by_model"].items()},
            "total_cost_yuan": round(_usage_stats["total_cost_yuan"], 4),
        }
    return stats


def reset_usage_stats() -> None:
    """重置用量统计（内存 + 文件）"""
    with _usage_lock:
        _usage_stats["total_calls"] = 0
        _usage_stats["total_prompt_tokens"] = 0
        _usage_stats["total_completion_tokens"] = 0
        _usage_stats["total_cached_tokens"] = 0
        _usage_stats["total_cost_yuan"] = 0.0
        _usage_stats["by_model"].clear()
        _save_stats_to_disk()


def _load_api_config() -> dict:
    """从 settings.yaml 读取 llm.api 配置"""
    settings = load_settings()
    api_cfg = settings.get("llm", {}).get("api", {})
    if not api_cfg.get("base_url"):
        raise RuntimeError("llm.api.base_url 未配置，请检查 config/settings.yaml")
    return api_cfg


def _resolve_model(logical_name: str, api_cfg: dict, role: str | None = None) -> str:
    """将逻辑名 (haiku/sonnet) 映射到实际模型名

    优先级: role_models.{role} → models.{logical_name} → logical_name
    """
    if role:
        role_models = api_cfg.get("role_models", {})
        if role in role_models:
            return role_models[role]
    models = api_cfg.get("models", {})
    return models.get(logical_name, logical_name)


def call_api(
    prompt: str,
    *,
    model: str = "haiku",
    role: str | None = None,
    system_prompt: str | None = None,
    json_schema: dict | None = None,
    _retries: int = MAX_RETRIES,
) -> dict | str:
    """调用 OpenAI 兼容 API，返回解析后的 JSON 或原始文本。

    Args:
        prompt: 用户提示词
        model: 逻辑模型名 (haiku / sonnet)，映射到实际模型
        role: AI 角色名，用于角色级模型选择 (优先于 model 映射)
        system_prompt: 系统提示词
        json_schema: JSON Schema 约束输出格式
        _retries: 剩余重试次数 (内部使用)

    Returns:
        解析后的 dict（如果输出是 JSON）或原始字符串
    """
    api_cfg = _load_api_config()
    base_url = api_cfg["base_url"].rstrip("/")
    api_key_env = api_cfg.get("api_key_env", "")
    api_key = os.environ.get(api_key_env, "") if api_key_env else ""
    actual_model = _resolve_model(model, api_cfg, role=role)
    timeout = api_cfg.get("timeout", 60)

    # 构建 messages
    messages = []
    sys_text = system_prompt or ""
    if json_schema:
        schema_str = json.dumps(json_schema, ensure_ascii=False, indent=2)
        sys_text += f"\n\n请严格按以下 JSON Schema 输出:\n{schema_str}"
    if sys_text:
        messages.append({"role": "system", "content": sys_text})
    messages.append({"role": "user", "content": prompt})

    # 构建请求体
    body: dict = {
        "model": actual_model,
        "messages": messages,
    }
    if json_schema:
        body["response_format"] = {"type": "json_object"}

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    logger.info(
        "调用 API: model=%s (%s), prompt_len=%d, url=%s",
        model, actual_model, len(prompt), base_url,
    )

    try:
        resp = httpx.post(
            f"{base_url}/chat/completions",
            json=body,
            headers=headers,
            timeout=timeout,
        )
    except httpx.TimeoutException:
        if _retries > 0:
            delay = RETRY_BASE_DELAY * (MAX_RETRIES - _retries + 1)
            logger.warning("API 超时, %d秒后重试 (剩余%d次)", delay, _retries)
            time.sleep(delay)
            return call_api(
                prompt, model=model, role=role, system_prompt=system_prompt,
                json_schema=json_schema, _retries=_retries - 1,
            )
        raise

    # 可重试的状态码
    if resp.status_code in (429, 500, 502, 503) and _retries > 0:
        delay = RETRY_BASE_DELAY * (MAX_RETRIES - _retries + 1)
        logger.warning(
            "API 返回 %d, %d秒后重试 (剩余%d次)",
            resp.status_code, delay, _retries,
        )
        time.sleep(delay)
        return call_api(
            prompt, model=model, system_prompt=system_prompt,
            json_schema=json_schema, _retries=_retries - 1,
        )

    resp.raise_for_status()

    data = resp.json()

    # 记录 token 用量
    usage = data.get("usage")
    if usage:
        _track_usage(actual_model, usage)

    content = data["choices"][0]["message"]["content"]

    # 尝试解析 JSON
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        pass

    # 尝试从 markdown 代码块提取 JSON
    from cryptobot.workflow.llm import _extract_json
    extracted = _extract_json(content)
    if extracted is not None:
        return extracted

    return content
