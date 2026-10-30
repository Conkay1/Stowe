"""First-launch copy of Stowe (com.stowe.app) data into Wellslip Desktop."""
import os
import sys
from pathlib import Path

import config


def _seed(old: Path) -> None:
    (old / "database").mkdir(parents=True)
    (old / "database" / "stowe.db").write_bytes(b"SQLite ledger")
    (old / "receipts").mkdir()
    (old / "receipts" / "a1.jpg").write_bytes(b"jpeg")


def _snapshot(root: Path) -> dict:
    return {
        p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }


def test_missing_target_gets_copy(tmp_path):
    old, new = tmp_path / "Stowe", tmp_path / "Wellslip Desktop"
    _seed(old)

    assert config.migrate_stowe_data_dir(new, old) == new
    assert (new / "database" / "stowe.db").read_bytes() == b"SQLite ledger"
    assert (new / "receipts" / "a1.jpg").read_bytes() == b"jpeg"


def test_empty_target_gets_copy(tmp_path):
    old, new = tmp_path / "Stowe", tmp_path / "Wellslip Desktop"
    _seed(old)
    new.mkdir()

    assert config.migrate_stowe_data_dir(new, old) == new
    assert (new / "database" / "stowe.db").read_bytes() == b"SQLite ledger"


def test_existing_target_is_never_overwritten(tmp_path):
    old, new = tmp_path / "Stowe", tmp_path / "Wellslip Desktop"
    _seed(old)
    (new / "database").mkdir(parents=True)
    (new / "database" / "stowe.db").write_bytes(b"newer data")

    assert config.migrate_stowe_data_dir(new, old) == new
    assert (new / "database" / "stowe.db").read_bytes() == b"newer data"
    assert not (new / "receipts").exists()


def test_missing_source_is_noop(tmp_path):
    old, new = tmp_path / "Stowe", tmp_path / "Wellslip Desktop"

    assert config.migrate_stowe_data_dir(new, old) == new
    assert not new.exists()
    assert not old.exists()


def test_empty_source_is_noop(tmp_path):
    old, new = tmp_path / "Stowe", tmp_path / "Wellslip Desktop"
    old.mkdir()

    assert config.migrate_stowe_data_dir(new, old) == new
    assert not new.exists()


def test_originals_preserved(tmp_path):
    old, new = tmp_path / "Stowe", tmp_path / "Wellslip Desktop"
    _seed(old)
    before = _snapshot(old)

    config.migrate_stowe_data_dir(new, old)

    assert _snapshot(old) == before


def test_idempotent_second_launch(tmp_path):
    old, new = tmp_path / "Stowe", tmp_path / "Wellslip Desktop"
    _seed(old)
    config.migrate_stowe_data_dir(new, old)
    (new / "database" / "stowe.db").write_bytes(b"edited after migration")
    (old / "database" / "stowe.db").write_bytes(b"stale old app write")

    assert config.migrate_stowe_data_dir(new, old) == new
    assert (new / "database" / "stowe.db").read_bytes() == b"edited after migration"


def test_failed_copy_keeps_original_and_retries(tmp_path, monkeypatch):
    old, new = tmp_path / "Stowe", tmp_path / "Wellslip Desktop"
    _seed(old)
    real_copytree = config.shutil.copytree

    def broken(src, dst, **kw):
        Path(dst).mkdir(parents=True)
        (Path(dst) / "partial").write_text("x")
        raise OSError("disk full")

    monkeypatch.setattr(config.shutil, "copytree", broken)
    assert config.migrate_stowe_data_dir(new, old) == old
    assert not new.exists()
    assert not (tmp_path / ".Wellslip Desktop.migrating").exists()

    monkeypatch.setattr(config.shutil, "copytree", real_copytree)
    assert config.migrate_stowe_data_dir(new, old) == new
    assert (new / "database" / "stowe.db").read_bytes() == b"SQLite ledger"


def test_frozen_macos_resolves_to_migrated_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))
    support = tmp_path / "Library" / "Application Support"
    _seed(support / "Stowe")

    resolved = config._resolve_data_dir()

    assert resolved == support / "Wellslip Desktop"
    assert (resolved / "database" / "stowe.db").read_bytes() == b"SQLite ledger"
    assert (support / "Stowe" / "database" / "stowe.db").exists()


def test_windows_data_dir_name_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert config._user_data_dir().name == "Stowe"
