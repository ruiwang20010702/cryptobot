"""Tests for cryptobot.data.orderbook"""

from unittest.mock import MagicMock, patch


from cryptobot.data.orderbook import get_orderbook_depth


# ---------------------------------------------------------------------------
# 1. 缓存命中 → 返回缓存数据，不调 httpx
# ---------------------------------------------------------------------------

class TestCacheHit:

    @patch("cryptobot.data.orderbook.set_cache")
    @patch("cryptobot.data.orderbook.get_cache")
    @patch("cryptobot.data.orderbook.httpx")
    def test_cache_hit_returns_cached(self, mock_httpx, mock_get_cache, mock_set_cache):
        cached_data = {"bid_volume": 10.0, "ask_volume": 5.0, "cached": True}
        mock_get_cache.return_value = cached_data

        result = get_orderbook_depth("BTCUSDT")

        assert result == cached_data
        mock_httpx.get.assert_not_called()
        mock_set_cache.assert_not_called()


# ---------------------------------------------------------------------------
# 2. 正常 API 响应 → 正确计算各字段
# ---------------------------------------------------------------------------

class TestNormalResponse:

    @patch("cryptobot.data.orderbook.set_cache")
    @patch("cryptobot.data.orderbook.get_cache")
    @patch("cryptobot.data.orderbook.httpx")
    def test_normal_api_response(self, mock_httpx, mock_get_cache, mock_set_cache):
        mock_get_cache.return_value = None
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "bids": [["50000.00", "1.5"], ["49999.00", "2.0"]],
            "asks": [["50001.00", "1.0"], ["50002.00", "3.0"]],
        }
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        result = get_orderbook_depth("BTCUSDT")

        assert result["bid_volume"] == 3.5  # 1.5 + 2.0
        assert result["ask_volume"] == 4.0  # 1.0 + 3.0
        assert result["bid_ask_ratio"] == round(3.5 / 4.0, 4)
        assert result["top_bid"] == 50000.00
        assert result["top_ask"] == 50001.00
        expected_spread = (50001.00 - 50000.00) / 50000.00 * 100
        assert result["spread_pct"] == round(expected_spread, 6)
        mock_set_cache.assert_called_once()


# ---------------------------------------------------------------------------
# 3. 空 bids/asks → 返回零值结果
# ---------------------------------------------------------------------------

class TestEmptyOrderbook:

    @patch("cryptobot.data.orderbook.set_cache")
    @patch("cryptobot.data.orderbook.get_cache")
    @patch("cryptobot.data.orderbook.httpx")
    def test_empty_bids_asks(self, mock_httpx, mock_get_cache, mock_set_cache):
        mock_get_cache.return_value = None
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"bids": [], "asks": []}
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        result = get_orderbook_depth("BTCUSDT")

        assert result["bid_volume"] == 0.0
        assert result["ask_volume"] == 0.0
        assert result["bid_ask_ratio"] == 0.0
        assert result["top_bid"] == 0.0
        assert result["top_ask"] == 0.0
        assert result["spread_pct"] == 0.0
        mock_set_cache.assert_not_called()


# ---------------------------------------------------------------------------
# 4. API 异常 → 返回零值结果, 不抛异常
# ---------------------------------------------------------------------------

class TestAPIException:

    @patch("cryptobot.data.orderbook.set_cache")
    @patch("cryptobot.data.orderbook.get_cache")
    @patch("cryptobot.data.orderbook.httpx")
    def test_api_exception_returns_zeros(self, mock_httpx, mock_get_cache, mock_set_cache):
        mock_get_cache.return_value = None
        mock_httpx.get.side_effect = Exception("connection timeout")

        result = get_orderbook_depth("BTCUSDT")

        assert result["bid_volume"] == 0.0
        assert result["ask_volume"] == 0.0
        assert result["bid_ask_ratio"] == 0.0
        mock_set_cache.assert_not_called()


# ---------------------------------------------------------------------------
# 5. ask_volume=0 时 bid_ask_ratio=999
# ---------------------------------------------------------------------------

class TestAskVolumeZero:

    @patch("cryptobot.data.orderbook.set_cache")
    @patch("cryptobot.data.orderbook.get_cache")
    @patch("cryptobot.data.orderbook.httpx")
    def test_ask_volume_zero_ratio_999(self, mock_httpx, mock_get_cache, mock_set_cache):
        mock_get_cache.return_value = None
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "bids": [["50000.00", "2.0"]],
            "asks": [["50001.00", "0.0"]],
        }
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        result = get_orderbook_depth("BTCUSDT")

        assert result["bid_ask_ratio"] == 999.0
        assert result["bid_volume"] == 2.0
        assert result["ask_volume"] == 0.0
