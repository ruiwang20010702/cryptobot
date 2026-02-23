"""LightGBM 信号评分模块测试"""

from unittest.mock import MagicMock, patch

import pytest

from cryptobot.ml.lgb_scorer import (
    ModelMetrics,
    SignalScore,
    _compute_auc_roc,
    _compute_label,
    _compute_metrics,
    _dicts_to_matrix,
    _find_nearest_price,
    _kfold_split,
    prepare_training_data,
)


# ─── dataclass 测试 ───────────────────────────────────────────────────


class TestDataclasses:
    def test_model_metrics_frozen(self):
        m = ModelMetrics(
            accuracy=0.8, auc_roc=0.85, precision=0.7,
            recall=0.9, f1=0.79, feature_importance={"rsi": 100.0},
        )
        assert m.accuracy == 0.8
        with pytest.raises(AttributeError):
            m.accuracy = 0.9  # type: ignore[misc]

    def test_signal_score_frozen(self):
        s = SignalScore(
            symbol="BTCUSDT", direction="up",
            probability=0.75, model_version="v1", features_used=10,
        )
        assert s.direction == "up"
        assert s.probability == 0.75


# ─── K-fold 分割 ──────────────────────────────────────────────────────


class TestKFoldSplit:
    def test_basic_split(self):
        """TimeSeriesSplit: train 始终在 val 之前"""
        folds = _kfold_split(12, 5)
        assert len(folds) == 5
        for train_idx, val_idx in folds:
            # train 和 val 无重叠
            assert set(train_idx) & set(val_idx) == set()
            # train 最大值 < val 最小值 (时序保证)
            assert max(train_idx) < min(val_idx)

    def test_train_grows_monotonically(self):
        """TimeSeriesSplit: 训练集逐步增大"""
        folds = _kfold_split(12, 5)
        train_sizes = [len(train_idx) for train_idx, _ in folds]
        for i in range(1, len(train_sizes)):
            assert train_sizes[i] > train_sizes[i - 1]

    def test_uneven_split(self):
        folds = _kfold_split(10, 3)
        assert len(folds) >= 2
        for train_idx, val_idx in folds:
            assert set(train_idx) & set(val_idx) == set()
            assert max(train_idx) < min(val_idx)


# ─── 指标计算 ─────────────────────────────────────────────────────────


class TestMetrics:
    def test_perfect_predictions(self):
        y_true = [1, 1, 0, 0, 1]
        y_pred = [1, 1, 0, 0, 1]
        y_prob = [0.9, 0.8, 0.1, 0.2, 0.95]
        m = _compute_metrics(y_true, y_pred, y_prob, ["f1"], [10.0])
        assert m.accuracy == 1.0
        assert m.precision == 1.0
        assert m.recall == 1.0
        assert m.f1 == 1.0

    def test_all_wrong(self):
        y_true = [1, 1, 0, 0]
        y_pred = [0, 0, 1, 1]
        y_prob = [0.1, 0.2, 0.9, 0.8]
        m = _compute_metrics(y_true, y_pred, y_prob, ["f1"], [5.0])
        assert m.accuracy == 0.0
        assert m.precision == 0.0
        assert m.recall == 0.0

    def test_empty(self):
        m = _compute_metrics([], [], [], [], [])
        assert m.accuracy == 0.0
        assert m.f1 == 0.0

    def test_feature_importance_mapping(self):
        m = _compute_metrics(
            [1, 0], [1, 0], [0.9, 0.1],
            ["rsi", "volume"], [100.5, 50.3],
        )
        assert m.feature_importance == {"rsi": 100.5, "volume": 50.3}


class TestAUCROC:
    def test_perfect_separation(self):
        y_true = [1, 1, 0, 0]
        y_prob = [0.9, 0.8, 0.2, 0.1]
        auc = _compute_auc_roc(y_true, y_prob)
        assert auc == 1.0

    def test_random_classifier(self):
        """全同类别 → AUC=0.5"""
        auc = _compute_auc_roc([1, 1, 1], [0.5, 0.5, 0.5])
        assert auc == 0.5

    def test_empty(self):
        assert _compute_auc_roc([], []) == 0.0


# ─── 标签计算 ─────────────────────────────────────────────────────────


class TestComputeLabel:
    def test_positive_label(self):
        # 当前价格 100, 未来价格 102 → 2% > 1% → label=1
        klines = {
            1000000: 100.0,  # 当前
            1000000 + 24 * 3600_000: 102.0,  # 24h 后
        }
        label = _compute_label("1970-01-01T00:16:40+00:00", klines, 24, 1.0)
        assert label == 1

    def test_negative_label(self):
        # 当前价格 100, 未来价格 100.5 → 0.5% < 1% → label=0
        klines = {
            1000000: 100.0,
            1000000 + 24 * 3600_000: 100.5,
        }
        label = _compute_label("1970-01-01T00:16:40+00:00", klines, 24, 1.0)
        assert label == 0

    def test_no_data(self):
        label = _compute_label("2025-01-01T00:00:00+00:00", {}, 24, 1.0)
        assert label is None

    def test_invalid_timestamp(self):
        label = _compute_label("not-a-date", {1000: 100.0}, 24, 1.0)
        assert label is None


class TestFindNearestPrice:
    def test_exact_match(self):
        klines = {1000: 50.0, 2000: 60.0}
        assert _find_nearest_price(klines, 1000, 500) == 50.0

    def test_within_tolerance(self):
        klines = {1000: 50.0}
        assert _find_nearest_price(klines, 1200, 500) == 50.0

    def test_out_of_tolerance(self):
        klines = {1000: 50.0}
        assert _find_nearest_price(klines, 2000, 500) is None

    def test_empty(self):
        assert _find_nearest_price({}, 1000, 500) is None


# ─── dicts_to_matrix ──────────────────────────────────────────────────


class TestDictsToMatrix:
    def test_basic(self):
        dicts = [{"a": 1.0, "b": 2.0}, {"a": 3.0}]
        result = _dicts_to_matrix(dicts, ["a", "b"])
        # "b" 不在 _FEATURE_DEFAULTS 中，缺失时为 None (NaN)
        assert result[0] == [1.0, 2.0]
        assert result[1][0] == 3.0
        assert result[1][1] is None

    def test_semantic_defaults(self):
        """已知语义的特征缺失时用对应默认值"""
        dicts = [{"rsi": 50.0}]
        result = _dicts_to_matrix(dicts, ["rsi", "long_short_ratio", "funding_rate"])
        assert result == [[50.0, 1.0, 0.0]]


# ─── prepare_training_data ────────────────────────────────────────────


class TestPrepareTrainingData:
    @patch("cryptobot.indicators.calculator.load_klines")
    @patch("cryptobot.features.feature_store._load_from_file")
    @patch("cryptobot.features.feature_store.FEATURES_DIR")
    def test_basic(self, mock_dir, mock_load, mock_klines):
        from cryptobot.features.pipeline import FeatureMatrix, FeatureVector

        mock_dir.exists.return_value = True

        # 创建一个模拟特征文件
        mock_file = MagicMock()
        mock_file.stem = "2025-12-01"
        mock_dir.glob.return_value = [mock_file]

        vec = FeatureVector(
            symbol="BTCUSDT",
            timestamp="2025-12-01T00:00:00+00:00",
            features={"rsi": 45.0, "volume": 100.0},
        )
        matrix = FeatureMatrix(vectors=[vec], feature_names=["rsi", "volume"])
        mock_load.return_value = matrix

        # Mock K 线数据
        import pandas as pd

        ts_base = int(
            pd.Timestamp("2025-12-01T00:00:00+00:00").timestamp() * 1000
        )
        index = pd.to_datetime([ts_base, ts_base + 24 * 3600_000], unit="ms")
        df = pd.DataFrame(
            {"close": [100.0, 102.0]},
            index=index,
        )
        mock_klines.return_value = df

        X, y = prepare_training_data(days=365)
        assert len(X) == 1
        assert len(y) == 1
        assert X[0] == {"rsi": 45.0, "volume": 100.0}
        assert y[0] == 1  # 2% > 1%

    @patch("cryptobot.features.feature_store.FEATURES_DIR")
    def test_no_features_dir(self, mock_dir):
        mock_dir.exists.return_value = False
        X, y = prepare_training_data()
        assert X == []
        assert y == []


# ─── train_model (mock lightgbm) ─────────────────────────────────────


class TestTrainModel:
    def test_train(self):
        """使用 mock lightgbm 测试训练流程"""
        import lightgbm as lgb

        mock_booster = MagicMock()
        mock_booster.predict.return_value = [0.8, 0.3]
        mock_booster.feature_importance.return_value = [10.0, 5.0]
        mock_booster.feature_name.return_value = ["rsi", "volume"]

        X = [
            {"rsi": 45.0, "volume": 100.0},
            {"rsi": 55.0, "volume": 200.0},
            {"rsi": 35.0, "volume": 150.0},
            {"rsi": 65.0, "volume": 80.0},
            {"rsi": 50.0, "volume": 120.0},
        ]
        y = [1, 0, 1, 0, 1]

        with (
            patch.object(lgb, "Dataset", return_value=MagicMock()),
            patch.object(lgb, "train", return_value=mock_booster),
            patch.object(lgb, "early_stopping", return_value=MagicMock()),
        ):
            from cryptobot.ml.lgb_scorer import train_model

            model, metrics = train_model(X, y, n_folds=5)
            assert model is mock_booster
            assert isinstance(metrics, ModelMetrics)
            assert lgb.train.called

    def test_too_few_samples(self):
        X = [{"rsi": 1.0}]
        y = [1]
        with pytest.raises(ValueError, match="样本数"):
            from cryptobot.ml.lgb_scorer import train_model
            train_model(X, y, n_folds=5)


# ─── score_signal ─────────────────────────────────────────────────────


class TestScoreSignal:
    def test_score(self):
        from cryptobot.ml.lgb_scorer import score_signal

        mock_model = MagicMock()
        mock_model.feature_name.return_value = ["rsi", "volume"]
        mock_model.predict.return_value = [0.75]

        result = score_signal(
            "BTCUSDT",
            {"rsi": 45.0, "volume": 100.0},
            model=mock_model,
        )
        assert isinstance(result, SignalScore)
        assert result.symbol == "BTCUSDT"
        assert result.direction == "up"
        assert result.probability == 0.75
        assert result.features_used == 2

    def test_score_down(self):
        from cryptobot.ml.lgb_scorer import score_signal

        mock_model = MagicMock()
        mock_model.feature_name.return_value = ["rsi"]
        mock_model.predict.return_value = [0.2]  # < 0.3 → down

        result = score_signal("ETHUSDT", {"rsi": 70.0}, model=mock_model)
        assert result.direction == "down"
        assert result.probability == 0.2

    def test_score_neutral(self):
        """0.3 <= prob <= 0.5 → neutral (更保守的做空标准)"""
        from cryptobot.ml.lgb_scorer import score_signal

        mock_model = MagicMock()
        mock_model.feature_name.return_value = ["rsi"]
        mock_model.predict.return_value = [0.4]

        result = score_signal("ETHUSDT", {"rsi": 70.0}, model=mock_model)
        assert result.direction == "neutral"
        assert result.probability == 0.4

    def test_missing_features(self):
        from cryptobot.ml.lgb_scorer import score_signal

        mock_model = MagicMock()
        mock_model.feature_name.return_value = ["rsi", "volume", "macd"]
        mock_model.predict.return_value = [0.6]

        result = score_signal("BTCUSDT", {"rsi": 45.0}, model=mock_model)
        assert result.features_used == 1  # 只有 rsi 匹配


# ─── 模型持久化 ───────────────────────────────────────────────────────


class TestModelPersistence:
    def test_save_model(self, tmp_path):
        from cryptobot.ml.lgb_scorer import save_model

        mock_model = MagicMock()

        with patch("cryptobot.ml.lgb_scorer.MODELS_DIR", tmp_path):
            path = save_model(mock_model, "v20250101")
            mock_model.save_model.assert_called_once_with(
                str(tmp_path / "v20250101.txt")
            )
            assert "v20250101" in path

    def test_load_latest_model(self, tmp_path):
        # 创建模拟模型文件
        (tmp_path / "v1.txt").write_text("model1")
        (tmp_path / "v2.txt").write_text("model2")

        mock_booster = MagicMock()

        with (
            patch("cryptobot.ml.lgb_scorer.MODELS_DIR", tmp_path),
            patch("cryptobot.ml.lgb_scorer.lgb", create=True),
        ):
            import sys

            mock_lgb_module = MagicMock()
            mock_lgb_module.Booster.return_value = mock_booster

            with patch.dict(sys.modules, {"lightgbm": mock_lgb_module}):
                from cryptobot.ml.lgb_scorer import load_latest_model

                model, version = load_latest_model()
                assert model is mock_booster

    def test_load_no_dir(self, tmp_path):
        nonexistent = tmp_path / "nonexistent"

        with patch("cryptobot.ml.lgb_scorer.MODELS_DIR", nonexistent):
            from cryptobot.ml.lgb_scorer import load_latest_model

            with pytest.raises(FileNotFoundError, match="模型目录不存在"):
                load_latest_model()

    def test_load_empty_dir(self, tmp_path):
        with patch("cryptobot.ml.lgb_scorer.MODELS_DIR", tmp_path):
            from cryptobot.ml.lgb_scorer import load_latest_model

            with pytest.raises(FileNotFoundError, match="无可用模型"):
                load_latest_model()


