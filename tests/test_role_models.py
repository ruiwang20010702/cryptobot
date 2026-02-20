"""角色级模型选择测试"""

from unittest.mock import patch

from cryptobot.workflow.api_llm import _resolve_model


def test_resolve_model_default():
    """测试默认模型映射 (无 role)"""
    api_cfg = {"models": {"haiku": "deepseek-chat", "sonnet": "deepseek-reasoner"}}
    assert _resolve_model("haiku", api_cfg) == "deepseek-chat"
    assert _resolve_model("sonnet", api_cfg) == "deepseek-reasoner"


def test_resolve_model_with_role():
    """测试角色级模型覆盖"""
    api_cfg = {
        "models": {"haiku": "deepseek-chat", "sonnet": "deepseek-chat"},
        "role_models": {"trader": "deepseek-reasoner", "risk_manager": "deepseek-reasoner"},
    }
    # role 匹配时使用 role_models
    assert _resolve_model("sonnet", api_cfg, role="trader") == "deepseek-reasoner"
    assert _resolve_model("sonnet", api_cfg, role="risk_manager") == "deepseek-reasoner"

    # role 不匹配时 fallback 到 models
    assert _resolve_model("haiku", api_cfg, role="technical") == "deepseek-chat"


def test_resolve_model_role_none():
    """测试 role=None 不影响现有行为"""
    api_cfg = {
        "models": {"haiku": "gpt-4o-mini"},
        "role_models": {"trader": "gpt-4o"},
    }
    assert _resolve_model("haiku", api_cfg, role=None) == "gpt-4o-mini"


def test_resolve_model_no_role_models_config():
    """测试未配置 role_models 时完全 fallback"""
    api_cfg = {"models": {"haiku": "deepseek-chat"}}
    assert _resolve_model("haiku", api_cfg, role="trader") == "deepseek-chat"


def test_resolve_model_unknown_logical_name():
    """测试未知逻辑名直接返回"""
    api_cfg = {"models": {"haiku": "deepseek-chat"}}
    assert _resolve_model("unknown-model", api_cfg) == "unknown-model"


@patch("cryptobot.workflow.llm._get_provider", return_value="api")
@patch("cryptobot.workflow.api_llm.call_api")
def test_call_claude_passes_role(mock_call_api, mock_provider):
    """测试 call_claude 将 role 参数传递到 call_api"""
    from cryptobot.workflow.llm import call_claude

    mock_call_api.return_value = {"result": "ok"}

    call_claude("test prompt", model="sonnet", role="trader")

    mock_call_api.assert_called_once()
    _, kwargs = mock_call_api.call_args
    assert kwargs["role"] == "trader"
    assert kwargs["model"] == "sonnet"


@patch("cryptobot.workflow.llm._get_provider", return_value="api")
@patch("cryptobot.workflow.api_llm.call_api")
def test_call_claude_role_none_default(mock_call_api, mock_provider):
    """测试 call_claude 默认 role=None"""
    from cryptobot.workflow.llm import call_claude

    mock_call_api.return_value = "ok"

    call_claude("test prompt")

    _, kwargs = mock_call_api.call_args
    assert kwargs["role"] is None
