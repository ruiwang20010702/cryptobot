"""Volatile 策略自适应开关测试"""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from cryptobot.evolution.volatile_toggle import (
    VolatileToggleState,
    _load_state,
    _save_state,
    evaluate_toggle,
    is_volatile_strategy_enabled,
    record_volatile_cycle,
)


@pytest.fixture(autouse=True)
def _clean_state(tmp_path, monkeypatch):
    """每个测试使用独立的状态文件"""
    state_path = tmp_path / "volatile_toggle_state.json"
    monkeypatch.setattr(
        "cryptobot.evolution.volatile_toggle._STATE_PATH", state_path,
    )
    return state_path


class TestRecordVolatileCycle:
    def test_record_increments_observe_count(self):
        """volatile + observe → 累加计数"""
        state = record_volatile_cycle("volatile", True)
        assert state.consecutive_observe == 1
        state = record_volatile_cycle("volatile", True)
        assert state.consecutive_observe == 2
        state = record_volatile_cycle("volatile", True)
        assert state.consecutive_observe == 3

    def test_record_resets_on_non_volatile(self):
        """非 volatile → 重置计数"""
        record_volatile_cycle("volatile", True)
        record_volatile_cycle("volatile", True)
        state = record_volatile_cycle("trending", False)
        assert state.consecutive_observe == 0

    def test_record_resets_on_volatile_non_observe(self):
        """volatile 但非 observe (策略已启用) → 重置"""
        record_volatile_cycle("volatile", True)
        state = record_volatile_cycle("volatile", False)
        assert state.consecutive_observe == 0

    def test_record_preserves_enabled_flag(self):
        """记录不改变 enabled 状态"""
        _save_state(VolatileToggleState(enabled=True))
        state = record_volatile_cycle("volatile", True)
        assert state.enabled is True


class TestEvaluateToggle:
    def test_evaluate_enables_after_3_observe(self):
        """连续 3 轮观望 → 启用"""
        settings = {"volatile_strategy": {"auto": True, "auto_enable_observe_cycles": 3}}
        # 先累计 3 轮
        record_volatile_cycle("volatile", True)
        record_volatile_cycle("volatile", True)
        record_volatile_cycle("volatile", True)

        with patch("cryptobot.evolution.volatile_toggle._count_virtual_pnl_positive_days", return_value=0), \
             patch("cryptobot.evolution.volatile_toggle._count_subtype_loss_streak", return_value=0), \
             patch("cryptobot.evolution.volatile_toggle._check_14d_volatile_pnl_negative", return_value=False), \
             patch("cryptobot.evolution.volatile_toggle.load_settings", return_value=settings):
            state = evaluate_toggle(settings)

        assert state.enabled is True

    def test_evaluate_disables_after_5_losses(self):
        """连续 5 笔亏损 → 禁用"""
        settings = {"volatile_strategy": {"auto": True, "auto_disable_loss_streak": 5}}
        # 先设为启用
        _save_state(VolatileToggleState(enabled=True))

        with patch("cryptobot.evolution.volatile_toggle._count_virtual_pnl_positive_days", return_value=0), \
             patch("cryptobot.evolution.volatile_toggle._count_subtype_loss_streak", return_value=5), \
             patch("cryptobot.evolution.volatile_toggle._check_14d_volatile_pnl_negative", return_value=False), \
             patch("cryptobot.evolution.volatile_toggle.load_settings", return_value=settings):
            state = evaluate_toggle(settings)

        assert state.enabled is False

    def test_evaluate_enables_on_virtual_pnl(self):
        """虚拟盘正收益 → 启用"""
        settings = {"volatile_strategy": {"auto": True}}

        with patch("cryptobot.evolution.volatile_toggle._count_virtual_pnl_positive_days", return_value=4), \
             patch("cryptobot.evolution.volatile_toggle._count_subtype_loss_streak", return_value=0), \
             patch("cryptobot.evolution.volatile_toggle._check_14d_volatile_pnl_negative", return_value=False), \
             patch("cryptobot.evolution.volatile_toggle.load_settings", return_value=settings):
            state = evaluate_toggle(settings)

        assert state.enabled is True

    def test_evaluate_disables_on_negative_14d(self):
        """14 天净 PnL 为负 → 禁用"""
        settings = {"volatile_strategy": {"auto": True}}
        _save_state(VolatileToggleState(enabled=True))

        with patch("cryptobot.evolution.volatile_toggle._count_virtual_pnl_positive_days", return_value=0), \
             patch("cryptobot.evolution.volatile_toggle._count_subtype_loss_streak", return_value=2), \
             patch("cryptobot.evolution.volatile_toggle._check_14d_volatile_pnl_negative", return_value=True), \
             patch("cryptobot.evolution.volatile_toggle.load_settings", return_value=settings):
            state = evaluate_toggle(settings)

        assert state.enabled is False

    def test_evaluate_no_change_when_already_disabled(self):
        """条件不满足时不变"""
        settings = {"volatile_strategy": {"auto": True, "auto_enable_observe_cycles": 3}}

        with patch("cryptobot.evolution.volatile_toggle._count_virtual_pnl_positive_days", return_value=0), \
             patch("cryptobot.evolution.volatile_toggle._count_subtype_loss_streak", return_value=0), \
             patch("cryptobot.evolution.volatile_toggle._check_14d_volatile_pnl_negative", return_value=False), \
             patch("cryptobot.evolution.volatile_toggle.load_settings", return_value=settings):
            state = evaluate_toggle(settings)

        assert state.enabled is False


class TestManualMode:
    def test_manual_mode_ignores_auto(self):
        """auto=false 走手动 enabled 字段"""
        settings = {"volatile_strategy": {"auto": False, "enabled": True}}
        assert is_volatile_strategy_enabled(settings) is True

    def test_manual_mode_disabled(self):
        """auto=false, enabled=false → 不启用"""
        settings = {"volatile_strategy": {"auto": False, "enabled": False}}
        assert is_volatile_strategy_enabled(settings) is False

    def test_auto_mode_reads_state_file(self):
        """auto=true → 读状态文件"""
        _save_state(VolatileToggleState(enabled=True))
        settings = {"volatile_strategy": {"auto": True, "enabled": False}}
        assert is_volatile_strategy_enabled(settings) is True

    def test_auto_mode_default_disabled(self):
        """auto=true, 状态文件不存在 → 默认 False"""
        settings = {"volatile_strategy": {"auto": True}}
        assert is_volatile_strategy_enabled(settings) is False

    def test_evaluate_skipped_in_manual(self):
        """auto=false 时 evaluate_toggle 不做任何变更"""
        settings = {"volatile_strategy": {"auto": False, "enabled": True}}
        _save_state(VolatileToggleState(enabled=False, consecutive_observe=10))
        state = evaluate_toggle(settings)
        # 手动模式直接返回当前状态，不变更
        assert state.enabled is False
        assert state.consecutive_observe == 10


class TestPersistence:
    def test_state_persistence_roundtrip(self, _clean_state):
        """持久化读写往返"""
        state = VolatileToggleState(
            enabled=True,
            consecutive_observe=5,
            virtual_pnl_positive_days=3,
            subtype_loss_streak=2,
            last_evaluated="2026-01-01T00:00:00",
            toggle_history=[{"action": "enabled", "reason": "test", "at": "2026-01-01"}],
        )
        _save_state(state)
        loaded = _load_state()

        assert loaded.enabled == state.enabled
        assert loaded.consecutive_observe == state.consecutive_observe
        assert loaded.virtual_pnl_positive_days == state.virtual_pnl_positive_days
        assert loaded.subtype_loss_streak == state.subtype_loss_streak
        assert loaded.last_evaluated == state.last_evaluated
        assert len(loaded.toggle_history) == 1

    def test_toggle_history_appended(self):
        """历史记录追加"""
        settings = {"volatile_strategy": {"auto": True, "auto_enable_observe_cycles": 1}}
        record_volatile_cycle("volatile", True)

        with patch("cryptobot.evolution.volatile_toggle._count_virtual_pnl_positive_days", return_value=0), \
             patch("cryptobot.evolution.volatile_toggle._count_subtype_loss_streak", return_value=0), \
             patch("cryptobot.evolution.volatile_toggle._check_14d_volatile_pnl_negative", return_value=False), \
             patch("cryptobot.evolution.volatile_toggle.load_settings", return_value=settings):
            state = evaluate_toggle(settings)

        assert state.enabled is True
        assert len(state.toggle_history) == 1
        assert state.toggle_history[0]["action"] == "enabled"
        assert "reason" in state.toggle_history[0]
        assert "at" in state.toggle_history[0]

    def test_corrupted_file_fallback(self, _clean_state):
        """损坏的文件 → 使用默认值"""
        _clean_state.write_text("not valid json{{{")
        state = _load_state()
        assert state.enabled is False
        assert state.consecutive_observe == 0
