"""Prompt Manager 单元测试"""

import json

import pytest


@pytest.fixture
def pm_setup(tmp_path, monkeypatch):
    """设置临时目录"""
    import cryptobot.evolution.prompt_manager as pm

    versions_dir = tmp_path / "evolution"
    versions_dir.mkdir()
    monkeypatch.setattr(pm, "_VERSIONS_DIR", versions_dir)
    monkeypatch.setattr(pm, "_VERSIONS_FILE", versions_dir / "prompt_versions.json")
    return pm


class TestPromptManager:
    def test_default_version(self, pm_setup):
        pm = pm_setup
        assert pm.get_active_version() == "v1.0"

    def test_list_versions_default(self, pm_setup):
        pm = pm_setup
        data = pm.list_versions()
        assert data["active_version"] == "v1.0"
        assert "v1.0" in data["versions"]

    def test_create_version(self, pm_setup):
        pm = pm_setup
        ver = pm.create_version("测试版本", {"TRADER": "额外提示"})
        assert ver == "v1.1"
        detail = pm.get_version_detail(ver)
        assert detail["note"] == "测试版本"
        assert detail["addons"]["TRADER"] == "额外提示"

    def test_create_version_increments(self, pm_setup):
        pm = pm_setup
        v1 = pm.create_version("v1")
        v2 = pm.create_version("v2")
        assert v1 == "v1.1"
        assert v2 == "v1.2"

    def test_activate_version(self, pm_setup):
        pm = pm_setup
        pm.create_version("新版本")
        assert pm.activate_version("v1.1")
        assert pm.get_active_version() == "v1.1"

    def test_activate_nonexistent(self, pm_setup):
        pm = pm_setup
        assert not pm.activate_version("v99.99")

    def test_get_prompt_addon(self, pm_setup):
        pm = pm_setup
        pm.create_version("带 addon", {"TRADER": "测试 addon"})
        pm.activate_version("v1.1")
        assert pm.get_prompt_addon("TRADER") == "测试 addon"
        assert pm.get_prompt_addon("NONEXIST") == ""

    def test_get_prompt_addon_empty(self, pm_setup):
        pm = pm_setup
        assert pm.get_prompt_addon("TRADER") == ""

    def test_get_version_detail_nonexistent(self, pm_setup):
        pm = pm_setup
        assert pm.get_version_detail("v99") is None

    def test_atomic_write(self, pm_setup):
        pm = pm_setup
        pm.create_version("test")
        # 验证 tmp 文件不残留
        tmp_file = pm._VERSIONS_FILE.with_suffix(".json.tmp")
        assert not tmp_file.exists()
        # 验证文件可正常读取
        data = json.loads(pm._VERSIONS_FILE.read_text())
        assert "v1.1" in data["versions"]
