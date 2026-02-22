"""特征工程管道测试"""

import json

import pytest

from cryptobot.features.extractors import (
    extract_correlation_features,
    extract_macro_features,
    extract_multi_tf_features,
    extract_onchain_features,
    extract_orderbook_features,
    extract_sentiment_features,
    extract_tech_features,
)
from cryptobot.features.pipeline import (
    FeatureMatrix,
    FeatureVector,
    build_feature_matrix,
    build_feature_vector,
    normalize_features,
    to_csv_rows,
)


# ─── extractors ──────────────────────────────────────────────────────────


class TestExtractTechFeatures:
    def test_normal_input(self):
        tech = {
            "latest_close": 50000.0,
            "momentum": {"rsi_14": 55.0},
            "trend": {
                "adx": 30.0,
                "macd_hist": 100.0,
                "ema_alignment": "bullish",
            },
            "volatility": {"bb_position": 0.7, "atr_pct": 2.5},
        }
        result = extract_tech_features(tech)
        assert result["rsi"] == 55.0
        assert result["adx"] == 30.0
        assert result["ema_score"] == 1.0
        assert result["bb_position"] == 0.7
        assert result["atr_pct"] == 2.5
        # macd_hist = 100 / 50000 * 100 = 0.2
        assert abs(result["macd_hist"] - 0.2) < 0.001

    def test_bearish_alignment(self):
        tech = {
            "latest_close": 1.0,
            "trend": {"ema_alignment": "bearish", "adx": 0.0, "macd_hist": 0.0},
            "momentum": {},
            "volatility": {},
        }
        result = extract_tech_features(tech)
        assert result["ema_score"] == -1.0

    def test_empty_dict(self):
        result = extract_tech_features({})
        assert result["rsi"] == 0.0
        assert result["adx"] == 0.0
        assert result["ema_score"] == 0.0

    def test_none_input(self):
        result = extract_tech_features(None)
        assert all(v == 0.0 for v in result.values())


class TestExtractMultiTfFeatures:
    def test_normal_input(self):
        multi_tf = {
            "timeframes": {
                "1h": {"direction": "bullish"},
                "4h": {"direction": "bullish"},
                "1d": {"direction": "bearish"},
            },
        }
        result = extract_multi_tf_features(multi_tf)
        assert result["tf_bullish_count"] == 2.0
        assert result["tf_bearish_count"] == 1.0
        # alignment = (2 - 1) / 3 = 0.3333
        assert abs(result["tf_alignment_score"] - 0.3333) < 0.01

    def test_empty_dict(self):
        result = extract_multi_tf_features({})
        assert result["tf_alignment_score"] == 0.0

    def test_none_input(self):
        result = extract_multi_tf_features(None)
        assert result["tf_bullish_count"] == 0.0


class TestExtractOnchainFeatures:
    def test_normal_input(self):
        crypto = {
            "funding": {"current_rate": 0.0005},
            "open_interest": {"change_pct": 3.5},
            "long_short": {"current_ratio": 1.2},
        }
        result = extract_onchain_features(crypto)
        assert result["funding_rate"] == 0.0005
        assert result["oi_change_pct"] == 3.5
        assert result["long_short_ratio"] == 1.2

    def test_empty_dict(self):
        result = extract_onchain_features({})
        assert result["funding_rate"] == 0.0
        assert result["long_short_ratio"] == 1.0

    def test_none_input(self):
        result = extract_onchain_features(None)
        assert result["funding_rate"] == 0.0


class TestExtractSentimentFeatures:
    def test_normal_input(self):
        fg = {"current_value": 75}
        news = {"sentiment_score": 0.3}
        result = extract_sentiment_features(fg, news)
        assert result["fear_greed_index"] == 75.0
        assert result["news_sentiment"] == 0.3

    def test_empty_inputs(self):
        result = extract_sentiment_features({}, {})
        # 空 dict 无 current_value，返回 0.0
        assert result["fear_greed_index"] == 0.0
        assert result["news_sentiment"] == 0.0

    def test_none_inputs(self):
        result = extract_sentiment_features(None, None)
        assert result["fear_greed_index"] == 0.0
        assert result["news_sentiment"] == 0.0


class TestExtractOrderbookFeatures:
    def test_normal_input(self):
        ob = {"bid_ask_ratio": 1.5, "spread_pct": 0.05}
        result = extract_orderbook_features(ob)
        assert result["bid_ask_ratio"] == 1.5
        assert result["spread_pct"] == 0.05

    def test_empty_dict(self):
        result = extract_orderbook_features({})
        assert result["bid_ask_ratio"] == 1.0
        assert result["spread_pct"] == 0.0

    def test_none_input(self):
        result = extract_orderbook_features(None)
        assert result["bid_ask_ratio"] == 1.0


class TestExtractMacroFeatures:
    def test_normal_input(self):
        dxy = {"current_value": 104.5}
        macro = {"events": [{"impact": "high"}, {"impact": "low"}, {"impact": "high"}]}
        stablecoin = {"net_flow_7d": 500000000}
        result = extract_macro_features(dxy, macro, stablecoin)
        assert result["dxy_value"] == 104.5
        assert result["high_impact_events"] == 2.0
        assert result["stablecoin_flow"] == 500000000.0

    def test_empty_dicts(self):
        result = extract_macro_features({}, {}, {})
        assert result["dxy_value"] == 0.0
        assert result["high_impact_events"] == 0.0

    def test_none_inputs(self):
        result = extract_macro_features(None, None, None)
        assert result["stablecoin_flow"] == 0.0


class TestExtractCorrelationFeatures:
    def test_normal_input(self):
        result = extract_correlation_features(0.85)
        assert result["btc_correlation"] == 0.85
        assert result["btc_corr_category"] == 0.8

    def test_medium_correlation(self):
        result = extract_correlation_features(0.5)
        assert result["btc_corr_category"] == 0.5

    def test_low_correlation(self):
        result = extract_correlation_features(0.1)
        assert result["btc_corr_category"] == 0.2

    def test_negative_correlation(self):
        result = extract_correlation_features(-0.8)
        assert result["btc_correlation"] == -0.8
        assert result["btc_corr_category"] == 0.8

    def test_zero(self):
        result = extract_correlation_features(0.0)
        assert result["btc_correlation"] == 0.0
        assert result["btc_corr_category"] == 0.2


# ─── pipeline ────────────────────────────────────────────────────────────


class TestBuildFeatureVector:
    def test_with_data(self):
        vec = build_feature_vector(
            symbol="BTCUSDT",
            timestamp="2026-01-01T00:00:00",
            tech={
                "latest_close": 50000,
                "momentum": {"rsi_14": 60},
                "trend": {"adx": 25, "macd_hist": 50, "ema_alignment": "bullish"},
                "volatility": {"bb_position": 0.5, "atr_pct": 1.5},
            },
        )
        assert vec.symbol == "BTCUSDT"
        assert vec.timestamp == "2026-01-01T00:00:00"
        assert vec.features["rsi"] == 60.0
        assert len(vec.features) >= 20

    def test_all_empty(self):
        vec = build_feature_vector(symbol="ETHUSDT", timestamp="2026-01-01T00:00:00")
        assert vec.symbol == "ETHUSDT"
        assert len(vec.features) >= 20
        # 大部分值应该是默认值
        assert vec.features["rsi"] == 0.0


class TestBuildFeatureMatrix:
    def test_multiple_vectors(self):
        v1 = build_feature_vector("BTCUSDT", "2026-01-01T00:00:00")
        v2 = build_feature_vector("ETHUSDT", "2026-01-01T00:00:00")
        matrix = build_feature_matrix([v1, v2])
        assert len(matrix.vectors) == 2
        assert len(matrix.feature_names) >= 20
        assert matrix.feature_names == sorted(matrix.feature_names)


class TestNormalizeFeatures:
    def test_z_score(self):
        v1 = FeatureVector("A", "t1", {"x": 10.0, "y": 20.0})
        v2 = FeatureVector("B", "t2", {"x": 20.0, "y": 40.0})
        v3 = FeatureVector("C", "t3", {"x": 30.0, "y": 60.0})
        matrix = FeatureMatrix(vectors=[v1, v2, v3], feature_names=["x", "y"])

        normed = normalize_features(matrix, method="z_score")
        # mean(x) = 20, std(x) = sqrt(200/3) ~ 8.165
        # z_score(10) = (10-20)/8.165 ~ -1.2247
        assert len(normed.vectors) == 3
        assert abs(normed.vectors[0].features["x"] - (-1.224745)) < 0.01
        assert abs(normed.vectors[1].features["x"] - 0.0) < 0.01
        assert abs(normed.vectors[2].features["x"] - 1.224745) < 0.01

    def test_min_max(self):
        v1 = FeatureVector("A", "t1", {"x": 10.0})
        v2 = FeatureVector("B", "t2", {"x": 20.0})
        v3 = FeatureVector("C", "t3", {"x": 30.0})
        matrix = FeatureMatrix(vectors=[v1, v2, v3], feature_names=["x"])

        normed = normalize_features(matrix, method="min_max")
        assert normed.vectors[0].features["x"] == 0.0
        assert normed.vectors[1].features["x"] == 0.5
        assert normed.vectors[2].features["x"] == 1.0

    def test_std_zero_column(self):
        """std=0 的列标准化值设为 0.0"""
        v1 = FeatureVector("A", "t1", {"x": 5.0, "y": 10.0})
        v2 = FeatureVector("B", "t2", {"x": 5.0, "y": 20.0})
        matrix = FeatureMatrix(vectors=[v1, v2], feature_names=["x", "y"])

        normed = normalize_features(matrix, method="z_score")
        assert normed.vectors[0].features["x"] == 0.0
        assert normed.vectors[1].features["x"] == 0.0

    def test_min_max_constant_column(self):
        """max==min 的列标准化值设为 0.0"""
        v1 = FeatureVector("A", "t1", {"x": 7.0})
        v2 = FeatureVector("B", "t2", {"x": 7.0})
        matrix = FeatureMatrix(vectors=[v1, v2], feature_names=["x"])

        normed = normalize_features(matrix, method="min_max")
        assert normed.vectors[0].features["x"] == 0.0
        assert normed.vectors[1].features["x"] == 0.0

    def test_empty_matrix(self):
        matrix = FeatureMatrix(vectors=[], feature_names=["x"])
        normed = normalize_features(matrix)
        assert normed.vectors == []

    def test_invalid_method(self):
        with pytest.raises(ValueError, match="未知标准化方法"):
            normalize_features(
                FeatureMatrix(
                    vectors=[FeatureVector("A", "t1", {"x": 1.0})],
                    feature_names=["x"],
                ),
                method="invalid",
            )


class TestToCsvRows:
    def test_format(self):
        v1 = FeatureVector("BTCUSDT", "2026-01-01T00:00:00", {"rsi": 55.0, "adx": 30.0})
        v2 = FeatureVector("ETHUSDT", "2026-01-01T00:00:00", {"rsi": 45.0, "adx": 20.0})
        matrix = FeatureMatrix(vectors=[v1, v2], feature_names=["adx", "rsi"])

        rows = to_csv_rows(matrix)
        assert len(rows) == 2
        assert rows[0]["symbol"] == "BTCUSDT"
        assert rows[0]["timestamp"] == "2026-01-01T00:00:00"
        assert rows[0]["rsi"] == 55.0
        assert rows[0]["adx"] == 30.0
        assert rows[1]["symbol"] == "ETHUSDT"


# ─── feature_store ───────────────────────────────────────────────────────


class TestFeatureStore:
    def test_save_and_load(self, tmp_path, monkeypatch):
        from cryptobot.features import feature_store

        monkeypatch.setattr(feature_store, "FEATURES_DIR", tmp_path)

        v1 = FeatureVector("BTCUSDT", "2026-01-01T00:00:00", {"rsi": 55.0})
        matrix = FeatureMatrix(vectors=[v1], feature_names=["rsi"])

        path = feature_store.save_features(matrix)
        assert path.exists()

        loaded = feature_store.load_latest_features()
        assert loaded is not None
        assert len(loaded.vectors) == 1
        assert loaded.vectors[0].symbol == "BTCUSDT"
        assert loaded.vectors[0].features["rsi"] == 55.0
        assert loaded.feature_names == ["rsi"]

    def test_load_latest_no_files(self, tmp_path, monkeypatch):
        from cryptobot.features import feature_store

        monkeypatch.setattr(feature_store, "FEATURES_DIR", tmp_path)

        result = feature_store.load_latest_features()
        assert result is None

    def test_load_latest_nonexistent_dir(self, tmp_path, monkeypatch):
        from cryptobot.features import feature_store

        monkeypatch.setattr(feature_store, "FEATURES_DIR", tmp_path / "nonexistent")

        result = feature_store.load_latest_features()
        assert result is None

    def test_cleanup_old_features(self, tmp_path, monkeypatch):
        from cryptobot.features import feature_store

        monkeypatch.setattr(feature_store, "FEATURES_DIR", tmp_path)

        # 创建旧文件和新文件
        old_file = tmp_path / "2020-01-01.json"
        old_file.write_text(json.dumps({"vectors": [], "feature_names": []}))

        new_file = tmp_path / "2026-01-01.json"
        new_file.write_text(json.dumps({"vectors": [], "feature_names": []}))

        deleted = feature_store.cleanup_old_features(keep_days=90)
        assert deleted == 1
        assert not old_file.exists()
        assert new_file.exists()

    def test_save_atomic_write(self, tmp_path, monkeypatch):
        """验证原子写入 (先 .tmp 再 rename)"""
        from cryptobot.features import feature_store

        monkeypatch.setattr(feature_store, "FEATURES_DIR", tmp_path)

        v1 = FeatureVector("BTCUSDT", "2026-01-01T00:00:00", {"rsi": 55.0})
        matrix = FeatureMatrix(vectors=[v1], feature_names=["rsi"])

        path = feature_store.save_features(matrix)

        # .tmp 文件不应存在
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

        # .json 文件应存在且可解析
        data = json.loads(path.read_text())
        assert data["vectors"][0]["symbol"] == "BTCUSDT"


# ─── frozen dataclass ────────────────────────────────────────────────────


class TestFrozenDataclass:
    def test_feature_vector_immutable(self):
        vec = FeatureVector("A", "t1", {"x": 1.0})
        with pytest.raises(AttributeError):
            vec.symbol = "B"  # type: ignore[misc]

    def test_feature_matrix_immutable(self):
        matrix = FeatureMatrix(vectors=[], feature_names=[])
        with pytest.raises(AttributeError):
            matrix.vectors = []  # type: ignore[misc]
