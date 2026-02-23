"""ML 模型重训 + 版本注册表测试"""

from unittest.mock import MagicMock, patch

import pytest

from cryptobot.ml.lgb_scorer import ModelMetrics
from cryptobot.ml.registry import (
    ModelRecord,
    get_active_model,
    load_registry,
    save_registry,
)
from cryptobot.ml.retrainer import RetrainResult, run_retrain


# ─── Registry 测试 ──────────────────────────────────────────────────


class TestRegistry:
    def test_load_empty(self, tmp_path):
        path = tmp_path / "registry.json"
        with patch("cryptobot.ml.registry.REGISTRY_PATH", path):
            records = load_registry()
            assert records == []

    def test_save_and_load(self, tmp_path):
        path = tmp_path / "registry.json"
        records = [
            ModelRecord(
                version="v1",
                created_at="2026-01-01T00:00:00",
                metrics={"auc_roc": 0.8},
                training_samples=100,
                status="active",
            ),
        ]
        with patch("cryptobot.ml.registry.REGISTRY_PATH", path):
            save_registry(records)
            loaded = load_registry()

        assert len(loaded) == 1
        assert loaded[0].version == "v1"
        assert loaded[0].status == "active"
        assert loaded[0].metrics == {"auc_roc": 0.8}

    def test_atomic_write(self, tmp_path):
        """确认 .tmp 文件已被 rename 清理"""
        path = tmp_path / "registry.json"
        with patch("cryptobot.ml.registry.REGISTRY_PATH", path):
            save_registry([])
        assert path.exists()
        assert not path.with_suffix(".json.tmp").exists()

    def test_get_active_model(self, tmp_path):
        path = tmp_path / "registry.json"
        records = [
            ModelRecord("v1", "t1", {"auc_roc": 0.7}, 100, "superseded"),
            ModelRecord("v2", "t2", {"auc_roc": 0.8}, 120, "active"),
        ]
        with patch("cryptobot.ml.registry.REGISTRY_PATH", path):
            save_registry(records)
            active = get_active_model()

        assert active is not None
        assert active.version == "v2"

    def test_get_active_model_none(self, tmp_path):
        path = tmp_path / "registry.json"
        with patch("cryptobot.ml.registry.REGISTRY_PATH", path):
            assert get_active_model() is None

    def test_load_corrupt_file(self, tmp_path):
        path = tmp_path / "registry.json"
        path.write_text("not json")
        with patch("cryptobot.ml.registry.REGISTRY_PATH", path):
            records = load_registry()
            assert records == []


# ─── Retrainer 测试 ─────────────────────────────────────────────────


def _mock_metrics(auc: float) -> ModelMetrics:
    return ModelMetrics(
        accuracy=0.8,
        auc_roc=auc,
        precision=0.7,
        recall=0.75,
        f1=0.72,
        feature_importance={"rsi": 10.0},
    )


class TestRunRetrain:
    def test_skipped_insufficient_samples(self, tmp_path):
        """样本不足时跳过"""
        reg_path = tmp_path / "registry.json"
        with (
            patch("cryptobot.ml.retrainer.prepare_training_data", return_value=([], [])),
            patch("cryptobot.ml.registry.REGISTRY_PATH", reg_path),
        ):
            result = run_retrain(min_samples=50)
        assert result.action == "skipped"
        assert "样本不足" in result.reason

    def test_first_model(self, tmp_path):
        """首次训练（无旧模型）-> first_model"""
        reg_path = tmp_path / "registry.json"
        mock_model = MagicMock()
        metrics = _mock_metrics(0.82)
        X = [{"rsi": i} for i in range(60)]
        y = [i % 2 for i in range(60)]

        with (
            patch("cryptobot.ml.retrainer.prepare_training_data", return_value=(X, y)),
            patch("cryptobot.ml.retrainer.train_model", return_value=(mock_model, metrics)),
            patch("cryptobot.ml.retrainer.save_model") as mock_save,
            patch("cryptobot.ml.registry.REGISTRY_PATH", reg_path),
        ):
            result = run_retrain(min_samples=50)

        assert result.action == "first_model"
        assert result.metrics["auc_roc"] == 0.82
        assert result.previous_version is None
        mock_save.assert_called_once()

        # 注册表应有一条 active 记录
        with patch("cryptobot.ml.registry.REGISTRY_PATH", reg_path):
            records = load_registry()
        assert len(records) == 1
        assert records[0].status == "active"

    def test_deployed_better_model(self, tmp_path):
        """新模型更好 -> deployed"""
        reg_path = tmp_path / "registry.json"

        # 预置旧模型
        old_record = ModelRecord(
            version="v_old",
            created_at="2026-01-01T00:00:00",
            metrics={"auc_roc": 0.80, "f1": 0.70},
            training_samples=100,
            status="active",
        )
        with patch("cryptobot.ml.registry.REGISTRY_PATH", reg_path):
            save_registry([old_record])

        mock_model = MagicMock()
        new_metrics = _mock_metrics(0.85)
        X = [{"rsi": i} for i in range(60)]
        y = [i % 2 for i in range(60)]

        with (
            patch("cryptobot.ml.retrainer.prepare_training_data", return_value=(X, y)),
            patch("cryptobot.ml.retrainer.train_model", return_value=(mock_model, new_metrics)),
            patch("cryptobot.ml.retrainer.save_model"),
            patch("cryptobot.ml.registry.REGISTRY_PATH", reg_path),
        ):
            result = run_retrain(min_samples=50)

        assert result.action == "deployed"
        assert result.previous_version == "v_old"
        assert result.metrics["auc_roc"] == 0.85

        # 注册表: 旧 superseded, 新 active
        with patch("cryptobot.ml.registry.REGISTRY_PATH", reg_path):
            records = load_registry()
        assert len(records) == 2
        assert records[0].status == "superseded"
        assert records[1].status == "active"

    def test_rolled_back_worse_model(self, tmp_path):
        """新模型退化严重 -> rolled_back"""
        reg_path = tmp_path / "registry.json"

        old_record = ModelRecord(
            version="v_old",
            created_at="2026-01-01T00:00:00",
            metrics={"auc_roc": 0.85, "f1": 0.75},
            training_samples=100,
            status="active",
        )
        with patch("cryptobot.ml.registry.REGISTRY_PATH", reg_path):
            save_registry([old_record])

        mock_model = MagicMock()
        # AUC 0.80 < 0.85 - 0.02 = 0.83 -> 回滚
        bad_metrics = _mock_metrics(0.80)
        X = [{"rsi": i} for i in range(60)]
        y = [i % 2 for i in range(60)]

        with (
            patch("cryptobot.ml.retrainer.prepare_training_data", return_value=(X, y)),
            patch("cryptobot.ml.retrainer.train_model", return_value=(mock_model, bad_metrics)),
            patch("cryptobot.ml.retrainer.save_model") as mock_save,
            patch("cryptobot.ml.registry.REGISTRY_PATH", reg_path),
        ):
            result = run_retrain(min_samples=50, rollback_threshold=0.02)

        assert result.action == "rolled_back"
        assert "已回滚" in result.reason
        assert result.previous_version == "v_old"
        mock_save.assert_not_called()

        # 注册表: 旧保持 active, 新标记 rolled_back
        with patch("cryptobot.ml.registry.REGISTRY_PATH", reg_path):
            records = load_registry()
        assert len(records) == 2
        assert records[0].status == "active"
        assert records[1].status == "rolled_back"

    def test_deployed_within_threshold(self, tmp_path):
        """新模型略差但在阈值内 -> 仍然 deployed"""
        reg_path = tmp_path / "registry.json"

        old_record = ModelRecord(
            version="v_old",
            created_at="2026-01-01T00:00:00",
            metrics={"auc_roc": 0.85, "f1": 0.75},
            training_samples=100,
            status="active",
        )
        with patch("cryptobot.ml.registry.REGISTRY_PATH", reg_path):
            save_registry([old_record])

        mock_model = MagicMock()
        # AUC 0.84 >= 0.85 - 0.02 = 0.83 -> 部署
        ok_metrics = _mock_metrics(0.84)
        X = [{"rsi": i} for i in range(60)]
        y = [i % 2 for i in range(60)]

        with (
            patch("cryptobot.ml.retrainer.prepare_training_data", return_value=(X, y)),
            patch("cryptobot.ml.retrainer.train_model", return_value=(mock_model, ok_metrics)),
            patch("cryptobot.ml.retrainer.save_model"),
            patch("cryptobot.ml.registry.REGISTRY_PATH", reg_path),
        ):
            result = run_retrain(min_samples=50, rollback_threshold=0.02)

        assert result.action == "deployed"


# ─── get_model_history 测试 ─────────────────────────────────────────


class TestGetModelHistory:
    def test_returns_dicts(self, tmp_path):
        reg_path = tmp_path / "registry.json"
        records = [
            ModelRecord("v1", "t1", {"auc_roc": 0.8}, 100, "superseded"),
            ModelRecord("v2", "t2", {"auc_roc": 0.85}, 120, "active"),
        ]
        with patch("cryptobot.ml.registry.REGISTRY_PATH", reg_path):
            save_registry(records)

        with patch("cryptobot.ml.registry.REGISTRY_PATH", reg_path):
            from cryptobot.ml.retrainer import get_model_history
            history = get_model_history()

        assert len(history) == 2
        assert history[0]["version"] == "v1"
        assert history[1]["status"] == "active"
        assert isinstance(history[0], dict)


# ─── RetrainResult 不可变 ───────────────────────────────────────────


class TestRetrainResult:
    def test_frozen(self):
        r = RetrainResult(
            version="v1",
            metrics={"auc_roc": 0.8},
            previous_version=None,
            previous_metrics=None,
            action="first_model",
            reason="test",
        )
        with pytest.raises(AttributeError):
            r.action = "deployed"  # type: ignore[misc]
