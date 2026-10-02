"""Frozen Windows data directory: LocalAppData, roaming migration, permissions."""
import logging
import os
import stat
import sys
from pathlib import Path, PosixPath

import config


def _seed_legacy(old: Path) -> None:
    (old / "database").mkdir(parents=True)
    (old / "database" / "stowe.db").write_text("ledger")
    (old / "receipts").mkdir()
    (old / "receipts" / "scan.png").write_bytes(b"png")


def test_windows_path_uses_localappdata_not_roaming(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "name", "nt")
    local = tmp_path / "Local"
    roaming = tmp_path / "Roaming"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))

    assert config._user_data_dir() == local / "Stowe"


def test_windows_path_falls_back_when_localappdata_unset(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("APPDATA", "/tmp/roaming-should-not-be-used")

    assert config._user_data_dir() == (
        PosixPath(os.path.expanduser("~")) / "AppData" / "Local" / "Stowe"
    )


def test_posix_path_uses_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(sys, "platform", "linux")
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))

    assert config._user_data_dir() == xdg / "stowe"


def test_darwin_path_uses_application_support(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(sys, "platform", "darwin")

    assert config._user_data_dir() == (
        Path.home() / "Library" / "Application Support" / "Stowe"
    )


def test_source_checkout_does_not_migrate(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))

    assert config._resolve_data_dir() == config.BASE_DIR


def test_frozen_windows_migrates_roaming_dir_on_resolve(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(os, "name", "nt")
    local = tmp_path / "Local"
    roaming = tmp_path / "Roaming"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    _seed_legacy(roaming / "Stowe")

    resolved = config._resolve_data_dir()

    assert resolved == local / "Stowe"
    assert (resolved / "database" / "stowe.db").read_text() == "ledger"
    assert (resolved / "receipts" / "scan.png").read_bytes() == b"png"
    assert not (roaming / "Stowe").exists()


def test_migrate_moves_legacy_tree(tmp_path, caplog):
    old = tmp_path / "Roaming" / "Stowe"
    new = tmp_path / "Local" / "Stowe"
    _seed_legacy(old)

    with caplog.at_level(logging.INFO, logger="stowe.config"):
        result = config.migrate_roaming_data_dir(new, old)

    assert result == new
    assert not old.exists()
    assert (new / "database" / "stowe.db").read_text() == "ledger"
    assert (new / "receipts" / "scan.png").read_bytes() == b"png"
    assert f"Moved Stowe data from {old} to {new}" in caplog.text


def test_migrate_leaves_both_alone_when_new_dir_exists(tmp_path, caplog):
    old = tmp_path / "Roaming" / "Stowe"
    new = tmp_path / "Local" / "Stowe"
    _seed_legacy(old)
    new.mkdir(parents=True)
    (new / "database").mkdir()
    (new / "database" / "stowe.db").write_text("already-local")

    with caplog.at_level(logging.INFO, logger="stowe.config"):
        result = config.migrate_roaming_data_dir(new, old)

    assert result == new
    assert (old / "database" / "stowe.db").read_text() == "ledger"
    assert (new / "database" / "stowe.db").read_text() == "already-local"
    assert "Moved Stowe data" not in caplog.text
    assert "Copied Stowe data" not in caplog.text


def test_migrate_noop_when_legacy_dir_missing(tmp_path):
    old = tmp_path / "Roaming" / "Stowe"
    new = tmp_path / "Local" / "Stowe"

    assert config.migrate_roaming_data_dir(new, old) == new
    assert not old.exists()
    assert not new.exists()


def test_migrate_same_path_does_not_destroy_data(tmp_path):
    folder = tmp_path / "Stowe"
    _seed_legacy(folder)

    assert config.migrate_roaming_data_dir(folder, folder) == folder
    assert (folder / "database" / "stowe.db").read_text() == "ledger"


def test_migrate_copies_when_move_fails(tmp_path, monkeypatch, caplog):
    old = tmp_path / "Roaming" / "Stowe"
    new = tmp_path / "Local" / "Stowe"
    _seed_legacy(old)

    def fail_move(src, dst):
        raise OSError("cross-device link")

    monkeypatch.setattr(config.shutil, "move", fail_move)

    with caplog.at_level(logging.INFO, logger="stowe.config"):
        result = config.migrate_roaming_data_dir(new, old)

    assert result == new
    assert (old / "database" / "stowe.db").read_text() == "ledger"
    assert (new / "database" / "stowe.db").read_text() == "ledger"
    assert (new / "receipts" / "scan.png").read_bytes() == b"png"
    assert "copying instead" in caplog.text
    assert "original left in place" in caplog.text


def test_migrate_copies_after_partial_move(tmp_path, monkeypatch):
    old = tmp_path / "Roaming" / "Stowe"
    new = tmp_path / "Local" / "Stowe"
    _seed_legacy(old)

    def partial_move(src, dst):
        dest = Path(dst)
        dest.mkdir(parents=True)
        (dest / "partial").write_text("incomplete")
        raise OSError("disk full")

    monkeypatch.setattr(config.shutil, "move", partial_move)
    result = config.migrate_roaming_data_dir(new, old)

    assert result == new
    assert (old / "database" / "stowe.db").read_text() == "ledger"
    assert (new / "database" / "stowe.db").read_text() == "ledger"
    assert not (new / "partial").exists()


def test_migrate_keeps_original_when_move_and_copy_fail(tmp_path, monkeypatch, caplog):
    old = tmp_path / "Roaming" / "Stowe"
    new = tmp_path / "Local" / "Stowe"
    _seed_legacy(old)

    def fail_move(src, dst):
        raise OSError("cannot move")

    def fail_copy(src, dst):
        raise OSError("cannot copy")

    monkeypatch.setattr(config.shutil, "move", fail_move)
    monkeypatch.setattr(config.shutil, "copytree", fail_copy)

    with caplog.at_level(logging.INFO, logger="stowe.config"):
        result = config.migrate_roaming_data_dir(new, old)

    assert result == old
    assert (old / "database" / "stowe.db").read_text() == "ledger"
    assert (old / "receipts" / "scan.png").read_bytes() == b"png"
    assert not new.exists()
    assert "keeping the original" in caplog.text


def test_migrate_does_not_delete_original_when_copy_leaves_partial(tmp_path, monkeypatch, caplog):
    old = tmp_path / "Roaming" / "Stowe"
    new = tmp_path / "Local" / "Stowe"
    _seed_legacy(old)
    removed = []
    real_rmtree = config.shutil.rmtree

    def spy_rmtree(path, *args, **kwargs):
        removed.append(Path(path).resolve())
        return real_rmtree(path, *args, **kwargs)

    def fail_move(src, dst):
        raise OSError("cannot move")

    def partial_copy(src, dst):
        dest = Path(dst)
        dest.mkdir(parents=True)
        (dest / "partial").write_text("incomplete")
        raise OSError("copy aborted")

    monkeypatch.setattr(config.shutil, "rmtree", spy_rmtree)
    monkeypatch.setattr(config.shutil, "move", fail_move)
    monkeypatch.setattr(config.shutil, "copytree", partial_copy)

    with caplog.at_level(logging.INFO, logger="stowe.config"):
        result = config.migrate_roaming_data_dir(new, old)

    assert result == old
    assert (old / "database" / "stowe.db").read_text() == "ledger"
    assert not new.exists()
    assert old.resolve() not in removed
    assert "keeping the original" in caplog.text


def test_ensure_private_dir_and_database_file_on_posix(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    data_dir = tmp_path / "Stowe"
    config.ensure_private_dir(data_dir)
    db_file = data_dir / "stowe.db"
    db_file.write_text("sqlite")
    os.chmod(db_file, 0o644)

    config.restrict_private_file(db_file)

    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(db_file.stat().st_mode) == 0o600


def test_permissions_skip_chmod_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    data_dir = tmp_path / "Stowe"
    data_dir.mkdir()
    db_file = data_dir / "stowe.db"
    db_file.write_text("sqlite")
    os.chmod(db_file, 0o644)
    before = stat.S_IMODE(db_file.stat().st_mode)

    calls = []
    monkeypatch.setattr(os, "chmod", lambda *args, **kwargs: calls.append(args))
    config.ensure_private_dir(data_dir)
    config.restrict_private_file(db_file)

    assert data_dir.is_dir()
    assert calls == []
    assert stat.S_IMODE(db_file.stat().st_mode) == before


def test_ensure_private_dir_does_not_chmod_repo_root(monkeypatch):
    calls = []
    monkeypatch.setattr(os, "chmod", lambda *args, **kwargs: calls.append(args))

    config.ensure_private_dir(config.BASE_DIR)

    assert calls == []
