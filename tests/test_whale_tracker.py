"""巨鲸钱包追踪数据测试"""

from unittest.mock import patch, MagicMock

from cryptobot.data.whale_tracker import get_whale_activity, _empty_result


def _mock_whale_response(transactions):
    """模拟 Whale Alert API 响应"""
    return {"transactions": transactions}


@patch.dict("os.environ", {"WHALE_ALERT_API_KEY": "test_key"})
@patch("cryptobot.data.whale_tracker.get_cache", return_value=None)
@patch("cryptobot.data.whale_tracker.set_cache")
@patch("cryptobot.data.whale_tracker.httpx.get")
def test_selling_pressure(mock_get, mock_set_cache, mock_get_cache):
    """大量流入交易所 → selling_pressure"""
    resp = MagicMock()
    resp.json.return_value = _mock_whale_response([
        {
            "amount_usd": 5_000_000,
            "from": {"owner_type": "unknown"},
            "to": {"owner_type": "exchange"},
        },
        {
            "amount_usd": 3_000_000,
            "from": {"owner_type": "unknown"},
            "to": {"owner_type": "exchange"},
        },
        {
            "amount_usd": 1_000_000,
            "from": {"owner_type": "exchange"},
            "to": {"owner_type": "unknown"},
        },
    ])
    mock_get.return_value = resp

    result = get_whale_activity("BTCUSDT")

    assert result["symbol"] == "BTCUSDT"
    assert result["exchange_inflow_usd"] == 8_000_000
    assert result["exchange_outflow_usd"] == 1_000_000
    assert result["whale_signal"] == "selling_pressure"
    assert result["tx_count"] == 3
    mock_set_cache.assert_called_once()


@patch.dict("os.environ", {"WHALE_ALERT_API_KEY": "test_key"})
@patch("cryptobot.data.whale_tracker.get_cache", return_value=None)
@patch("cryptobot.data.whale_tracker.set_cache")
@patch("cryptobot.data.whale_tracker.httpx.get")
def test_accumulation(mock_get, mock_set_cache, mock_get_cache):
    """大量流出交易所 → accumulation"""
    resp = MagicMock()
    resp.json.return_value = _mock_whale_response([
        {
            "amount_usd": 10_000_000,
            "from": {"owner_type": "exchange"},
            "to": {"owner_type": "unknown"},
        },
        {
            "amount_usd": 2_000_000,
            "from": {"owner_type": "unknown"},
            "to": {"owner_type": "exchange"},
        },
    ])
    mock_get.return_value = resp

    result = get_whale_activity("ETHUSDT")

    assert result["whale_signal"] == "accumulation"
    assert result["exchange_outflow_usd"] == 10_000_000


@patch.dict("os.environ", {"WHALE_ALERT_API_KEY": "test_key"})
@patch("cryptobot.data.whale_tracker.get_cache", return_value=None)
@patch("cryptobot.data.whale_tracker.set_cache")
@patch("cryptobot.data.whale_tracker.httpx.get")
def test_neutral_balanced(mock_get, mock_set_cache, mock_get_cache):
    """流入流出均衡 → neutral"""
    resp = MagicMock()
    resp.json.return_value = _mock_whale_response([
        {
            "amount_usd": 5_000_000,
            "from": {"owner_type": "unknown"},
            "to": {"owner_type": "exchange"},
        },
        {
            "amount_usd": 5_000_000,
            "from": {"owner_type": "exchange"},
            "to": {"owner_type": "unknown"},
        },
    ])
    mock_get.return_value = resp

    result = get_whale_activity("BTCUSDT")

    assert result["whale_signal"] == "neutral"


def test_unsupported_symbol():
    """非 BTC/ETH 返回空结果"""
    result = get_whale_activity("SOLUSDT")
    assert result == _empty_result("SOLUSDT")


@patch.dict("os.environ", {"WHALE_ALERT_API_KEY": ""})
def test_no_api_key_returns_empty():
    """无 API key 时返回空结果"""
    result = get_whale_activity("BTCUSDT")
    assert result == _empty_result("BTCUSDT")


@patch.dict("os.environ", {"WHALE_ALERT_API_KEY": "test_key"})
@patch("cryptobot.data.whale_tracker.get_cache")
def test_cache_hit(mock_get_cache):
    """缓存命中"""
    cached = {"symbol": "BTCUSDT", "whale_signal": "neutral", "_cached_at": 999}
    mock_get_cache.return_value = cached
    result = get_whale_activity("BTCUSDT")
    assert result == cached


@patch.dict("os.environ", {"WHALE_ALERT_API_KEY": "test_key"})
@patch("cryptobot.data.whale_tracker.get_cache", return_value=None)
@patch("cryptobot.data.whale_tracker.httpx.get", side_effect=Exception("network error"))
def test_api_error_returns_empty(mock_get, mock_get_cache):
    """API 错误返回空结果"""
    result = get_whale_activity("BTCUSDT")
    assert result == _empty_result("BTCUSDT")


def test_empty_result_structure():
    result = _empty_result("BTCUSDT")
    assert result["symbol"] == "BTCUSDT"
    assert result["whale_signal"] == "neutral"
    assert result["tx_count"] == 0
