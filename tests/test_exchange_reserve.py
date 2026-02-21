"""持仓量趋势数据测试"""

from unittest.mock import patch, MagicMock

from cryptobot.data.exchange_reserve import get_open_interest_trend, _empty_result


@patch("cryptobot.data.exchange_reserve.get_cache", return_value=None)
@patch("cryptobot.data.exchange_reserve.set_cache")
@patch("cryptobot.data.exchange_reserve.httpx.get")
@patch.dict("os.environ", {"COINGLASS_API_KEY": "test-key"})
def test_oi_trend_increasing(mock_get, mock_set_cache, mock_get_cache):
    """测试 OI 上升场景"""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "code": "0",
        "data": [
            {"openInterest": 100_000},
            {"openInterest": 102_000},
            {"openInterest": 108_000},
        ],
    }
    mock_get.return_value = resp

    result = get_open_interest_trend("BTCUSDT")

    assert result["symbol"] == "BTCUSDT"
    assert result["open_interest"] == 108_000
    assert result["oi_change_7d_pct"] == 8.0
    assert result["oi_trend"] == "increasing"
    mock_set_cache.assert_called_once()


@patch("cryptobot.data.exchange_reserve.get_cache", return_value=None)
@patch("cryptobot.data.exchange_reserve.set_cache")
@patch("cryptobot.data.exchange_reserve.httpx.get")
@patch.dict("os.environ", {"COINGLASS_API_KEY": "test-key"})
def test_oi_trend_decreasing(mock_get, mock_set_cache, mock_get_cache):
    """测试 OI 下降场景"""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "code": "0",
        "data": [
            {"openInterest": 100_000},
            {"openInterest": 93_000},
        ],
    }
    mock_get.return_value = resp

    result = get_open_interest_trend("ETHUSDT")
    assert result["oi_trend"] == "decreasing"
    assert result["oi_change_7d_pct"] == -7.0


@patch("cryptobot.data.exchange_reserve.get_cache", return_value=None)
@patch("cryptobot.data.exchange_reserve.set_cache")
@patch("cryptobot.data.exchange_reserve.httpx.get")
@patch.dict("os.environ", {"COINGLASS_API_KEY": "test-key"})
def test_oi_trend_stable(mock_get, mock_set_cache, mock_get_cache):
    """测试 OI 稳定场景"""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "code": "0",
        "data": [
            {"openInterest": 100_000},
            {"openInterest": 101_000},
        ],
    }
    mock_get.return_value = resp

    result = get_open_interest_trend("BTCUSDT")
    assert result["oi_trend"] == "stable"


def test_unsupported_symbol():
    """测试不支持的币种返回空结果"""
    result = get_open_interest_trend("DOGEUSDT")
    assert result == _empty_result("DOGEUSDT")
    assert result["oi_trend"] == "unknown"


@patch("cryptobot.data.exchange_reserve.get_cache")
def test_cached_result(mock_get_cache):
    """测试缓存命中"""
    cached = {"symbol": "BTCUSDT", "oi_trend": "stable", "_cached_at": 999}
    mock_get_cache.return_value = cached

    result = get_open_interest_trend("BTCUSDT")
    assert result == cached


@patch("cryptobot.data.exchange_reserve.get_cache", return_value=None)
@patch.dict("os.environ", {}, clear=False)
def test_no_api_key(mock_get_cache, monkeypatch):
    """测试无 API key 返回空结果"""
    monkeypatch.delenv("COINGLASS_API_KEY", raising=False)
    result = get_open_interest_trend("BTCUSDT")
    assert result["oi_trend"] == "unknown"


@patch("cryptobot.data.exchange_reserve.get_cache", return_value=None)
@patch("cryptobot.data.exchange_reserve.httpx.get", side_effect=Exception("timeout"))
@patch.dict("os.environ", {"COINGLASS_API_KEY": "test-key"})
def test_api_error(mock_get, mock_get_cache):
    """测试 API 错误返回空结果"""
    result = get_open_interest_trend("BTCUSDT")
    assert result == _empty_result("BTCUSDT")
