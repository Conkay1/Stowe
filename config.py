import logging
import os
import pathlib
import shutil
import sys
from pathlib import Path

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


def _is_empty_dir(path: Path) -> bool:
    try:
        return path.is_dir() and not any(path.iterdir())
    except OSError:
        return False


def migrate_stowe_data_dir(new_dir: Path, old_dir: Path | None = None) -> Path:
    """Copy Stowe data into the Wellslip Desktop data folder on first launch.

    Runs only when ``new_dir`` is missing or empty and ``old_dir`` holds data.
    The whole tree is copied (``database/stowe.db``, ``receipts/`` and
    anything else), so settings stored there come along. The original folder
    is never modified or deleted. Existing data in ``new_dir`` is never
    overwritten. The copy goes to a staging folder first and is renamed into
    place only when complete, so an interrupted copy is retried next launch.

    Returns the directory the app should use: ``new_dir`` normally, or
    ``old_dir`` if the copy failed (the app keeps working on the original).
    """
    if old_dir is None:
        old_dir = _legacy_stowe_data_dir()

    if _same_path(old_dir, new_dir) or not old_dir.is_dir():
        return new_dir
    if new_dir.exists() and not _is_empty_dir(new_dir):
        return new_dir
    try:
        if not any(old_dir.iterdir()):
            return new_dir
    except OSError:
        return new_dir

    staging = new_dir.parent / f".{new_dir.name}.migrating"
    try:
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(old_dir, staging, symlinks=True)
        if new_dir.exists():
            # Only an empty folder reaches this point.
            new_dir.rmdir()
        os.replace(staging, new_dir)
    except Exception as exc:
        logger.error(
            "Could not copy Stowe data from %s to %s (%s); using the original folder",
            old_dir,
            new_dir,
            exc,
        )
        try:
            if staging.exists():
                shutil.rmtree(staging)
        except OSError:
            pass
        return old_dir

    logger.info(
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


_SQLITE_SIDECARS = ("", "-wal", "-shm", "-journal")


def resolve_database_path(db_dir: Path) -> Path:
    """Return the database file to open in ``db_dir``.

    Uses ``wellslip.db``. If it is missing and a Stowe-era ``stowe.db`` exists,
    copies ``stowe.db`` (and any SQLite -wal/-shm/-journal sidecars) to the new
    name first and leaves the originals in place. If that copy fails, the app
    falls back to reading ``stowe.db`` so no data is hidden. An existing
    ``wellslip.db`` is never overwritten.
    """
    new = db_dir / DATABASE_FILENAME
    old = db_dir / LEGACY_DATABASE_FILENAME
    if new.exists() or not old.is_file():
        return new
    staged = []
    try:
        for suffix in _SQLITE_SIDECARS:
            src = db_dir / f"{LEGACY_DATABASE_FILENAME}{suffix}"
            if not src.is_file():
                continue
            tmp = db_dir / f".{DATABASE_FILENAME}{suffix}.migrating"
            shutil.copy2(src, tmp)
            staged.append((tmp, db_dir / f"{DATABASE_FILENAME}{suffix}"))
        # Sidecars first, main file last: wellslip.db appears only when complete.
        for tmp, dst in reversed(staged):
            os.replace(tmp, dst)
    except Exception as exc:
        logger.error(
            "Could not copy %s to %s (%s); reading the Stowe database in place",
            old,
            new,
            exc,
        )
        for tmp, dst in staged:
            for leftover in (tmp, dst):
                try:
                    if leftover.exists() and leftover != new:
                        leftover.unlink()
                except OSError:
                    pass
        if new.exists():
            return new
        return old
    logger.info("Copied %s to %s; original left in place", old, new)
    return new


# When packaged with PyInstaller, write user data to a platform-standard
# writable location instead of inside the read-only .app bundle: a
# "Wellslip Desktop" folder (see _user_data_dir). Stowe-era folders and the
# stowe.db file are copied to the new names on first launch.
DATA_DIR = _resolve_data_dir()

DATABASE_PATH = resolve_database_path(DATA_DIR / "database")
RECEIPTS_DIR = DATA_DIR / "receipts"
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

HSA_CATEGORIES = [
    "Medical",
    "Pharmacy",
    "Dental",
    "Vision",
    "Mental Health",
    "Medical Equipment",
    "Other",
]
