"""稳定币流入流出数据测试"""

from unittest.mock import patch, MagicMock

from cryptobot.data.stablecoin import get_stablecoin_flows, _empty_result


def _mock_defillama_response():
    """模拟 DefiLlama API 响应"""
    return {
        "peggedAssets": [
            {
                "name": "Tether",
                "circulating": {"peggedUSD": 120_000_000_000},
                "circulatingPrevDay": {"peggedUSD": 119_000_000_000},
                "circulatingPrevWeek": {"peggedUSD": 117_000_000_000},
            },
            {
                "name": "USD Coin",
                "circulating": {"peggedUSD": 45_000_000_000},
                "circulatingPrevDay": {"peggedUSD": 44_800_000_000},
                "circulatingPrevWeek": {"peggedUSD": 44_000_000_000},
            },
            {
                "name": "DAI",
                "circulating": {"peggedUSD": 5_000_000_000},
                "circulatingPrevDay": {"peggedUSD": 4_900_000_000},
                "circulatingPrevWeek": {"peggedUSD": 4_800_000_000},
            },
        ]
    }


@patch("cryptobot.data.stablecoin.get_cache", return_value=None)
@patch("cryptobot.data.stablecoin.set_cache")
@patch("cryptobot.data.stablecoin.httpx.get")
def test_get_stablecoin_flows_inflow(mock_get, mock_set_cache, mock_get_cache):
    """测试正常流入场景"""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = _mock_defillama_response()
    mock_get.return_value = resp

    result = get_stablecoin_flows()

    assert result["total_mcap"] > 0
    assert result["flow_signal"] in ("inflow", "outflow", "neutral")
    assert "Tether" in result["breakdown"]
    assert "USD Coin" in result["breakdown"]
    # DAI 不应在 breakdown 中
    assert "DAI" not in result["breakdown"]
    # 缓存被写入
    mock_set_cache.assert_called_once()


@patch("cryptobot.data.stablecoin.get_cache", return_value=None)
@patch("cryptobot.data.stablecoin.set_cache")
@patch("cryptobot.data.stablecoin.httpx.get")
def test_get_stablecoin_flows_outflow(mock_get, mock_set_cache, mock_get_cache):
    """测试流出场景 (1d 变化 < -0.5%)"""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "peggedAssets": [
            {
                "name": "Tether",
                "circulating": {"peggedUSD": 100_000_000_000},
                "circulatingPrevDay": {"peggedUSD": 101_000_000_000},
                "circulatingPrevWeek": {"peggedUSD": 102_000_000_000},
            },
            {
                "name": "USD Coin",
                "circulating": {"peggedUSD": 40_000_000_000},
                "circulatingPrevDay": {"peggedUSD": 40_500_000_000},
                "circulatingPrevWeek": {"peggedUSD": 41_000_000_000},
            },
        ]
    }
    mock_get.return_value = resp

    result = get_stablecoin_flows()
    assert result["flow_signal"] == "outflow"
    assert result["change_1d_pct"] < -0.5


@patch("cryptobot.data.stablecoin.get_cache", return_value=None)
@patch("cryptobot.data.stablecoin.set_cache")
@patch("cryptobot.data.stablecoin.httpx.get")
def test_get_stablecoin_flows_neutral(mock_get, mock_set_cache, mock_get_cache):
    """测试中性场景 (1d 变化在 -0.5% ~ 0.5%)"""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "peggedAssets": [
            {
                "name": "Tether",
                "circulating": {"peggedUSD": 100_000_000_000},
                "circulatingPrevDay": {"peggedUSD": 100_000_000_000},
                "circulatingPrevWeek": {"peggedUSD": 100_000_000_000},
            },
            {
                "name": "USD Coin",
                "circulating": {"peggedUSD": 40_000_000_000},
                "circulatingPrevDay": {"peggedUSD": 40_000_000_000},
                "circulatingPrevWeek": {"peggedUSD": 40_000_000_000},
            },
        ]
    }
    mock_get.return_value = resp

    result = get_stablecoin_flows()
    assert result["flow_signal"] == "neutral"


@patch("cryptobot.data.stablecoin.get_cache")
def test_get_stablecoin_flows_cached(mock_get_cache):
    """测试缓存命中"""
    cached = {"total_mcap": 160e9, "flow_signal": "inflow", "_cached_at": 999}
    mock_get_cache.return_value = cached

    result = get_stablecoin_flows()
    assert result == cached


@patch("cryptobot.data.stablecoin.get_cache", return_value=None)
@patch("cryptobot.data.stablecoin.httpx.get", side_effect=Exception("network error"))
def test_get_stablecoin_flows_api_error(mock_get, mock_get_cache):
    """测试 API 错误返回空结果"""
    result = get_stablecoin_flows()
    assert result == _empty_result()


def test_empty_result_structure():
    """测试空结果结构"""
    result = _empty_result()
    assert result["total_mcap"] == 0
    assert result["flow_signal"] == "neutral"
    assert result["breakdown"] == {}
