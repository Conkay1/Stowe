"""First-launch copy of Stowe (com.stowe.app) data into Wellslip Desktop."""
import multiprocessing
import os
import sqlite3
import sys
from pathlib import Path

import pytest

import config


def _seed(old: Path, value: str = "SQLite ledger") -> None:
    (old / "database").mkdir(parents=True)
    con = sqlite3.connect(old / "database" / "stowe.db")
    con.execute("create table t (v text)")
    con.execute("insert into t values (?)", (value,))
    con.commit()
    con.close()
    (old / "receipts").mkdir()
    (old / "receipts" / "a1.jpg").write_bytes(b"jpeg")


def _read(db: Path):
    con = sqlite3.connect(db)
    try:
        return [r[0] for r in con.execute("select v from t")]
    finally:
        con.close()


def _snapshot(root: Path) -> dict:
    return {
        p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(config, "MIGRATION_NOTICE", None)
    config._migration_events.clear()
    yield
    config._migration_events.clear()


def test_missing_target_gets_copy(tmp_path):
    old, new = tmp_path / "Stowe", tmp_path / "Wellslip Desktop"
    _seed(old)

    assert config.migrate_stowe_data_dir(new, old) == new
    assert _read(new / "database" / "wellslip.db") == ["SQLite ledger"]
    # One database in the new folder, under the new name only.
    assert not (new / "database" / "stowe.db").exists()
    assert (new / "receipts" / "a1.jpg").read_bytes() == b"jpeg"
    assert config.MIGRATION_NOTICE is None


def test_empty_target_gets_copy(tmp_path):
    old, new = tmp_path / "Stowe", tmp_path / "Wellslip Desktop"
    _seed(old)
    new.mkdir()

    assert config.migrate_stowe_data_dir(new, old) == new
    assert _read(new / "database" / "wellslip.db") == ["SQLite ledger"]


@pytest.mark.parametrize("clutter", [".DS_Store", "desktop.ini", "Thumbs.db", "migration.log"])
def test_target_with_only_os_clutter_still_gets_copy(tmp_path, clutter):
    old, new = tmp_path / "Stowe", tmp_path / "Wellslip Desktop"
    _seed(old)
    new.mkdir()
    (new / clutter).write_text("earlier")

    assert config.migrate_stowe_data_dir(new, old) == new
    assert _read(new / "database" / "wellslip.db") == ["SQLite ledger"]
    assert (new / clutter).read_text() == "earlier"


def test_existing_target_is_never_overwritten(tmp_path):
    old, new = tmp_path / "Stowe", tmp_path / "Wellslip Desktop"
    _seed(old)
    (new / "database").mkdir(parents=True)
    (new / "database" / "wellslip.db").write_bytes(b"newer data")

    assert config.migrate_stowe_data_dir(new, old) == new
    assert (new / "database" / "wellslip.db").read_bytes() == b"newer data"
    assert not (new / "receipts").exists()


def test_missing_source_is_noop(tmp_path):
    old, new = tmp_path / "Stowe", tmp_path / "Wellslip Desktop"

    assert config.migrate_stowe_data_dir(new, old) == new
    assert not new.exists()
    assert not old.exists()


def test_source_with_only_clutter_is_noop(tmp_path):
    old, new = tmp_path / "Stowe", tmp_path / "Wellslip Desktop"
    old.mkdir()
    (old / ".DS_Store").write_bytes(b"x")

    assert config.migrate_stowe_data_dir(new, old) == new
    assert not new.exists()


def test_originals_preserved(tmp_path):
    old, new = tmp_path / "Stowe", tmp_path / "Wellslip Desktop"
    _seed(old)
    before = _snapshot(old)

    config.migrate_stowe_data_dir(new, old)

    assert _snapshot(old) == before
    assert sorted(p.name for p in tmp_path.iterdir() if not p.name.startswith(".")) == [
        "Stowe",
        "Wellslip Desktop",
    ]


def test_idempotent_second_launch(tmp_path):
    old, new = tmp_path / "Stowe", tmp_path / "Wellslip Desktop"
    _seed(old)
    config.migrate_stowe_data_dir(new, old)
    (new / "receipts" / "new.jpg").write_bytes(b"after migration")
    (old / "receipts" / "stale.jpg").write_bytes(b"stale old app write")

    assert config.migrate_stowe_data_dir(new, old) == new
    assert not (new / "receipts" / "stale.jpg").exists()
    assert (new / "receipts" / "new.jpg").exists()


def test_open_stowe_db_mid_write_is_snapshotted_consistently(tmp_path):
    old, new = tmp_path / "Stowe", tmp_path / "Wellslip Desktop"
    _seed(old)
    con = sqlite3.connect(old / "database" / "stowe.db")
    con.execute("begin")
    con.execute("insert into t values ('half-written')")
    try:
        assert config.migrate_stowe_data_dir(new, old) == new
    finally:
        con.rollback()
        con.close()
    assert _read(new / "database" / "wellslip.db") == ["SQLite ledger"]


def test_failed_copy_keeps_original_sets_notice_and_retries(tmp_path, monkeypatch):
    old, new = tmp_path / "Stowe", tmp_path / "Wellslip Desktop"
    _seed(old)
    before = _snapshot(old)
    real_copytree = config.shutil.copytree

    def broken(src, dst, **kw):
        Path(dst).mkdir(parents=True)
        (Path(dst) / "partial").write_text("x")
        raise OSError("disk full")

    monkeypatch.setattr(config.shutil, "copytree", broken)
    assert config.migrate_stowe_data_dir(new, old) == old
    assert not new.exists()
    assert not (tmp_path / ".Wellslip Desktop.migrating").exists()
    assert "Stowe folder" in config.MIGRATION_NOTICE
    assert any("disk full" in e for e in config._migration_events)
    assert _snapshot(old) == before

    monkeypatch.setattr(config.shutil, "copytree", real_copytree)
    monkeypatch.setattr(config, "MIGRATION_NOTICE", None)
    assert config.migrate_stowe_data_dir(new, old) == new
    assert _read(new / "database" / "wellslip.db") == ["SQLite ledger"]


def test_failure_after_another_launch_finished_uses_new_dir(tmp_path, monkeypatch):
    old, new = tmp_path / "Stowe", tmp_path / "Wellslip Desktop"
    _seed(old)

    def other_launch_won(src, dst, **kw):
        (new / "database").mkdir(parents=True)
        (new / "database" / "wellslip.db").write_bytes(b"winner")
        raise OSError("rename raced")

    monkeypatch.setattr(config, "_copy_stowe_tree", other_launch_won)
    assert config.migrate_stowe_data_dir(new, old) == new
    assert config.MIGRATION_NOTICE is None


def _child_migrate(new, old, q):
    sys.path.insert(0, str(Path(config.__file__).parent))
    import config as c

    q.put(str(c.migrate_stowe_data_dir(Path(new), Path(old))))


@pytest.mark.skipif(sys.platform == "win32", reason="fork-based race test")
def test_two_processes_migrating_at_once_agree(tmp_path):
    old, new = tmp_path / "Stowe", tmp_path / "Wellslip Desktop"
    _seed(old)
    ctx = multiprocessing.get_context("fork")
    q = ctx.Queue()
    procs = [ctx.Process(target=_child_migrate, args=(str(new), str(old), q)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(30)
    results = [q.get(timeout=5) for _ in procs]
    assert results == [str(new)] * 4
    assert _read(new / "database" / "wellslip.db") == ["SQLite ledger"]


def test_migration_lock_serialises(tmp_path):
    import threading

    order = []

    def worker(tag):
        with config._migration_lock(tmp_path):
            order.append(f"{tag}-in")
            order.append(f"{tag}-out")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for i in range(0, len(order), 2):
        assert order[i].split("-")[0] == order[i + 1].split("-")[0]


def test_frozen_macos_resolves_to_migrated_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))
    support = tmp_path / "Library" / "Application Support"
    _seed(support / "Stowe")

    resolved = config._resolve_data_dir()

    assert resolved == support / "Wellslip Desktop"
    assert _read(resolved / "database" / "wellslip.db") == ["SQLite ledger"]
    assert (support / "Stowe" / "database" / "stowe.db").exists()


def test_failed_copy_never_writes_into_stowe_folder(tmp_path, monkeypatch):
    """Item 1: fallback must not create wellslip.db (or a log) in the old folder."""
    old, new = tmp_path / "Stowe", tmp_path / "Wellslip Desktop"
    _seed(old)
    before = _snapshot(old)

    def broken(*a, **k):
        raise OSError("read-only volume")

    monkeypatch.setattr(config.shutil, "copytree", broken)
    data_dir = config.migrate_stowe_data_dir(new, old)
    assert data_dir == old
    db = config.resolve_database_path(
        data_dir / "database", legacy_folder=config.MIGRATION_NOTICE is not None
    )
    assert db == old / "database" / "stowe.db"
    log = config.write_migration_log(new)
    assert log == new / "migration.log"
    assert _snapshot(old) == before

    # The log alone doesn't block the retry on the next launch.
    monkeypatch.undo()
    config.MIGRATION_NOTICE = None
    assert config.migrate_stowe_data_dir(new, old) == new
    assert _read(new / "database" / "wellslip.db") == ["SQLite ledger"]
    assert "read-only volume" in (new / "migration.log").read_text()


def test_windows_data_dir_is_wellslip_desktop(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert config._user_data_dir() == tmp_path / "Local" / "Wellslip Desktop"
    assert config._legacy_stowe_data_dir() == tmp_path / "Local" / "Stowe"


def test_frozen_windows_copies_localappdata_stowe(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(os, "name", "nt")
    local = tmp_path / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    _seed(local / "Stowe")
    before = _snapshot(local / "Stowe")

    resolved = config._resolve_data_dir()

    assert resolved == local / "Wellslip Desktop"
    assert _read(resolved / "database" / "wellslip.db") == ["SQLite ledger"]
    assert _snapshot(local / "Stowe") == before


def test_frozen_windows_existing_new_dir_skips(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(os, "name", "nt")
    local = tmp_path / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    _seed(local / "Stowe")
    (local / "Wellslip Desktop" / "database").mkdir(parents=True)
    (local / "Wellslip Desktop" / "database" / "wellslip.db").write_bytes(b"new")

    resolved = config._resolve_data_dir()

    assert resolved == local / "Wellslip Desktop"
    assert not (resolved / "database" / "stowe.db").exists()
    assert (resolved / "database" / "wellslip.db").read_bytes() == b"new"


def test_data_dir_endpoint_reports_notice(client, monkeypatch):
    monkeypatch.setattr(config, "MIGRATION_NOTICE", "using the Stowe folder")
    body = client.get("/api/v1/system/data-dir").json()
    assert body["migration_notice"] == "using the Stowe folder"
    monkeypatch.setattr(config, "MIGRATION_NOTICE", None)
    assert client.get("/api/v1/system/data-dir").json()["migration_notice"] is None
