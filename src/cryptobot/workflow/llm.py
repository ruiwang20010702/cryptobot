"""Claude CLI subprocess wrapper

通过 `claude -p` 调用 Claude Code 订阅额度，不需要 Anthropic API key。
支持同步调用和并行调用，内置速率限制和重试机制。

速率限制参考:
  - Max 订阅: ~225(5x)/~900(20x) 条消息 per 5h 窗口
  - 默认 5 并发, 工作流约 40 次调用, 占 Max 5x 额度 ~18%
"""

import json
import logging
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# 默认预算上限 (USD) — 订阅用户仅作安全上限，不实际扣费
BUDGET_DEFAULT = 5.0

# 并发与速率控制
# Max 订阅: 10 并发 (5h 窗口 225~900 条, 工作流约 40 条)
# Pro 订阅: 建议改为 3
MAX_CONCURRENT = 10     # 最大并行 claude 进程数
MAX_RETRIES = 3         # 最大重试次数
RETRY_BASE_DELAY = 10   # 重试基础延迟 (秒)


_provider_cache: str | None = None


def _get_provider() -> str:
    """读取 settings.yaml 中的 llm.provider，默认 'claude'"""
    global _provider_cache
    if _provider_cache is None:
        from cryptobot.config import load_settings
        _provider_cache = load_settings().get("llm", {}).get("provider", "claude")
    return _provider_cache


def reset_provider_cache() -> None:
    """重置 provider 缓存，用于测试或动态切换"""
    global _provider_cache
    _provider_cache = None


def call_claude(
    prompt: str,
    *,
    model: str = "haiku",
    role: str | None = None,
    system_prompt: str | None = None,
    json_schema: dict | None = None,
    max_budget: float | None = None,
    _retries: int = MAX_RETRIES,
) -> dict | str:
    """调用 LLM，根据配置路由到 Claude CLI 或 OpenAI 兼容 API。

    内置重试机制: 遇到速率限制或临时错误时自动指数退避重试。

    Args:
        prompt: 用户提示词
        model: 模型名 (haiku / sonnet)
        role: AI 角色名 (technical/trader/risk_manager 等)，用于角色级模型选择
        system_prompt: 系统提示词
        json_schema: JSON Schema 约束输出格式
        max_budget: 单次预算上限 (USD)，仅 Claude CLI 模式有效
        _retries: 剩余重试次数 (内部使用)

    Returns:
        解析后的 dict（如果输出是 JSON）或原始字符串
    """
    # 路由: 如果配置了 API provider，走 api_llm
    if _get_provider() == "api":
        from cryptobot.workflow.api_llm import call_api
        return call_api(
            prompt, model=model, role=role, system_prompt=system_prompt,
            json_schema=json_schema, _retries=_retries,
        )

    if max_budget is None:
        max_budget = BUDGET_DEFAULT

    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--model", model,
        "--no-session-persistence",
        "--max-budget-usd", str(max_budget),
    ]

    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])

    if json_schema:
        cmd.extend(["--json-schema", json.dumps(json_schema)])

    # prompt 通过 stdin 传入，避免命令行长度限制
    cmd.append("-")

    # 清除 CLAUDECODE 环境变量，允许在 Claude Code 会话内嵌套调用
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    logger.info("调用 Claude CLI: model=%s, prompt_len=%d", model, len(prompt))

    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
    except subprocess.TimeoutExpired:
        if _retries > 0:
            delay = RETRY_BASE_DELAY * (MAX_RETRIES - _retries + 1)
            logger.warning("Claude CLI 超时, %d秒后重试 (剩余%d次)", delay, _retries)
            time.sleep(delay)
            return call_claude(
                prompt, model=model, role=role, system_prompt=system_prompt,
                json_schema=json_schema, max_budget=max_budget, _retries=_retries - 1,
            )
        raise

    if result.returncode != 0:
        stderr = result.stderr or ""
        # 速率限制或过载 → 重试
        is_retryable = any(kw in stderr.lower() for kw in [
            "rate limit", "overloaded", "429", "503", "capacity", "throttl",
        ])
        if is_retryable and _retries > 0:
            delay = RETRY_BASE_DELAY * (MAX_RETRIES - _retries + 1)
            logger.warning("Claude CLI 限流 (code=%d), %d秒后重试 (剩余%d次)",
                           result.returncode, delay, _retries)
            time.sleep(delay)
            return call_claude(
                prompt, model=model, role=role, system_prompt=system_prompt,
                json_schema=json_schema, max_budget=max_budget, _retries=_retries - 1,
            )
        logger.error("Claude CLI 失败: returncode=%d, stderr=%s", result.returncode, stderr)
        raise RuntimeError(f"Claude CLI 调用失败 (code={result.returncode}): {stderr[:500]}")

    raw = result.stdout.strip()

    # claude --output-format json 返回 {"type":"result","result":"..."}
    try:
        wrapper = json.loads(raw)
    except json.JSONDecodeError:
        return raw

    # 检测预算超限等错误 — CLI 返回 exit 0 但 subtype 是 error
    subtype = wrapper.get("subtype", "")
    if subtype.startswith("error"):
        logger.error("Claude CLI 返回错误: subtype=%s, cost=$%.4f",
                      subtype, wrapper.get("total_cost_usd", 0))
        raise RuntimeError(f"Claude CLI 错误: {subtype}")

    # --json-schema 的结构化输出在 structured_output 字段
    structured = wrapper.get("structured_output")
    if structured is not None:
        return structured

    # 普通文本结果在 result 字段
    content = wrapper.get("result", raw)

    # 尝试解析内层 JSON
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        # 从 markdown 代码块提取 JSON (```json ... ```)
        extracted = _extract_json(content)
        if extracted is not None:
            return extracted
        return content
    return content


def _extract_json(text: str) -> dict | list | None:
    """从文本中提取 JSON，支持 markdown 代码块和裸 JSON"""
    # 1. 尝试 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    # 2. 用 raw_decode 逐位查找第一个有效 JSON 对象
    decoder = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(text, idx)
            return obj
        except json.JSONDecodeError:
            idx = text.find("{", idx + 1)
    return None


def call_claude_parallel(
    tasks: list[dict],
    *,
    max_workers: int | None = None,
) -> list[dict | str]:
    """并行调用多个 Claude 进程。

    并发数受 MAX_CONCURRENT 全局限制（默认 2），避免触发速率限制。

    Args:
        tasks: 每个元素是 call_claude 的关键字参数 dict，
               必须包含 "prompt"，可选 "model", "system_prompt", "json_schema", "max_budget"
        max_workers: 最大并行数，默认 MAX_CONCURRENT (2)

    Returns:
        与 tasks 顺序一致的结果列表
    """
    if max_workers is None:
        max_workers = MAX_CONCURRENT
    # 硬上限: 不超过全局并发限制
    max_workers = min(max_workers, MAX_CONCURRENT)

    logger.info("并行调用 %d 个任务, max_workers=%d", len(tasks), max_workers)

    results = [None] * len(tasks)

    def _run(index: int, kwargs: dict):
        return index, call_claude(**kwargs)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_run, i, task): i
            for i, task in enumerate(tasks)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                _, result = future.result()
                results[idx] = result
            except Exception as e:
                logger.error("并行调用 #%d 失败: %s", idx, e)
                results[idx] = {"error": str(e)}

    return results
