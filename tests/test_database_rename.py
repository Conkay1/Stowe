"""stowe.db -> wellslip.db on first launch (packaged, Windows and from source)."""
import config


def test_fresh_install_uses_wellslip_db(tmp_path):
    assert config.resolve_database_path(tmp_path) == tmp_path / "wellslip.db"
    assert list(tmp_path.iterdir()) == []


def test_copies_stowe_db_and_sidecars_keeping_originals(tmp_path):
    (tmp_path / "stowe.db").write_bytes(b"main")
    (tmp_path / "stowe.db-wal").write_bytes(b"wal")

    path = config.resolve_database_path(tmp_path)

    assert path == tmp_path / "wellslip.db"
    assert path.read_bytes() == b"main"
    assert (tmp_path / "wellslip.db-wal").read_bytes() == b"wal"
    assert (tmp_path / "stowe.db").read_bytes() == b"main"
    assert (tmp_path / "stowe.db-wal").read_bytes() == b"wal"
    assert not list(tmp_path.glob(".*migrating"))


def test_existing_wellslip_db_never_overwritten(tmp_path):
    (tmp_path / "stowe.db").write_bytes(b"old")
    (tmp_path / "wellslip.db").write_bytes(b"new")

    assert config.resolve_database_path(tmp_path).read_bytes() == b"new"


def test_idempotent(tmp_path):
    (tmp_path / "stowe.db").write_bytes(b"old")
    first = config.resolve_database_path(tmp_path)
    first.write_bytes(b"edited")
    assert config.resolve_database_path(tmp_path).read_bytes() == b"edited"


def test_copy_failure_falls_back_to_stowe_db(tmp_path, monkeypatch):
    (tmp_path / "stowe.db").write_bytes(b"old")

    def boom(*a, **k):
        raise OSError("read-only")

    monkeypatch.setattr(config.shutil, "copy2", boom)
    assert config.resolve_database_path(tmp_path) == tmp_path / "stowe.db"
    assert not (tmp_path / "wellslip.db").exists()
    assert (tmp_path / "stowe.db").read_bytes() == b"old"


def test_real_sqlite_database_survives_copy(tmp_path):
    import sqlite3

    con = sqlite3.connect(tmp_path / "stowe.db")
    con.execute("create table t (v text)")
    con.execute("insert into t values ('receipt')")
    con.commit()
    con.close()

    path = config.resolve_database_path(tmp_path)
    con = sqlite3.connect(path)
    assert con.execute("select v from t").fetchall() == [("receipt",)]
    con.close()
