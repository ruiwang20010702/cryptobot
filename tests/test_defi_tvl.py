"""DeFi TVL 趋势数据测试"""

from unittest.mock import patch, MagicMock

from cryptobot.data.defi_tvl import get_defi_tvl, _empty_result


def _mock_tvl_history(current, prev_1d, prev_7d):
    """生成模拟 TVL 历史数据 (8 天)"""
    # 构造 8 天数据点，index -1 = current, -2 = prev_1d, [0] = prev_7d
    data = []
    for i in range(8):
        if i == 0:
            data.append({"date": 1000000 + i * 86400, "tvl": prev_7d})
        elif i == 6:
            data.append({"date": 1000000 + i * 86400, "tvl": prev_1d})
        elif i == 7:
            data.append({"date": 1000000 + i * 86400, "tvl": current})
        else:
            data.append({"date": 1000000 + i * 86400, "tvl": prev_7d})
    return data


@patch("cryptobot.data.defi_tvl.get_cache", return_value=None)
@patch("cryptobot.data.defi_tvl.set_cache")
@patch("cryptobot.data.defi_tvl.httpx.get")
def test_tvl_growing(mock_get, mock_set_cache, mock_get_cache):
    """TVL 增长 > 5% → growing"""
    resp = MagicMock()
    resp.json.return_value = _mock_tvl_history(
        current=110_000_000_000,
        prev_1d=108_000_000_000,
        prev_7d=100_000_000_000,
    )
    mock_get.return_value = resp

    result = get_defi_tvl("ETHUSDT")

    assert result["chain"] == "Ethereum"
    assert result["tvl_trend"] == "growing"
    assert result["tvl_change_7d_pct"] > 5
    assert result["risk_flag"] is False
    mock_set_cache.assert_called_once()


@patch("cryptobot.data.defi_tvl.get_cache", return_value=None)
@patch("cryptobot.data.defi_tvl.set_cache")
@patch("cryptobot.data.defi_tvl.httpx.get")
def test_tvl_declining_with_risk(mock_get, mock_set_cache, mock_get_cache):
    """TVL 下降 > 10% → declining + risk_flag"""
    resp = MagicMock()
    resp.json.return_value = _mock_tvl_history(
        current=85_000_000_000,
        prev_1d=88_000_000_000,
        prev_7d=100_000_000_000,
    )
    mock_get.return_value = resp

    result = get_defi_tvl("SOLUSDT")

    assert result["chain"] == "Solana"
    assert result["tvl_trend"] == "declining"
    assert result["risk_flag"] is True


@patch("cryptobot.data.defi_tvl.get_cache", return_value=None)
@patch("cryptobot.data.defi_tvl.set_cache")
@patch("cryptobot.data.defi_tvl.httpx.get")
def test_tvl_stable(mock_get, mock_set_cache, mock_get_cache):
    """TVL 稳定"""
    resp = MagicMock()
    resp.json.return_value = _mock_tvl_history(
        current=50_000_000_000,
        prev_1d=49_800_000_000,
        prev_7d=49_500_000_000,
    )
    mock_get.return_value = resp

    result = get_defi_tvl("BNBUSDT")

    assert result["chain"] == "BSC"
    assert result["tvl_trend"] == "stable"
    assert result["risk_flag"] is False


def test_unsupported_symbol_returns_empty():
    """不在映射中的币种返回空结果"""
    result = get_defi_tvl("DOGEUSDT")
    assert result["chain"] == ""
    assert result["current_tvl"] == 0


@patch("cryptobot.data.defi_tvl.get_cache")
def test_cache_hit(mock_get_cache):
    """缓存命中"""
    cached = {"symbol": "ETHUSDT", "chain": "Ethereum", "tvl_trend": "growing", "_cached_at": 999}
    mock_get_cache.return_value = cached
    result = get_defi_tvl("ETHUSDT")
    assert result == cached


@patch("cryptobot.data.defi_tvl.get_cache", return_value=None)
@patch("cryptobot.data.defi_tvl.httpx.get", side_effect=Exception("network error"))
def test_api_error_returns_empty(mock_get, mock_get_cache):
    """API 错误返回空结果"""
    result = get_defi_tvl("ETHUSDT")
    assert result["symbol"] == "ETHUSDT"
    assert result["current_tvl"] == 0


def test_empty_result_structure():
    result = _empty_result("ETHUSDT")
    assert result["symbol"] == "ETHUSDT"
    assert result["chain"] == ""
    assert result["tvl_trend"] == "stable"
    assert result["risk_flag"] is False
