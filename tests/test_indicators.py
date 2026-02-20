"""指标计算测试"""

from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from cryptobot.indicators.calculator import (
    calc_all_indicators,
    load_klines,
    _fetch_klines_from_api,
    _ema_alignment,
    _rsi_zone,
    _bb_position,
)


class TestLoadKlines:
    def test_load_existing_data(self):
        """加载已下载的 BTC 4h 数据"""
        df = load_klines("BTCUSDT", "4h")
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0
        assert "close" in df.columns
        assert "volume" in df.columns

    @patch("cryptobot.indicators.calculator._fetch_klines_from_api")
    def test_fallback_to_api(self, mock_api):
        """本地文件不存在时回退到 API"""
        mock_df = pd.DataFrame({
            "open": [95000.0], "high": [96000.0],
            "low": [94000.0], "close": [95500.0], "volume": [100.0],
        }, index=pd.to_datetime([1700000000000], unit="ms"))
        mock_df.index.name = "datetime"
        mock_api.return_value = mock_df

        df = load_klines("NONEEXIST123USDT", "4h")
        assert isinstance(df, pd.DataFrame)
        assert "close" in df.columns
        mock_api.assert_called_once_with("NONEEXIST123USDT", "4h")

    @patch("cryptobot.indicators.calculator._fetch_klines_from_api",
           side_effect=Exception("API error"))
    def test_api_fallback_fails_raises(self, mock_api):
        """本地文件不存在且 API 也失败时抛出 FileNotFoundError"""
        with pytest.raises(FileNotFoundError, match="API 获取失败"):
            load_klines("NONEEXIST123USDT", "4h")


class TestFetchKlinesFromApi:
    @patch("httpx.get")
    @patch("cryptobot.cache.get_cache", return_value=None)
    @patch("cryptobot.cache.set_cache")
    def test_fetches_and_parses(self, mock_set, mock_get_cache, mock_http):
        """API 返回正确解析为 DataFrame"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            [1700000000000, "95000", "96000", "94000", "95500", "100", 0, 0, 0, 0, 0, 0],
            [1700014400000, "95500", "97000", "95000", "96500", "120", 0, 0, 0, 0, 0, 0],
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_http.return_value = mock_resp

        df = _fetch_klines_from_api("BTCUSDT", "4h", limit=2)
        assert len(df) == 2
        assert df["close"].iloc[0] == 95500.0
        assert df["close"].iloc[1] == 96500.0
        assert df.index.name == "datetime"
        mock_set.assert_called_once()

    @patch("cryptobot.cache.get_cache")
    def test_cache_hit(self, mock_get_cache):
        """缓存命中直接返回"""
        mock_get_cache.return_value = {
            "records": [
                {"datetime": 1700000000000, "open": 95000, "high": 96000,
                 "low": 94000, "close": 95500, "volume": 100},
            ],
        }
        df = _fetch_klines_from_api("BTCUSDT", "4h")
        assert len(df) == 1
        assert df["close"].iloc[0] == 95500.0


class TestCalcAllIndicators:
    @pytest.fixture(autouse=True)
    def _result(self):
        self.result = calc_all_indicators("BTCUSDT", "4h")

    def test_has_required_keys(self):
        assert "symbol" in self.result
        assert "trend" in self.result
        assert "momentum" in self.result
        assert "volatility" in self.result
        assert "signals" in self.result

    def test_trend_indicators(self):
        t = self.result["trend"]
        assert "ema_7" in t
        assert "macd" in t
        assert "adx" in t
        assert t["ema_alignment"] in ("bullish", "bearish", "mixed", "unknown")

    def test_momentum_indicators(self):
        m = self.result["momentum"]
        assert m["rsi_14"] is None or 0 <= m["rsi_14"] <= 100
        assert m["rsi_zone"] in ("overbought", "oversold", "strong", "weak", "neutral", "unknown")

    def test_volatility_indicators(self):
        v = self.result["volatility"]
        assert v["atr_14"] is not None
        assert v["atr_pct"] >= 0

    def test_signals(self):
        s = self.result["signals"]
        assert -10 <= s["technical_score"] <= 10
        assert s["bias"] in ("bullish", "bearish", "neutral")
        assert isinstance(s["signals"], list)


class TestHelpers:
    def test_rsi_zone(self):
        assert _rsi_zone(75) == "overbought"
        assert _rsi_zone(25) == "oversold"
        assert _rsi_zone(55) == "neutral"
        assert _rsi_zone(65) == "strong"
        assert _rsi_zone(35) == "weak"
        assert _rsi_zone(None) == "unknown"

    def test_ema_alignment(self):
        assert _ema_alignment(100, 90, 80) == "bullish"
        assert _ema_alignment(80, 90, 100) == "bearish"
        assert _ema_alignment(90, 100, 80) == "mixed"

    def test_bb_position(self):
        pos = _bb_position(110, 90, 100)
        assert pos == 0.5  # 中间位置

        pos = _bb_position(110, 90, 90)
        assert pos == 0.0  # 下轨
