import contextlib
import logging
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

# Pick the lock primitive by what the platform has (not os.name, which tests patch).
try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

APP_NAME = "Stowe"
# Releases from the Wellslip rebrand on keep their data in a Wellslip-named
# folder on every platform. The first launch copies the old Stowe folder.
NEW_APP_NAME = "Wellslip Desktop"
DATABASE_FILENAME = "wellslip.db"
LEGACY_DATABASE_FILENAME = "stowe.db"
BASE_DIR = Path(__file__).parent
logger = logging.getLogger("stowe.config")


def _os_path(value: str) -> Path:
    """Path for ``value`` using the host separator, not a patched ``os.name``.

    ``pathlib.Path`` follows ``os.name``. Tests select the Windows branch by
    patching ``os.name`` to ``"nt"`` on POSIX; those path objects still need
    the host flavour so joins and filesystem calls keep working.
    """
    if os.sep == "\\":
        return pathlib.WindowsPath(value)
    return pathlib.PosixPath(value)


def _user_data_dir() -> Path:
    # Local, not Roaming: domain profiles and folder redirection copy
    # %APPDATA% to a file server. Health and tax records must stay on the PC.
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or str(
            _os_path(os.path.expanduser("~")) / "AppData" / "Local"
        )
        return _os_path(local) / NEW_APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / NEW_APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / "wellslip-desktop"


def _legacy_stowe_data_dir() -> Path:
    """Data folder used by Stowe (bundle ID com.stowe.app) before the rebrand."""
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or str(
            _os_path(os.path.expanduser("~")) / "AppData" / "Local"
        )
        return _os_path(local) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / APP_NAME.lower()


# Files the OS drops into folders on its own. A data folder holding only these
# (or our own migration.log) still counts as empty.
_IGNORABLE_NAMES = frozenset({".DS_Store", "desktop.ini", "Thumbs.db", "migration.log"})
MIGRATION_LOG_NAME = "migration.log"

# Set when the app is running on the old Stowe folder because the copy failed.
# Shown once per launch in the app (see /api/v1/system/data-dir).
MIGRATION_NOTICE: str | None = None
_migration_events: list[str] = []


def _note(level: int, msg: str, *args) -> None:
    """Log ``msg`` and keep it for migration.log."""
    logger.log(level, msg, *args)
    try:
        text = msg % args if args else msg
    except Exception:
        text = msg
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    _migration_events.append(f"{stamp} {logging.getLevelName(level)} {text}")


def write_migration_log(target_dir: Path) -> Path | None:
    """Append this launch's migration events to ``target_dir/migration.log``.

    Falls back to the temp folder if ``target_dir`` can't be written. Writes
    nothing when no migration step ran. Never call this with the old Stowe
    folder: that folder must stay untouched.
    """
    if not _migration_events:
        return None
    lines = "".join(f"{line}\n" for line in _migration_events)
    for folder in (target_dir, _os_path(tempfile.gettempdir())):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / MIGRATION_LOG_NAME
            if folder != target_dir:
                path = folder / "wellslip-desktop-migration.log"
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(lines)
            _migration_events.clear()
            return path
        except OSError:
            continue
    return None


@contextlib.contextmanager
def _migration_lock(folder: Path):
    """Hold an exclusive lock so two launches can't migrate at the same time.

    Uses ``folder/.wellslip-migration.lock``. If the lock can't be taken (for
    example a read-only folder) the migration runs unlocked rather than not at
    all; the staging and re-check logic still keep data safe.
    """
    fh = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        fh = open(folder / ".wellslip-migration.lock", "a+b")
        if fcntl is None:
            while True:
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    # LK_LOCK gives up after ~10 s; keep waiting.
                    continue
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except OSError as exc:
        logger.warning("Could not lock %s for migration (%s); continuing", folder, exc)
        if fh is not None:
            fh.close()
            fh = None
    try:
        yield
    finally:
        if fh is not None:
            try:
                if fcntl is None:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            fh.close()


def _is_empty_dir(path: Path) -> bool:
    """True if ``path`` is a folder with nothing in it but OS clutter."""
    try:
        return path.is_dir() and all(
            child.name in _IGNORABLE_NAMES for child in path.iterdir()
        )
    except OSError:
        return False


def _has_data(path: Path) -> bool:
    return path.is_dir() and not _is_empty_dir(path)


def snapshot_sqlite(src: Path, dst: Path) -> None:
    """Copy the SQLite database ``src`` to ``dst`` with SQLite's backup API.

    The source is opened read-only and the backup takes a consistent
    snapshot, so a database another process (an old Stowe still running) is
    writing to is never copied half-written, and any -wal contents are folded
    in. ``dst`` must not exist. Raises on any error or a failed check.
    """
    source = sqlite3.connect(f"{src.resolve().as_uri()}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(str(dst))
        try:
            source.backup(target)
            result = target.execute("PRAGMA quick_check").fetchone()
            if not result or result[0] != "ok":
                raise sqlite3.DatabaseError(f"quick_check failed: {result}")
        finally:
            target.close()
    finally:
        source.close()
    if os.name != "nt":
        os.chmod(dst, 0o600)


def _remove_sqlite_sidecars(db_file: Path) -> None:
    """Delete stray -wal/-shm/-journal files next to ``db_file``.

    Left over from an interrupted earlier attempt, they would be replayed
    into a freshly placed database and could corrupt it.
    """
    for suffix in ("-wal", "-shm", "-journal"):
        side = db_file.with_name(db_file.name + suffix)
        if side.exists():
            side.unlink()
            _note(logging.WARNING, "Removed stray %s before placing %s", side, db_file)


def _copy_stowe_tree(old_dir: Path, staging: Path) -> None:
    """Copy ``old_dir`` to ``staging``, snapshotting the database.

    Everything but the SQLite files is copied as-is. ``database/stowe.db`` is
    snapshotted straight to ``database/wellslip.db``, so the new folder holds
    one database under the new name.
    """
    old_db_dir = old_dir / "database"

    def skip_sqlite(folder: str, names: list[str]) -> set[str]:
        if not _same_path(_os_path(folder), old_db_dir):
            return set()
        return {n for n in names if n == LEGACY_DATABASE_FILENAME or n.startswith(
            LEGACY_DATABASE_FILENAME + "-")}

    shutil.copytree(old_dir, staging, symlinks=True, ignore=skip_sqlite)
    old_db = old_db_dir / LEGACY_DATABASE_FILENAME
    if old_db.is_file():
        (staging / "database").mkdir(parents=True, exist_ok=True)
        snapshot_sqlite(old_db, staging / "database" / DATABASE_FILENAME)


def migrate_stowe_data_dir(new_dir: Path, old_dir: Path | None = None) -> Path:
    """Copy Stowe data into the Wellslip Desktop data folder on first launch.

    Runs only when ``new_dir`` is missing or empty (OS clutter aside) and
    ``old_dir`` holds data. Receipts and every other file are copied; the
    database is snapshotted with SQLite's backup API to
    ``database/wellslip.db``. The original folder is never modified or
    deleted, and existing data in ``new_dir`` is never overwritten. The copy
    goes to a staging folder first and is renamed into place only when
    complete, so an interrupted copy is retried next launch. A file lock keeps
    two launches from migrating at once.

    Returns the directory the app should use: ``new_dir`` normally, or
    ``old_dir`` if the copy failed (the app keeps working on the original and
    sets ``MIGRATION_NOTICE``).
    """
    global MIGRATION_NOTICE
    if old_dir is None:
        old_dir = _legacy_stowe_data_dir()

    if _same_path(old_dir, new_dir) or not _has_data(old_dir) or _has_data(new_dir):
        return new_dir

    staging = new_dir.parent / f".{new_dir.name}.migrating"
    try:
        with _migration_lock(new_dir.parent):
            # Another launch may have finished while we waited for the lock.
            if _has_data(new_dir):
                return new_dir
            if staging.exists():
                shutil.rmtree(staging)
            _copy_stowe_tree(old_dir, staging)
            if new_dir.exists():
                # Only OS clutter or an earlier migration.log is here: carry
                # it over so the log keeps its history, then swap the folder.
                for child in new_dir.iterdir():
                    dest = staging / child.name
                    if not dest.exists():
                        shutil.move(str(child), str(dest))
                    else:
                        child.unlink()
                new_dir.rmdir()
            os.replace(staging, new_dir)
    except Exception as exc:
        try:
            if staging.exists():
                shutil.rmtree(staging)
        except OSError:
            pass
        if _has_data(new_dir):
            # Lost a race with another launch that completed the copy.
            _note(logging.WARNING, "Stowe data copy hit %s, but %s is now complete; using it", exc, new_dir)
            return new_dir
        _note(
            logging.ERROR,
            "Could not copy Stowe data from %s to %s (%s); using the original folder",
            old_dir,
            new_dir,
            exc,
        )
        MIGRATION_NOTICE = (
            "Wellslip Desktop couldn't copy your Stowe data to its new folder, so it is "
            f"using the Stowe folder ({old_dir}) for now. It will try again next launch. "
            f"Details are in {new_dir / MIGRATION_LOG_NAME}."
        )
        return old_dir

    _note(
        logging.INFO,
        "Copied Stowe data from %s to %s; original left in place",
        old_dir,
        new_dir,
    )
    return new_dir


def _windows_roaming_data_dir() -> Path:
    appdata = os.environ.get("APPDATA") or str(
        _os_path(os.path.expanduser("~")) / "AppData" / "Roaming"
    )
    return _os_path(appdata) / APP_NAME


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


def _discard_partial_copy(new_dir: Path, old_dir: Path) -> None:
    """Remove an incomplete destination when the source directory is still intact.

    A failed move can leave a partial tree at ``new_dir``. Leaving it in place
    would make the next launch treat that partial tree as the data directory
    and ignore the complete original. The original is never deleted here.
    """
    if not new_dir.exists() or not old_dir.exists() or _same_path(new_dir, old_dir):
        return
    try:
        shutil.rmtree(new_dir)
    except Exception as exc:
        logger.error(
            "Could not remove incomplete Stowe data at %s (%s); original remains at %s",
            new_dir,
            exc,
            old_dir,
        )
    else:
        logger.warning(
            "Removed incomplete Stowe data at %s after a failed migration; original remains at %s",
            new_dir,
            old_dir,
        )


def migrate_roaming_data_dir(new_dir: Path, old_dir: Path | None = None) -> Path:
    """Move legacy ``%APPDATA%\\Stowe`` data to ``%LOCALAPPDATA%\\Stowe``.

    Returns the directory the app should use. If ``new_dir`` already exists,
    it is left alone. If only ``old_dir`` exists, it is moved; when the move
    fails the tree is copied and the original is left in place. Data is never
    deleted when migration fails.
    """
    if old_dir is None:
        old_dir = _windows_roaming_data_dir()

    if _same_path(old_dir, new_dir) or new_dir.exists() or not old_dir.is_dir():
        return new_dir

    try:
        new_dir.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error(
            "Could not create %s (%s); keeping Stowe data at %s",
            new_dir.parent,
            exc,
            old_dir,
        )
        return old_dir

    try:
        shutil.move(str(old_dir), str(new_dir))
    except Exception as exc:
        logger.warning(
            "Could not move Stowe data from %s to %s (%s); copying instead",
            old_dir,
            new_dir,
            exc,
        )
    else:
        logger.info("Moved Stowe data from %s to %s", old_dir, new_dir)
        return new_dir

    # shutil.move deletes the source only after a successful copy. If the
    # original is already gone, the destination is the only remaining copy.
    if not old_dir.exists():
        logger.error(
            "Move of Stowe data from %s did not complete cleanly and the original is gone; using %s",
            old_dir,
            new_dir,
        )
        return new_dir

    _discard_partial_copy(new_dir, old_dir)
    if new_dir.exists():
        logger.error(
            "Incomplete Stowe data remains at %s and could not be removed; keeping the original at %s",
            new_dir,
            old_dir,
        )
        return old_dir

    try:
        shutil.copytree(old_dir, new_dir)
    except Exception as exc:
        logger.error(
            "Failed to copy Stowe data from %s to %s (%s); keeping the original at %s",
            old_dir,
            new_dir,
            exc,
            old_dir,
        )
        _discard_partial_copy(new_dir, old_dir)
        return old_dir if old_dir.exists() else new_dir

    logger.info(
        "Copied Stowe data from %s to %s; original left in place",
        old_dir,
        new_dir,
    )
    return new_dir


def _resolve_data_dir() -> Path:
    if not getattr(sys, "frozen", False):
        return BASE_DIR
    data_dir = _user_data_dir()
    if os.name == "nt":
        # Older builds used %APPDATA%\Stowe; move that to %LOCALAPPDATA%\Stowe
        # first, then copy Stowe's folder into %LOCALAPPDATA%\Wellslip Desktop.
        legacy = migrate_roaming_data_dir(_legacy_stowe_data_dir())
        return migrate_stowe_data_dir(data_dir, legacy)
    return migrate_stowe_data_dir(data_dir)


def _is_repo_root(path: Path) -> bool:
    try:
        return path.resolve() == BASE_DIR.resolve()
    except OSError:
        return False


def ensure_private_dir(path: Path) -> None:
    """Create a directory. On POSIX, restrict it to the owner (mode 0o700).

    Windows ignores the mkdir mode and chmod is skipped there. The repository
    root (the from-source data directory) is never chmod'd.
    """
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "nt" or _is_repo_root(path):
        return
    try:
        os.chmod(path, 0o700)
    except OSError as exc:
        logger.warning("Could not set permissions 0o700 on %s: %s", path, exc)


def restrict_private_file(path: Path) -> None:
    """On POSIX, restrict a file to the owner (mode 0o600). No-op on Windows."""
    if os.name == "nt" or not path.is_file():
        return
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        logger.warning("Could not set permissions 0o600 on %s: %s", path, exc)


def resolve_database_path(db_dir: Path, *, legacy_folder: bool = False) -> Path:
    """Return the database file to open in ``db_dir``.

    Uses ``wellslip.db``. If it is missing and a Stowe-era ``stowe.db`` exists,
    snapshots ``stowe.db`` to the new name with SQLite's backup API (which
    also folds in any -wal contents) and leaves the original in place. Stray
    ``wellslip.db`` sidecars from an interrupted attempt are removed before
    the new file is placed. If the copy fails, the app falls back to reading
    ``stowe.db`` so no data is hidden. An existing ``wellslip.db`` is never
    overwritten.

    With ``legacy_folder=True`` (the app is running on the old Stowe folder
    after a failed folder copy) nothing is written: ``stowe.db`` is used in
    place so the Stowe folder stays untouched.
    """
    new = db_dir / DATABASE_FILENAME
    old = db_dir / LEGACY_DATABASE_FILENAME
    if legacy_folder:
        return old if old.is_file() or not new.is_file() else new
    if new.exists() or not old.is_file():
        return new
    tmp = db_dir / f".{DATABASE_FILENAME}.migrating"
    try:
        with _migration_lock(db_dir):
            if new.exists():
                return new
            if tmp.exists():
                tmp.unlink()
            snapshot_sqlite(old, tmp)
            _remove_sqlite_sidecars(new)
            os.replace(tmp, new)
    except Exception as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        if new.exists():
            return new
        _note(
            logging.ERROR,
            "Could not copy %s to %s (%s); reading the Stowe database in place",
            old,
            new,
            exc,
        )
        return old
    _note(logging.INFO, "Copied %s to %s; original left in place", old, new)
    return new


# When packaged with PyInstaller, write user data to a platform-standard
# writable location instead of inside the read-only .app bundle: a
# "Wellslip Desktop" folder (see _user_data_dir). Stowe-era folders and the
# stowe.db file are copied to the new names on first launch.
DATA_DIR = _resolve_data_dir()
# True when the folder copy failed and the app is on the untouched Stowe
# folder: then nothing may be written there, so stowe.db is used in place.
RUNNING_ON_STOWE_FOLDER = MIGRATION_NOTICE is not None

DATABASE_PATH = resolve_database_path(
    DATA_DIR / "database", legacy_folder=RUNNING_ON_STOWE_FOLDER
)
RECEIPTS_DIR = DATA_DIR / "receipts"
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"
# migration.log goes in the Wellslip Desktop folder (never the Stowe one), or
# next to the database when running from source. Only written if a step ran.
write_migration_log(
    _user_data_dir() if getattr(sys, "frozen", False) else DATA_DIR / "database"
)

HSA_CATEGORIES = [
    "Medical",
    "Pharmacy",
    "Dental",
    "Vision",
    "Mental Health",
    "Medical Equipment",
    "Other",
]
