"""Tests for cryptobot.data.liquidation — force order / liquidation data."""

from unittest.mock import MagicMock, patch


from cryptobot.data.liquidation import _calc_clusters, _empty_result, get_force_orders


# ---------------------------------------------------------------------------
# 1. _empty_result
# ---------------------------------------------------------------------------

class TestEmptyResult:
    def test_returns_all_zeros(self):
        result = _empty_result("BTCUSDT")
        assert result["symbol"] == "BTCUSDT"
        assert result["long_liq_count"] == 0
        assert result["short_liq_count"] == 0
        assert result["long_liq_amount"] == 0
        assert result["short_liq_amount"] == 0
        assert result["net_liq_bias"] == "no_data"
        assert result["intensity"] == "low"
        assert result["clusters"] == []
        assert result["total_count"] == 0


# ---------------------------------------------------------------------------
# 2–4. _calc_clusters
# ---------------------------------------------------------------------------

class TestCalcClusters:
    def test_spread_prices_returns_sorted_bins(self):
        """Prices spread across a range → multiple bins sorted by count desc."""
        prices = [100.0, 101.0, 102.0, 103.0, 104.0,
                  100.5, 101.5, 100.2, 100.8, 101.2]
        clusters = _calc_clusters(prices, n_bins=5)

        assert len(clusters) > 0
        # sorted by count descending
        counts = [c["count"] for c in clusters]
        assert counts == sorted(counts, reverse=True)
        # total count across bins should equal number of prices
        assert sum(c["count"] for c in clusters) == len(prices)

    def test_less_than_2_prices_returns_empty(self):
        assert _calc_clusters([100.0]) == []
        assert _calc_clusters([]) == []

    def test_all_same_price_single_cluster(self):
        clusters = _calc_clusters([50000.0, 50000.0, 50000.0])
        assert len(clusters) == 1
        assert clusters[0]["count"] == 3
        assert clusters[0]["range_low"] == clusters[0]["range_high"]

    def test_two_distinct_groups(self):
        """Two groups of prices far apart → at least 2 bins with counts."""
        prices = [10000.0] * 5 + [20000.0] * 3
        clusters = _calc_clusters(prices, n_bins=5)

        non_empty = [c for c in clusters if c["count"] > 0]
        assert len(non_empty) >= 2
        assert sum(c["count"] for c in clusters) == 8


# ---------------------------------------------------------------------------
# 5. get_force_orders — cache hit
# ---------------------------------------------------------------------------

class TestGetForceOrdersCacheHit:

    @patch("cryptobot.data.liquidation.set_cache")
    @patch("cryptobot.data.liquidation.get_cache")
    @patch("cryptobot.data.liquidation.httpx")
    def test_cache_hit_returns_cached(self, mock_httpx, mock_get_cache, mock_set_cache):
        cached_data = {"symbol": "BTCUSDT", "cached": True}
        mock_get_cache.return_value = cached_data

        result = get_force_orders("BTCUSDT")

        assert result == cached_data
        mock_httpx.get.assert_not_called()
        mock_set_cache.assert_not_called()


# ---------------------------------------------------------------------------
# 6–10. get_force_orders — API responses
# ---------------------------------------------------------------------------

class TestGetForceOrdersAPI:

    def _setup_mocks(self, mock_httpx, mock_get_cache, orders):
        mock_get_cache.return_value = None
        mock_resp = MagicMock()
        mock_resp.json.return_value = orders
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

    # 6. Mixed SELL/BUY orders
    @patch("cryptobot.data.liquidation.set_cache")
    @patch("cryptobot.data.liquidation.get_cache")
    @patch("cryptobot.data.liquidation.httpx")
    def test_mixed_sell_buy_orders(self, mock_httpx, mock_get_cache, mock_set_cache):
        orders = [
            {"side": "SELL", "price": "95000", "origQty": "0.1", "time": 1000},
            {"side": "SELL", "price": "94000", "origQty": "0.2", "time": 1001},
            {"side": "BUY", "price": "96000", "origQty": "0.05", "time": 1002},
        ]
        self._setup_mocks(mock_httpx, mock_get_cache, orders)

        result = get_force_orders("BTCUSDT")

        assert result["long_liq_count"] == 2  # SELL = long liquidated
        assert result["short_liq_count"] == 1  # BUY = short liquidated
        assert result["long_liq_amount"] == round(95000 * 0.1 + 94000 * 0.2, 2)
        assert result["short_liq_amount"] == round(96000 * 0.05, 2)
        assert result["total_count"] == 3
        mock_set_cache.assert_called_once()

    # 7. Empty API response
    @patch("cryptobot.data.liquidation.set_cache")
    @patch("cryptobot.data.liquidation.get_cache")
    @patch("cryptobot.data.liquidation.httpx")
    def test_empty_api_response(self, mock_httpx, mock_get_cache, mock_set_cache):
        self._setup_mocks(mock_httpx, mock_get_cache, [])

        result = get_force_orders("BTCUSDT")

        assert result == _empty_result("BTCUSDT")

    # 8. API exception
    @patch("cryptobot.data.liquidation.set_cache")
    @patch("cryptobot.data.liquidation.get_cache")
    @patch("cryptobot.data.liquidation.httpx")
    def test_api_exception_returns_empty(self, mock_httpx, mock_get_cache, mock_set_cache):
        mock_get_cache.return_value = None
        mock_httpx.get.side_effect = Exception("connection timeout")

        result = get_force_orders("BTCUSDT")

        assert result["net_liq_bias"] == "no_data"
        assert result["total_count"] == 0

    # 9. Intensity thresholds
    @patch("cryptobot.data.liquidation.set_cache")
    @patch("cryptobot.data.liquidation.get_cache")
    @patch("cryptobot.data.liquidation.httpx")
    def test_intensity_extreme(self, mock_httpx, mock_get_cache, mock_set_cache):
        """51 orders → extreme."""
        orders = [
            {"side": "SELL", "price": "95000", "origQty": "0.01", "time": i}
            for i in range(51)
        ]
        self._setup_mocks(mock_httpx, mock_get_cache, orders)

        result = get_force_orders("BTCUSDT")
        assert result["intensity"] == "extreme"
        assert result["total_count"] == 51

    @patch("cryptobot.data.liquidation.set_cache")
    @patch("cryptobot.data.liquidation.get_cache")
    @patch("cryptobot.data.liquidation.httpx")
    def test_intensity_high(self, mock_httpx, mock_get_cache, mock_set_cache):
        """21 orders → high."""
        orders = [
            {"side": "BUY", "price": "96000", "origQty": "0.01", "time": i}
            for i in range(21)
        ]
        self._setup_mocks(mock_httpx, mock_get_cache, orders)

        result = get_force_orders("BTCUSDT")
        assert result["intensity"] == "high"

    @patch("cryptobot.data.liquidation.set_cache")
    @patch("cryptobot.data.liquidation.get_cache")
    @patch("cryptobot.data.liquidation.httpx")
    def test_intensity_moderate(self, mock_httpx, mock_get_cache, mock_set_cache):
        """6 orders → moderate."""
        orders = [
            {"side": "SELL", "price": "95000", "origQty": "0.01", "time": i}
            for i in range(6)
        ]
        self._setup_mocks(mock_httpx, mock_get_cache, orders)

        result = get_force_orders("BTCUSDT")
        assert result["intensity"] == "moderate"

    @patch("cryptobot.data.liquidation.set_cache")
    @patch("cryptobot.data.liquidation.get_cache")
    @patch("cryptobot.data.liquidation.httpx")
    def test_intensity_low(self, mock_httpx, mock_get_cache, mock_set_cache):
        """3 orders → low."""
        orders = [
            {"side": "SELL", "price": "95000", "origQty": "0.01", "time": i}
            for i in range(3)
        ]
        self._setup_mocks(mock_httpx, mock_get_cache, orders)

        result = get_force_orders("BTCUSDT")
        assert result["intensity"] == "low"

    # 10. net_liq_bias
    @patch("cryptobot.data.liquidation.set_cache")
    @patch("cryptobot.data.liquidation.get_cache")
    @patch("cryptobot.data.liquidation.httpx")
    def test_net_liq_bias_long_squeezed(self, mock_httpx, mock_get_cache, mock_set_cache):
        """Long amount >> short amount * 1.5 → long_squeezed."""
        orders = [
            {"side": "SELL", "price": "95000", "origQty": "1.0", "time": 1},  # long liq: 95000
            {"side": "BUY", "price": "96000", "origQty": "0.1", "time": 2},   # short liq: 9600
        ]
        self._setup_mocks(mock_httpx, mock_get_cache, orders)

        result = get_force_orders("BTCUSDT")
        assert result["net_liq_bias"] == "long_squeezed"

    @patch("cryptobot.data.liquidation.set_cache")
    @patch("cryptobot.data.liquidation.get_cache")
    @patch("cryptobot.data.liquidation.httpx")
    def test_net_liq_bias_short_squeezed(self, mock_httpx, mock_get_cache, mock_set_cache):
        """Short amount >> long amount * 1.5 → short_squeezed."""
        orders = [
            {"side": "BUY", "price": "96000", "origQty": "1.0", "time": 1},   # short liq: 96000
            {"side": "SELL", "price": "95000", "origQty": "0.1", "time": 2},   # long liq: 9500
        ]
        self._setup_mocks(mock_httpx, mock_get_cache, orders)

        result = get_force_orders("BTCUSDT")
        assert result["net_liq_bias"] == "short_squeezed"

    @patch("cryptobot.data.liquidation.set_cache")
    @patch("cryptobot.data.liquidation.get_cache")
    @patch("cryptobot.data.liquidation.httpx")
    def test_net_liq_bias_balanced(self, mock_httpx, mock_get_cache, mock_set_cache):
        """Similar amounts → balanced."""
        orders = [
            {"side": "SELL", "price": "95000", "origQty": "0.1", "time": 1},   # long: 9500
            {"side": "BUY", "price": "95000", "origQty": "0.1", "time": 2},    # short: 9500
        ]
        self._setup_mocks(mock_httpx, mock_get_cache, orders)

        result = get_force_orders("BTCUSDT")
        assert result["net_liq_bias"] == "balanced"
