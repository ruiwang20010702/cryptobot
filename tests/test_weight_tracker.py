"""策略权重管理测试"""

from cryptobot.strategy.weight_tracker import (
    StrategyWeight,
    WeightAllocation,
    get_weights,
    load_weights,
    save_weights,
    WEIGHTS_PATH,
)


def test_get_weights_trending():
    alloc = get_weights("trending")
    assert alloc.regime == "trending"
    by_name = {w.strategy: w.weight for w in alloc.weights}
    assert by_name["ai_trend"] == 0.8
    assert by_name["mean_reversion"] == 0.0
    assert by_name["grid"] == 0.2


def test_get_weights_ranging():
    alloc = get_weights("ranging")
    assert alloc.regime == "ranging"
    by_name = {w.strategy: w.weight for w in alloc.weights}
    assert by_name["mean_reversion"] == 0.5
    assert by_name["grid"] == 0.3
    assert by_name["ai_trend"] == 0.2


def test_get_weights_volatile():
    alloc = get_weights("volatile")
    assert alloc.regime == "volatile"
    assert all(w.weight == 0.0 for w in alloc.weights)


def test_save_load_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "strategy_weights.json"
    monkeypatch.setattr(
        "cryptobot.strategy.weight_tracker.WEIGHTS_PATH", path,
    )

    alloc = WeightAllocation(
        regime="ranging",
        weights=[
            StrategyWeight("ai_trend", 0.3, "test"),
            StrategyWeight("mean_reversion", 0.7, "test"),
        ],
        updated_at="2026-01-01T00:00:00+00:00",
    )
    save_weights(alloc)
    loaded = load_weights()

    assert loaded is not None
    assert loaded.regime == "ranging"
    assert len(loaded.weights) == 2
    assert loaded.weights[0].weight == 0.3
    assert loaded.weights[1].weight == 0.7


def test_load_weights_no_file(tmp_path, monkeypatch):
    path = tmp_path / "nonexistent.json"
    monkeypatch.setattr(
        "cryptobot.strategy.weight_tracker.WEIGHTS_PATH", path,
    )
    assert load_weights() is None


def test_immutability():
    sw = StrategyWeight("ai_trend", 0.8, "test")
    try:
        sw.weight = 0.5  # type: ignore
        raise AssertionError("Should be frozen")
    except AttributeError:
        pass

    alloc = WeightAllocation(
        regime="trending",
        weights=[sw],
        updated_at="2026-01-01T00:00:00+00:00",
    )
    try:
        alloc.regime = "ranging"  # type: ignore
        raise AssertionError("Should be frozen")
    except AttributeError:
        pass


# ─── P14: volatile 子状态权重 ──────────────────────────────


def test_get_weights_volatile_normal():
    alloc = get_weights("volatile_normal")
    assert alloc.regime == "volatile_normal"
    by_name = {w.strategy: w.weight for w in alloc.weights}
    assert by_name["ai_trend"] == 0.3
    assert by_name["grid"] == 0.2


def test_get_weights_volatile_fear():
    alloc = get_weights("volatile_fear")
    assert alloc.regime == "volatile_fear"
    by_name = {w.strategy: w.weight for w in alloc.weights}
    assert by_name["funding_arb"] == 0.6
    assert by_name["grid"] == 0.2


def test_get_weights_volatile_greed():
    alloc = get_weights("volatile_greed")
    assert alloc.regime == "volatile_greed"
    by_name = {w.strategy: w.weight for w in alloc.weights}
    assert by_name["ai_trend"] == 0.4
    assert len(alloc.weights) == 1
