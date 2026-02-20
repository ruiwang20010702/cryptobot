"""数据模块测试 (需要网络连接)"""

import pytest

from cryptobot.data.onchain import get_funding_rate, get_open_interest_hist, get_taker_buy_sell_ratio
from cryptobot.data.sentiment import get_fear_greed_index, get_long_short_ratio
from cryptobot.data.news import get_market_overview, get_coin_info
from cryptobot.cache import get_cache, set_cache


class TestCache:
    def test_set_and_get(self, tmp_path, monkeypatch):
        import cryptobot.cache
        monkeypatch.setattr(cryptobot.cache, "DATA_OUTPUT_DIR", tmp_path)

        set_cache("test", "key1", {"value": 42})
        result = get_cache("test", "key1", ttl=60)
        assert result is not None
        assert result["value"] == 42

    def test_expired_cache(self, tmp_path, monkeypatch):
        import cryptobot.cache
        monkeypatch.setattr(cryptobot.cache, "DATA_OUTPUT_DIR", tmp_path)

        set_cache("test", "key2", {"value": 1})
        result = get_cache("test", "key2", ttl=0)  # TTL=0 立即过期
        assert result is None


@pytest.mark.network
class TestOnchainData:
    """链上数据测试 (需网络)"""

    def test_funding_rate(self):
        result = get_funding_rate("BTCUSDT", limit=5)
        assert result["symbol"] == "BTCUSDT"
        assert "current_rate" in result
        assert result["count"] > 0

    def test_open_interest(self):
        result = get_open_interest_hist("BTCUSDT", limit=5)
        assert result["symbol"] == "BTCUSDT"
        assert result["current_oi_value"] > 0

    def test_taker_ratio(self):
        result = get_taker_buy_sell_ratio("BTCUSDT", limit=5)
        assert result["symbol"] == "BTCUSDT"
        assert result["current_ratio"] > 0


@pytest.mark.network
class TestSentimentData:
    def test_fear_greed(self):
        result = get_fear_greed_index(limit=5)
        assert 0 <= result["current_value"] <= 100
        assert result["current_classification"] != ""

    def test_long_short_ratio(self):
        result = get_long_short_ratio("BTCUSDT", limit=5)
        assert result["symbol"] == "BTCUSDT"
        assert result["current_ratio"] > 0


@pytest.mark.network
class TestNewsData:
    def test_market_overview(self):
        result = get_market_overview()
        assert result["total_market_cap_usd"] > 0
        assert result["btc_dominance"] > 0

    def test_coin_info(self):
        result = get_coin_info("BTC")
        assert result["symbol"] == "BTC"
        assert result["current_price"] > 0

    def test_unknown_coin(self):
        result = get_coin_info("FAKECOIN123")
        assert "error" in result
