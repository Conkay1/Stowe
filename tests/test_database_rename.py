"""stowe.db -> wellslip.db on first launch (packaged, Windows and from source)."""
import sqlite3
import threading

import pytest

import config


def _make_db(path, value="receipt", wal=False):
    con = sqlite3.connect(path)
    if wal:
        con.execute("pragma journal_mode=wal")
    con.execute("create table t (v text)")
    con.execute("insert into t values (?)", (value,))
    con.commit()
    return con


def _read(path):
    con = sqlite3.connect(path)
    try:
        return [r[0] for r in con.execute("select v from t")]
    finally:
        con.close()


@pytest.fixture(autouse=True)
def _clear_events():
    config._migration_events.clear()
    yield
    config._migration_events.clear()


def test_fresh_install_uses_wellslip_db(tmp_path):
    assert config.resolve_database_path(tmp_path) == tmp_path / "wellslip.db"
    assert list(tmp_path.iterdir()) == []


def test_snapshot_keeps_original_and_leaves_no_temp(tmp_path):
    _make_db(tmp_path / "stowe.db").close()
    before = (tmp_path / "stowe.db").read_bytes()

    path = config.resolve_database_path(tmp_path)

    assert path == tmp_path / "wellslip.db"
    assert _read(path) == ["receipt"]
    assert (tmp_path / "stowe.db").read_bytes() == before
    assert not list(tmp_path.glob(".*migrating"))


def test_wal_contents_are_included(tmp_path):
    # Writer still open: committed rows live only in stowe.db-wal.
    con = _make_db(tmp_path / "stowe.db", wal=True)
    con.execute("insert into t values ('in wal')")
    con.commit()
    assert (tmp_path / "stowe.db-wal").stat().st_size > 0
    try:
        path = config.resolve_database_path(tmp_path)
    finally:
        con.close()
    assert sorted(_read(path)) == ["in wal", "receipt"]
    assert not (tmp_path / "wellslip.db-wal").exists()


def test_open_writer_mid_transaction_gives_consistent_snapshot(tmp_path):
    con = _make_db(tmp_path / "stowe.db", wal=True)
    con.execute("begin")
    con.execute("insert into t values ('uncommitted')")
    try:
        path = config.resolve_database_path(tmp_path)
    finally:
        con.rollback()
        con.close()
    assert _read(path) == ["receipt"]


def test_stray_sidecars_removed_before_placing(tmp_path):
    _make_db(tmp_path / "stowe.db").close()
    (tmp_path / "wellslip.db-wal").write_bytes(b"stale wal from a crash")
    (tmp_path / "wellslip.db-journal").write_bytes(b"stale journal")

    path = config.resolve_database_path(tmp_path)

    assert not (tmp_path / "wellslip.db-journal").exists()
    assert _read(path) == ["receipt"]
    con = sqlite3.connect(path)
    assert con.execute("pragma integrity_check").fetchone()[0] == "ok"
    con.close()


def test_existing_wellslip_db_never_overwritten(tmp_path):
    _make_db(tmp_path / "stowe.db", "old").close()
    _make_db(tmp_path / "wellslip.db", "new").close()

    assert _read(config.resolve_database_path(tmp_path)) == ["new"]


def test_idempotent(tmp_path):
    _make_db(tmp_path / "stowe.db", "old").close()
    first = config.resolve_database_path(tmp_path)
    con = sqlite3.connect(first)
    con.execute("update t set v = 'edited'")
    con.commit()
    con.close()
    assert _read(config.resolve_database_path(tmp_path)) == ["edited"]


def test_copy_failure_falls_back_to_stowe_db(tmp_path, monkeypatch):
    _make_db(tmp_path / "stowe.db", "old").close()

    def boom(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(config, "snapshot_sqlite", boom)
    assert config.resolve_database_path(tmp_path) == tmp_path / "stowe.db"
    assert not (tmp_path / "wellslip.db").exists()
    assert not list(tmp_path.glob(".*migrating"))
    assert any("disk I/O error" in e for e in config._migration_events)


def test_not_a_database_falls_back(tmp_path):
    (tmp_path / "stowe.db").write_bytes(b"not sqlite")
    assert config.resolve_database_path(tmp_path) == tmp_path / "stowe.db"
    assert not (tmp_path / "wellslip.db").exists()


def test_legacy_folder_mode_writes_nothing(tmp_path):
    _make_db(tmp_path / "stowe.db").close()
    before = sorted(p.name for p in tmp_path.iterdir())

    path = config.resolve_database_path(tmp_path, legacy_folder=True)

    assert path == tmp_path / "stowe.db"
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_concurrent_resolves_agree(tmp_path):
    _make_db(tmp_path / "stowe.db").close()
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(config.resolve_database_path(tmp_path)))
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [tmp_path / "wellslip.db"] * 4
    assert _read(tmp_path / "wellslip.db") == ["receipt"]


def test_migration_log_written_and_temp_fallback(tmp_path, monkeypatch):
    config._note(config.logging.INFO, "hello %s", "log")
    path = config.write_migration_log(tmp_path / "Wellslip Desktop")
    assert path == tmp_path / "Wellslip Desktop" / "migration.log"
    assert "hello log" in path.read_text()
    assert config.write_migration_log(tmp_path / "x") is None  # nothing new

    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setattr(config.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    config._note(config.logging.ERROR, "fallback")
    path = config.write_migration_log(blocker / "sub")
    assert path == tmp_path / "tmp" / "wellslip-desktop-migration.log"
