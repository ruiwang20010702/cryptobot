"""DXY 美元指数数据测试"""

from unittest.mock import patch, MagicMock

from cryptobot.data.dxy import get_dxy_trend, _empty_result


def _mock_yahoo_response(closes):
    """模拟 Yahoo Finance API 响应"""
    return {
        "chart": {
            "result": [{
                "indicators": {
                    "quote": [{"close": closes}]
                }
            }]
        }
    }


@patch("cryptobot.data.dxy.get_cache", return_value=None)
@patch("cryptobot.data.dxy.set_cache")
@patch("cryptobot.data.dxy.httpx.get")
def test_dxy_strengthening_bearish(mock_get, mock_set_cache, mock_get_cache):
    """DXY 上涨 > 0.5% → strengthening + bearish"""
    resp = MagicMock()
    resp.json.return_value = _mock_yahoo_response(
        [103.0, 103.2, 103.5, 103.8, 104.0, 104.2, 104.8]
    )
    mock_get.return_value = resp

    result = get_dxy_trend()

    assert result["current_value"] == 104.8
    assert result["change_1d_pct"] > 0.5
    assert result["trend"] == "strengthening"
    assert result["signal"] == "bearish"
    mock_set_cache.assert_called_once()


@patch("cryptobot.data.dxy.get_cache", return_value=None)
@patch("cryptobot.data.dxy.set_cache")
@patch("cryptobot.data.dxy.httpx.get")
def test_dxy_weakening_bullish(mock_get, mock_set_cache, mock_get_cache):
    """DXY 下跌 > 0.5% → weakening + bullish"""
    resp = MagicMock()
    resp.json.return_value = _mock_yahoo_response(
        [105.0, 104.8, 104.5, 104.2, 104.0, 103.5, 102.9]
    )
    mock_get.return_value = resp

    result = get_dxy_trend()

    assert result["trend"] == "weakening"
    assert result["signal"] == "bullish"
    assert result["change_1d_pct"] < -0.3


@patch("cryptobot.data.dxy.get_cache", return_value=None)
@patch("cryptobot.data.dxy.set_cache")
@patch("cryptobot.data.dxy.httpx.get")
def test_dxy_stable_neutral(mock_get, mock_set_cache, mock_get_cache):
    """DXY 平稳 → stable + neutral"""
    resp = MagicMock()
    resp.json.return_value = _mock_yahoo_response(
        [104.0, 104.05, 104.1, 104.0, 103.95, 104.0, 104.02]
    )
    mock_get.return_value = resp

    result = get_dxy_trend()

    assert result["trend"] == "stable"
    assert result["signal"] == "neutral"


@patch("cryptobot.data.dxy.get_cache")
def test_dxy_cache_hit(mock_get_cache):
    """缓存命中"""
    cached = {"current_value": 104.5, "trend": "stable", "signal": "neutral", "_cached_at": 999}
    mock_get_cache.return_value = cached
    result = get_dxy_trend()
    assert result == cached


@patch("cryptobot.data.dxy.get_cache", return_value=None)
@patch("cryptobot.data.dxy.httpx.get", side_effect=Exception("network error"))
def test_dxy_api_error(mock_get, mock_get_cache):
    """API 错误且无过期缓存时返回空结果 (data_available=False)"""
    result = get_dxy_trend()
    assert result == _empty_result(data_available=False)


@patch("cryptobot.data.dxy.get_cache")
@patch("cryptobot.data.dxy.httpx.get", side_effect=Exception("network error"))
def test_dxy_api_error_stale_fallback(mock_get, mock_get_cache):
    """API 错误时使用过期缓存兜底"""
    stale_data = {"current_value": 104.0, "trend": "stable", "signal": "neutral"}
    # 第一次调用 (正常 TTL) 返回 None, 第二次调用 (stale TTL) 返回过期缓存
    mock_get_cache.side_effect = [None, stale_data]
    result = get_dxy_trend()
    assert result["current_value"] == 104.0
    assert result["_is_stale"] is True


@patch("cryptobot.data.dxy.get_cache", return_value=None)
@patch("cryptobot.data.dxy.set_cache")
@patch("cryptobot.data.dxy.httpx.get")
def test_dxy_insufficient_data(mock_get, mock_set_cache, mock_get_cache):
    """数据不足返回空结果"""
    resp = MagicMock()
    resp.json.return_value = _mock_yahoo_response([104.0])
    mock_get.return_value = resp

    result = get_dxy_trend()
    assert result == _empty_result(data_available=False)


@patch("cryptobot.data.dxy.get_cache", return_value=None)
@patch("cryptobot.data.dxy.set_cache")
@patch("cryptobot.data.dxy.httpx.get")
def test_dxy_filters_none_values(mock_get, mock_set_cache, mock_get_cache):
    """过滤 None 值"""
    resp = MagicMock()
    resp.json.return_value = _mock_yahoo_response(
        [None, 103.0, None, 104.0, None, 104.5, 104.6]
    )
    mock_get.return_value = resp

    result = get_dxy_trend()
    assert result["current_value"] == 104.6


def test_empty_result_structure():
    result = _empty_result()
    assert result["current_value"] == 0
    assert result["trend"] == "stable"
    assert result["signal"] == "neutral"
    assert result["_data_available"] is True

    result_unavail = _empty_result(data_available=False)
    assert result_unavail["_data_available"] is False
