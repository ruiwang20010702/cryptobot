"""缓存清理测试"""

import time


from cryptobot.cache import cleanup_stale


class TestCleanupStale:
    def test_removes_old_files(self, tmp_path, monkeypatch):
        """超龄文件被删除"""
        import cryptobot.cache as cache_mod
        monkeypatch.setattr(cache_mod, "DATA_OUTPUT_DIR", tmp_path)

        # 创建 .cache 子目录和文件
        cache_dir = tmp_path / ".cache"
        cache_dir.mkdir()
        old_file = cache_dir / "old.json"
        old_file.write_text("{}")
        # 设置 mtime 为 4 天前
        import os
        old_mtime = time.time() - 4 * 24 * 3600
        os.utime(old_file, (old_mtime, old_mtime))

        new_file = cache_dir / "new.json"
        new_file.write_text("{}")

        removed = cleanup_stale(max_age_hours=72)
        assert removed == 1
        assert not old_file.exists()
        assert new_file.exists()

    def test_no_files_returns_zero(self, tmp_path, monkeypatch):
        """无超龄文件返回 0"""
        import cryptobot.cache as cache_mod
        monkeypatch.setattr(cache_mod, "DATA_OUTPUT_DIR", tmp_path)

        removed = cleanup_stale(max_age_hours=72)
        assert removed == 0

    def test_skips_non_json(self, tmp_path, monkeypatch):
        """非 JSON 文件不删除"""
        import cryptobot.cache as cache_mod
        monkeypatch.setattr(cache_mod, "DATA_OUTPUT_DIR", tmp_path)

        cache_dir = tmp_path / ".cache"
        cache_dir.mkdir()
        txt_file = cache_dir / "notes.txt"
        txt_file.write_text("keep me")
        import os
        old_mtime = time.time() - 5 * 24 * 3600
        os.utime(txt_file, (old_mtime, old_mtime))

        removed = cleanup_stale(max_age_hours=72)
        assert removed == 0
        assert txt_file.exists()

    def test_multiple_subdirs(self, tmp_path, monkeypatch):
        """多个缓存子目录"""
        import cryptobot.cache as cache_mod
        monkeypatch.setattr(cache_mod, "DATA_OUTPUT_DIR", tmp_path)

        import os
        old_mtime = time.time() - 5 * 24 * 3600

        for subdir in [".cache", "dxy", "whale"]:
            d = tmp_path / subdir
            d.mkdir()
            f = d / "data.json"
            f.write_text("{}")
            os.utime(f, (old_mtime, old_mtime))

        removed = cleanup_stale(max_age_hours=72)
        assert removed == 3
