"""Tests for cryptobot.data.coinglass — CoinGlass liquidation heatmap data."""

from unittest.mock import MagicMock, patch


from cryptobot.data.coinglass import (
    _calc_nearest_liq_level,
    _empty_result,
    get_liquidation_heatmap,
)


# ---------------------------------------------------------------------------
# 1. _empty_result
# ---------------------------------------------------------------------------


class TestEmptyResult:
    def test_returns_correct_structure(self):
        result = _empty_result("BTCUSDT")
        assert result["symbol"] == "BTCUSDT"
        assert result["long_liq_usd"] == 0
        assert result["short_liq_usd"] == 0
        assert result["liq_ratio"] == 0
        assert result["nearest_liq_level"] == "unknown"
        assert result["data_source"] == "coinglass"

    def test_uses_given_symbol(self):
        result = _empty_result("ETHUSDT")
        assert result["symbol"] == "ETHUSDT"


# ---------------------------------------------------------------------------
# 2. Cache hit
# ---------------------------------------------------------------------------


class TestCacheHit:

    @patch("cryptobot.data.coinglass.set_cache")
    @patch("cryptobot.data.coinglass.get_cache")
    @patch("cryptobot.data.coinglass.httpx")
    def test_cache_hit_returns_cached(self, mock_httpx, mock_get_cache, mock_set_cache):
        cached_data = {"symbol": "BTCUSDT", "cached": True}
        mock_get_cache.return_value = cached_data

        result = get_liquidation_heatmap("BTCUSDT")

        assert result == cached_data
        mock_httpx.get.assert_not_called()
        mock_set_cache.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Normal API response
# ---------------------------------------------------------------------------


class TestNormalAPI:

    def _setup_mocks(self, mock_httpx, mock_get_cache, mock_settings, body):
        mock_get_cache.return_value = None
        mock_settings.return_value = {
            "data_sources": {
                "coinglass": {"base_url": "https://open-api-v3.coinglass.com"}
            }
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = body
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

    @patch("cryptobot.data.coinglass.load_settings")
    @patch("cryptobot.data.coinglass.set_cache")
    @patch("cryptobot.data.coinglass.get_cache")
    @patch("cryptobot.data.coinglass.httpx")
    @patch.dict("os.environ", {"COINGLASS_API_KEY": "test-key"})
    def test_normal_response_parsed(
        self, mock_httpx, mock_get_cache, mock_set_cache, mock_settings
    ):
        body = {
            "code": "0",
            "msg": "success",
            "data": {"longLiqUsd": 5000000.0, "shortLiqUsd": 3000000.0},
        }
        self._setup_mocks(mock_httpx, mock_get_cache, mock_settings, body)

        result = get_liquidation_heatmap("BTCUSDT")

        assert result["symbol"] == "BTCUSDT"
        assert result["long_liq_usd"] == 5000000.0
        assert result["short_liq_usd"] == 3000000.0
        assert result["liq_ratio"] == round(5000000.0 / 3000000.0, 4)
        assert result["nearest_liq_level"] == "below"  # long > short
        assert result["data_source"] == "coinglass"
        mock_set_cache.assert_called_once()

    @patch("cryptobot.data.coinglass.load_settings")
    @patch("cryptobot.data.coinglass.set_cache")
    @patch("cryptobot.data.coinglass.get_cache")
    @patch("cryptobot.data.coinglass.httpx")
    @patch.dict("os.environ", {"COINGLASS_API_KEY": "test-key"})
    def test_api_bad_code_returns_empty(
        self, mock_httpx, mock_get_cache, mock_set_cache, mock_settings
    ):
        body = {"code": "1", "msg": "error", "data": None}
        self._setup_mocks(mock_httpx, mock_get_cache, mock_settings, body)

        result = get_liquidation_heatmap("BTCUSDT")

        assert result["nearest_liq_level"] == "unknown"
        assert result["long_liq_usd"] == 0


# ---------------------------------------------------------------------------
# 4. API key empty
# ---------------------------------------------------------------------------


class TestApiKeyEmpty:

    @patch("cryptobot.data.coinglass.set_cache")
    @patch("cryptobot.data.coinglass.get_cache")
    @patch("cryptobot.data.coinglass.httpx")
    @patch.dict("os.environ", {}, clear=True)
    def test_no_api_key_returns_empty(self, mock_httpx, mock_get_cache, mock_set_cache):
        mock_get_cache.return_value = None

        result = get_liquidation_heatmap("BTCUSDT")

        assert result == _empty_result("BTCUSDT")
        mock_httpx.get.assert_not_called()


# ---------------------------------------------------------------------------
# 5. API exception
# ---------------------------------------------------------------------------


class TestApiException:

    @patch("cryptobot.data.coinglass.load_settings")
    @patch("cryptobot.data.coinglass.set_cache")
    @patch("cryptobot.data.coinglass.get_cache")
    @patch("cryptobot.data.coinglass.httpx")
    @patch.dict("os.environ", {"COINGLASS_API_KEY": "test-key"})
    def test_exception_returns_empty(
        self, mock_httpx, mock_get_cache, mock_set_cache, mock_settings
    ):
        mock_get_cache.return_value = None
        mock_settings.return_value = {
            "data_sources": {
                "coinglass": {"base_url": "https://open-api-v3.coinglass.com"}
            }
        }
        mock_httpx.get.side_effect = Exception("connection timeout")

        result = get_liquidation_heatmap("BTCUSDT")

        assert result["nearest_liq_level"] == "unknown"
        assert result["long_liq_usd"] == 0


# ---------------------------------------------------------------------------
# 6. liq_ratio calculation
# ---------------------------------------------------------------------------


class TestLiqRatio:

    @patch("cryptobot.data.coinglass.load_settings")
    @patch("cryptobot.data.coinglass.set_cache")
    @patch("cryptobot.data.coinglass.get_cache")
    @patch("cryptobot.data.coinglass.httpx")
    @patch.dict("os.environ", {"COINGLASS_API_KEY": "test-key"})
    def test_short_zero_gives_999(
        self, mock_httpx, mock_get_cache, mock_set_cache, mock_settings
    ):
        mock_get_cache.return_value = None
        mock_settings.return_value = {
            "data_sources": {
                "coinglass": {"base_url": "https://open-api-v3.coinglass.com"}
            }
        }
        body = {
            "code": "0",
            "msg": "success",
            "data": {"longLiqUsd": 1000000.0, "shortLiqUsd": 0},
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = body
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        result = get_liquidation_heatmap("BTCUSDT")

        assert result["liq_ratio"] == 999.0

    @patch("cryptobot.data.coinglass.load_settings")
    @patch("cryptobot.data.coinglass.set_cache")
    @patch("cryptobot.data.coinglass.get_cache")
    @patch("cryptobot.data.coinglass.httpx")
    @patch.dict("os.environ", {"COINGLASS_API_KEY": "test-key"})
    def test_both_zero_gives_zero(
        self, mock_httpx, mock_get_cache, mock_set_cache, mock_settings
    ):
        mock_get_cache.return_value = None
        mock_settings.return_value = {
            "data_sources": {
                "coinglass": {"base_url": "https://open-api-v3.coinglass.com"}
            }
        }
        body = {
            "code": "0",
            "msg": "success",
            "data": {"longLiqUsd": 0, "shortLiqUsd": 0},
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = body
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        result = get_liquidation_heatmap("BTCUSDT")

        assert result["liq_ratio"] == 0.0

    @patch("cryptobot.data.coinglass.load_settings")
    @patch("cryptobot.data.coinglass.set_cache")
    @patch("cryptobot.data.coinglass.get_cache")
    @patch("cryptobot.data.coinglass.httpx")
    @patch.dict("os.environ", {"COINGLASS_API_KEY": "test-key"})
    def test_normal_ratio(
        self, mock_httpx, mock_get_cache, mock_set_cache, mock_settings
    ):
        mock_get_cache.return_value = None
        mock_settings.return_value = {
            "data_sources": {
                "coinglass": {"base_url": "https://open-api-v3.coinglass.com"}
            }
        }
        body = {
            "code": "0",
            "msg": "success",
            "data": {"longLiqUsd": 4000000.0, "shortLiqUsd": 2000000.0},
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = body
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        result = get_liquidation_heatmap("BTCUSDT")

        assert result["liq_ratio"] == 2.0


# ---------------------------------------------------------------------------
# 7. nearest_liq_level logic
# ---------------------------------------------------------------------------


class TestNearestLiqLevel:

    def test_above_when_short_dominates(self):
        assert _calc_nearest_liq_level(1000, 5000) == "above"

    def test_below_when_long_dominates(self):
        assert _calc_nearest_liq_level(5000, 1000) == "below"

    def test_balanced_when_similar(self):
        assert _calc_nearest_liq_level(5000, 4500) == "balanced"

    def test_balanced_when_both_zero(self):
        assert _calc_nearest_liq_level(0, 0) == "balanced"

    def test_above_threshold_boundary(self):
        # ratio = |1000 - 3000| / 4000 = 0.5 > 0.2, short > long → above
        assert _calc_nearest_liq_level(1000, 3000) == "above"

    def test_below_threshold_boundary(self):
        # ratio = |3000 - 1000| / 4000 = 0.5 > 0.2, long > short → below
        assert _calc_nearest_liq_level(3000, 1000) == "below"
