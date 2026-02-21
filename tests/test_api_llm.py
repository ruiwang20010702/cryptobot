"""OpenAI 兼容 API 后端测试"""

from unittest.mock import MagicMock, patch



# ─── _get_provider ─────────────────────────────────────────────────────


def test_get_provider_default():
    """默认 provider 为 claude"""
    from cryptobot.workflow.llm import _get_provider, reset_provider_cache

    reset_provider_cache()
    with patch("cryptobot.config.load_settings", return_value={}):
        result = _get_provider()
        assert result == "claude"
    reset_provider_cache()


def test_get_provider_api():
    """配置 api 后返回 api"""
    from cryptobot.workflow.llm import _get_provider, reset_provider_cache

    reset_provider_cache()
    settings = {"llm": {"provider": "api"}}
    with patch("cryptobot.config.load_settings", return_value=settings):
        result = _get_provider()
        assert result == "api"
    reset_provider_cache()


# ─── call_api 请求构建 ─────────────────────────────────────────────────


def test_call_api_builds_request():
    """验证 call_api 构建正确的 HTTP 请求"""
    from cryptobot.workflow.api_llm import call_api

    api_cfg = {
        "base_url": "https://api.example.com/v1",
        "api_key_env": "TEST_API_KEY",
        "models": {"haiku": "test-model"},
        "timeout": 30,
    }
    settings = {"llm": {"provider": "api", "api": api_cfg}}

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": '{"result": "ok"}'}}]
    }
    mock_response.raise_for_status = MagicMock()

    with (
        patch("cryptobot.workflow.api_llm.load_settings", return_value=settings),
        patch("cryptobot.workflow.api_llm.httpx.post", return_value=mock_response) as mock_post,
        patch.dict("os.environ", {"TEST_API_KEY": "sk-test-123"}),
    ):
        result = call_api("测试提示词", model="haiku", system_prompt="你是助手")

    assert result == {"result": "ok"}

    # 验证请求参数
    call_args = mock_post.call_args
    assert call_args[0][0] == "https://api.example.com/v1/chat/completions"
    body = call_args[1]["json"]
    assert body["model"] == "test-model"
    assert len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["content"] == "测试提示词"
    headers = call_args[1]["headers"]
    assert headers["Authorization"] == "Bearer sk-test-123"


def test_call_api_json_schema_in_prompt():
    """JSON schema 应注入到 system prompt 末尾"""
    from cryptobot.workflow.api_llm import call_api

    api_cfg = {
        "base_url": "https://api.example.com/v1",
        "api_key_env": "",
        "models": {"haiku": "test-model"},
        "timeout": 30,
    }
    settings = {"llm": {"provider": "api", "api": api_cfg}}

    schema = {"type": "object", "properties": {"action": {"type": "string"}}}

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": '{"action": "long"}'}}]
    }
    mock_response.raise_for_status = MagicMock()

    with (
        patch("cryptobot.workflow.api_llm.load_settings", return_value=settings),
        patch("cryptobot.workflow.api_llm.httpx.post", return_value=mock_response) as mock_post,
    ):
        result = call_api(
            "分析 BTC",
            model="haiku",
            system_prompt="你是交易员",
            json_schema=schema,
        )

    assert result == {"action": "long"}

    body = mock_post.call_args[1]["json"]
    # system prompt 应包含 schema
    sys_content = body["messages"][0]["content"]
    assert "JSON Schema" in sys_content
    assert '"action"' in sys_content
    # response_format 应设为 json_object
    assert body["response_format"] == {"type": "json_object"}


def test_call_api_retry_on_429():
    """429 应自动重试"""
    from cryptobot.workflow.api_llm import call_api

    api_cfg = {
        "base_url": "https://api.example.com/v1",
        "api_key_env": "",
        "models": {"haiku": "test-model"},
        "timeout": 30,
    }
    settings = {"llm": {"provider": "api", "api": api_cfg}}

    resp_429 = MagicMock()
    resp_429.status_code = 429

    resp_ok = MagicMock()
    resp_ok.status_code = 200
    resp_ok.json.return_value = {
        "choices": [{"message": {"content": '{"ok": true}'}}]
    }
    resp_ok.raise_for_status = MagicMock()

    with (
        patch("cryptobot.workflow.api_llm.load_settings", return_value=settings),
        patch("cryptobot.workflow.api_llm.httpx.post", side_effect=[resp_429, resp_ok]),
        patch("cryptobot.workflow.api_llm.time.sleep"),  # 跳过等待
    ):
        result = call_api("test", model="haiku", _retries=2)

    assert result == {"ok": True}


def test_usage_tracking():
    """token 用量应正确累计"""
    from cryptobot.workflow.api_llm import (
        call_api, get_usage_stats, reset_usage_stats,
    )

    reset_usage_stats()

    api_cfg = {
        "base_url": "https://api.example.com/v1",
        "api_key_env": "",
        "models": {"haiku": "deepseek-chat"},
        "timeout": 30,
    }
    settings = {"llm": {"provider": "api", "api": api_cfg}}

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": '{"ok": true}'}}],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "prompt_cache_hit_tokens": 30,
        },
    }
    mock_response.raise_for_status = MagicMock()

    with (
        patch("cryptobot.workflow.api_llm.load_settings", return_value=settings),
        patch("cryptobot.workflow.api_llm.httpx.post", return_value=mock_response),
    ):
        call_api("test", model="haiku")
        call_api("test2", model="haiku")

    stats = get_usage_stats()
    assert stats["total_calls"] == 2
    assert stats["total_prompt_tokens"] == 200
    assert stats["total_completion_tokens"] == 100
    assert stats["total_cached_tokens"] == 60
    assert stats["total_cost_yuan"] > 0
    assert "deepseek-chat" in stats["by_model"]
    assert stats["by_model"]["deepseek-chat"]["calls"] == 2

    reset_usage_stats()
    assert get_usage_stats()["total_calls"] == 0


def test_call_api_no_system_prompt():
    """无 system_prompt 时 messages 只有 user"""
    from cryptobot.workflow.api_llm import call_api

    api_cfg = {
        "base_url": "https://api.example.com/v1",
        "api_key_env": "",
        "models": {},
        "timeout": 30,
    }
    settings = {"llm": {"provider": "api", "api": api_cfg}}

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "hello"}}]
    }
    mock_response.raise_for_status = MagicMock()

    with (
        patch("cryptobot.workflow.api_llm.load_settings", return_value=settings),
        patch("cryptobot.workflow.api_llm.httpx.post", return_value=mock_response) as mock_post,
    ):
        result = call_api("hi", model="haiku")

    assert result == "hello"
    body = mock_post.call_args[1]["json"]
    # 无 system prompt → 只有 user message
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "user"


# ─── 路由集成测试 ──────────────────────────────────────────────────────


def test_call_claude_routes_to_api():
    """provider=api 时 call_claude 应路由到 call_api"""
    from cryptobot.workflow import llm as llm_mod
    from cryptobot.workflow.llm import call_claude, reset_provider_cache

    reset_provider_cache()
    # 直接设置 cache 绕过 load_settings
    llm_mod._provider_cache = "api"

    with patch("cryptobot.workflow.api_llm.call_api", return_value={"routed": True}) as mock_api:
        result = call_claude("test prompt", model="sonnet", system_prompt="sys")

    assert result == {"routed": True}
    mock_api.assert_called_once_with(
        "test prompt", model="sonnet", role=None, system_prompt="sys",
        json_schema=None, _retries=3,
    )
    reset_provider_cache()


def test_call_claude_default_uses_cli():
    """默认 provider=claude 时走原有 CLI 逻辑"""
    from cryptobot.workflow import llm as llm_mod
    from cryptobot.workflow.llm import call_claude, reset_provider_cache

    reset_provider_cache()
    # 直接设置 cache 绕过 load_settings
    llm_mod._provider_cache = "claude"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"type":"result","result":"cli response"}',
            stderr="",
        )
        result = call_claude("test")

    # 应走 subprocess 路径
    assert mock_run.called
    assert result == "cli response"
    reset_provider_cache()
