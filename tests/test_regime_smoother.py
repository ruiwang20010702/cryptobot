"""市场状态转换平滑测试"""

import json

from cryptobot.regime_smoother import smooth_regime_transition, _HISTORY_PATH


def _write_history(history: dict):
    _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _HISTORY_PATH.write_text(json.dumps(history))


def _cleanup():
    if _HISTORY_PATH.exists():
        _HISTORY_PATH.unlink()


class TestSmoothRegimeTransition:
    def setup_method(self):
        _cleanup()

    def teardown_method(self):
        _cleanup()

    def test_same_regime_no_change(self):
        """检测到同一 regime 不切换"""
        _write_history({
            "current_regime": "trending",
            "pending_transition": None,
            "last_updated": "2026-01-01T00:00:00+00:00",
        })
        regime, changed = smooth_regime_transition("trending", confirm_cycles=2)
        assert regime == "trending"
        assert changed is False

    def test_different_regime_first_detection(self):
        """首次检测到不同 regime，不切换"""
        _write_history({
            "current_regime": "trending",
            "pending_transition": None,
            "last_updated": "2026-01-01T00:00:00+00:00",
        })
        regime, changed = smooth_regime_transition("ranging", confirm_cycles=2)
        assert regime == "trending"
        assert changed is False

        # 验证 pending 被记录
        history = json.loads(_HISTORY_PATH.read_text())
        assert history["pending_transition"]["to"] == "ranging"
        assert history["pending_transition"]["count"] == 1

    def test_confirm_after_n_cycles(self):
        """连续 N 次检测后确认切换"""
        _write_history({
            "current_regime": "trending",
            "pending_transition": {"to": "ranging", "count": 1},
            "last_updated": "2026-01-01T00:00:00+00:00",
        })
        regime, changed = smooth_regime_transition("ranging", confirm_cycles=2)
        assert regime == "ranging"
        assert changed is True

        # 切换完成后 pending 被清空
        history = json.loads(_HISTORY_PATH.read_text())
        assert history["current_regime"] == "ranging"
        assert history["pending_transition"] is None

    def test_pending_reset_on_same_regime(self):
        """回到当前 regime 时清空 pending"""
        _write_history({
            "current_regime": "trending",
            "pending_transition": {"to": "ranging", "count": 1},
            "last_updated": "2026-01-01T00:00:00+00:00",
        })
        regime, changed = smooth_regime_transition("trending", confirm_cycles=2)
        assert regime == "trending"
        assert changed is False

        history = json.loads(_HISTORY_PATH.read_text())
        assert history["pending_transition"] is None

    def test_pending_reset_on_different_new_regime(self):
        """检测到第三种 regime 时重置 pending"""
        _write_history({
            "current_regime": "trending",
            "pending_transition": {"to": "ranging", "count": 1},
            "last_updated": "2026-01-01T00:00:00+00:00",
        })
        regime, changed = smooth_regime_transition("volatile", confirm_cycles=2)
        assert regime == "trending"
        assert changed is False

        history = json.loads(_HISTORY_PATH.read_text())
        assert history["pending_transition"]["to"] == "volatile"
        assert history["pending_transition"]["count"] == 1

    def test_volatile_upgrade_skips_smoothing(self):
        """volatile 升级跳过平滑"""
        _write_history({
            "current_regime": "trending",
            "pending_transition": None,
            "last_updated": "2026-01-01T00:00:00+00:00",
        })
        regime, changed = smooth_regime_transition(
            "volatile", confirm_cycles=2, is_volatile_upgrade=True,
        )
        assert regime == "volatile"
        assert changed is False  # 跳过平滑，不算"平滑切换"

    def test_simulation_mode_skips_smoothing(self):
        """模拟模式跳过平滑"""
        _write_history({
            "current_regime": "trending",
            "pending_transition": None,
            "last_updated": "2026-01-01T00:00:00+00:00",
        })
        regime, changed = smooth_regime_transition(
            "ranging", confirm_cycles=2, is_simulation=True,
        )
        assert regime == "ranging"
        assert changed is False

    def test_no_history_file_uses_default(self):
        """无历史文件时使用默认 ranging"""
        regime, changed = smooth_regime_transition("trending", confirm_cycles=2)
        assert regime == "ranging"  # 默认是 ranging，首次检测到 trending 不立即切换
        assert changed is False

    def test_confirm_cycles_1(self):
        """confirm_cycles=1 时立即切换"""
        _write_history({
            "current_regime": "trending",
            "pending_transition": None,
            "last_updated": "2026-01-01T00:00:00+00:00",
        })
        regime, changed = smooth_regime_transition("ranging", confirm_cycles=1)
        assert regime == "ranging"
        assert changed is True
