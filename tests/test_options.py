"""期权市场数据测试"""

from unittest.mock import patch, MagicMock

from cryptobot.data.options import get_options_sentiment, _empty_result


def _mock_deribit_response_bearish():
    """模拟看跌: put_oi 远大于 call_oi"""
    return {
        "result": [
            {"instrument_name": "BTC-28MAR26-100000-C", "open_interest": 1000, "volume": 50},
            {"instrument_name": "BTC-28MAR26-90000-C", "open_interest": 800, "volume": 30},
            {"instrument_name": "BTC-28MAR26-80000-P", "open_interest": 2500, "volume": 100},
            {"instrument_name": "BTC-28MAR26-70000-P", "open_interest": 1500, "volume": 80},
        ]
    }


def _mock_deribit_response_bullish():
    """模拟看涨: call_oi 远大于 put_oi"""
    return {
        "result": [
            {"instrument_name": "ETH-28MAR26-5000-C", "open_interest": 5000, "volume": 200},
            {"instrument_name": "ETH-28MAR26-4000-C", "open_interest": 3000, "volume": 150},
            {"instrument_name": "ETH-28MAR26-3000-P", "open_interest": 1000, "volume": 50},
        ]
    }


@patch("cryptobot.data.options.get_cache", return_value=None)
@patch("cryptobot.data.options.set_cache")
@patch("cryptobot.data.options.httpx.get")
def test_btc_bearish(mock_get, mock_set_cache, mock_get_cache):
    """BTC 看跌信号: put_oi > call_oi * 1.2"""
    resp = MagicMock()
    resp.json.return_value = _mock_deribit_response_bearish()
    mock_get.return_value = resp

    result = get_options_sentiment("BTCUSDT")

    assert result["symbol"] == "BTC"
    assert result["put_oi"] == 4000.0  # 2500 + 1500
    assert result["call_oi"] == 1800.0  # 1000 + 800
    assert result["put_call_ratio"] > 1.2
    assert result["put_call_signal"] == "bearish"
    assert result["data_source"] == "deribit"
    mock_set_cache.assert_called_once()


@patch("cryptobot.data.options.get_cache", return_value=None)
@patch("cryptobot.data.options.set_cache")
@patch("cryptobot.data.options.httpx.get")
def test_eth_bullish(mock_get, mock_set_cache, mock_get_cache):
    """ETH 看涨信号: put_call_ratio < 0.7"""
    resp = MagicMock()
    resp.json.return_value = _mock_deribit_response_bullish()
    mock_get.return_value = resp

    result = get_options_sentiment("ETHUSDT")

    assert result["symbol"] == "ETH"
    assert result["call_oi"] == 8000.0  # 5000 + 3000
    assert result["put_oi"] == 1000.0
    assert result["put_call_ratio"] < 0.7
    assert result["put_call_signal"] == "bullish"


def test_unsupported_symbol():
    """不支持的币种返回空结果"""
    result = get_options_sentiment("SOLUSDT")
    assert result["symbol"] == "SOL"
    assert result["put_call_signal"] == "neutral"
    assert result["put_oi"] == 0.0


@patch("cryptobot.data.options.get_cache")
def test_cache_hit(mock_get_cache):
    """缓存命中"""
    cached = {"symbol": "BTC", "put_call_signal": "bearish", "_cached_at": 999}
    mock_get_cache.return_value = cached
    result = get_options_sentiment("BTCUSDT")
    assert result == cached


@patch("cryptobot.data.options.get_cache", return_value=None)
@patch("cryptobot.data.options.httpx.get", side_effect=Exception("timeout"))
def test_api_error(mock_get, mock_get_cache):
    """API 错误返回空结果"""
    result = get_options_sentiment("BTCUSDT")
    assert result["symbol"] == "BTC"
    assert result["put_call_signal"] == "neutral"
    assert result["put_oi"] == 0.0


@patch("cryptobot.data.options.get_cache", return_value=None)
@patch("cryptobot.data.options.set_cache")
@patch("cryptobot.data.options.httpx.get")
def test_empty_result_from_api(mock_get, mock_set_cache, mock_get_cache):
    """API 返回空结果"""
    resp = MagicMock()
    resp.json.return_value = {"result": []}
    mock_get.return_value = resp

    result = get_options_sentiment("BTCUSDT")
    assert result["put_call_signal"] == "neutral"
    mock_set_cache.assert_called_once()


def test_empty_result_structure():
    result = _empty_result("BTC")
    assert result["symbol"] == "BTC"
    assert result["put_call_signal"] == "neutral"
    assert result["data_source"] == "deribit"
