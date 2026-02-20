"""代币稀释风险测试"""

from unittest.mock import patch

from cryptobot.data.token_unlocks import get_dilution_risk, _empty_result


@patch("cryptobot.data.token_unlocks.get_cache", return_value=None)
@patch("cryptobot.data.token_unlocks.set_cache")
@patch("cryptobot.data.news.get_coin_info")
def test_high_dilution_risk(mock_coin_info, mock_set_cache, mock_get_cache):
    """稀释 > 100% -> high"""
    mock_coin_info.return_value = {
        "circulating_supply": 10_000_000,
        "total_supply": 100_000_000,  # (100-10)/10 = 900%
    }
    result = get_dilution_risk("XYZUSDT")
    assert result["risk_level"] == "high"
    assert result["dilution_pct"] == 900.0
    assert "高" in result["label"]
    mock_set_cache.assert_called_once()


@patch("cryptobot.data.token_unlocks.get_cache", return_value=None)
@patch("cryptobot.data.token_unlocks.set_cache")
@patch("cryptobot.data.news.get_coin_info")
def test_medium_dilution_risk(mock_coin_info, mock_set_cache, mock_get_cache):
    """稀释 50-100% -> medium"""
    mock_coin_info.return_value = {
        "circulating_supply": 100_000,
        "total_supply": 170_000,  # (170-100)/100 = 70%
    }
    result = get_dilution_risk("XYZUSDT")
    assert result["risk_level"] == "medium"
    assert result["dilution_pct"] == 70.0


@patch("cryptobot.data.token_unlocks.get_cache", return_value=None)
@patch("cryptobot.data.token_unlocks.set_cache")
@patch("cryptobot.data.news.get_coin_info")
def test_low_dilution_risk(mock_coin_info, mock_set_cache, mock_get_cache):
    """稀释 < 50% -> low"""
    mock_coin_info.return_value = {
        "circulating_supply": 100_000,
        "total_supply": 130_000,  # (130-100)/100 = 30%
    }
    result = get_dilution_risk("XYZUSDT")
    assert result["risk_level"] == "low"
    assert result["dilution_pct"] == 30.0


@patch("cryptobot.data.token_unlocks.get_cache", return_value=None)
@patch("cryptobot.data.token_unlocks.set_cache")
@patch("cryptobot.data.news.get_coin_info")
def test_fully_circulating(mock_coin_info, mock_set_cache, mock_get_cache):
    """全流通 -> none"""
    mock_coin_info.return_value = {
        "circulating_supply": 21_000_000,
        "total_supply": 21_000_000,
    }
    result = get_dilution_risk("BTCUSDT")
    assert result["risk_level"] == "none"
    assert result["dilution_pct"] == 0


@patch("cryptobot.data.token_unlocks.get_cache", return_value=None)
@patch("cryptobot.data.token_unlocks.set_cache")
@patch("cryptobot.data.news.get_coin_info")
def test_zero_total_supply(mock_coin_info, mock_set_cache, mock_get_cache):
    """total_supply 为 0"""
    mock_coin_info.return_value = {
        "circulating_supply": 100_000,
        "total_supply": 0,
    }
    result = get_dilution_risk("ETHUSDT")
    assert result["risk_level"] == "none"


@patch("cryptobot.data.token_unlocks.get_cache", return_value=None)
@patch("cryptobot.data.news.get_coin_info", side_effect=Exception("API error"))
def test_api_error(mock_coin_info, mock_get_cache):
    """API 错误返回空结果"""
    result = get_dilution_risk("XYZUSDT")
    assert result["risk_level"] == "unknown"


@patch("cryptobot.data.token_unlocks.get_cache")
def test_cache_hit(mock_get_cache):
    """缓存命中"""
    cached = {"symbol": "BTC", "risk_level": "none", "_cached_at": 999}
    mock_get_cache.return_value = cached
    result = get_dilution_risk("BTCUSDT")
    assert result == cached


def test_empty_result():
    result = _empty_result("BTC")
    assert result["symbol"] == "BTC"
    assert result["risk_level"] == "unknown"
