import logging
import os
import pathlib
import shutil
import sys
from pathlib import Path

APP_NAME = "Stowe"
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
        return _os_path(local) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / APP_NAME.lower()


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
        return migrate_roaming_data_dir(data_dir)
    return data_dir


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


# When packaged with PyInstaller, write user data to a platform-standard
# writable location instead of inside the read-only .app bundle. On Windows
# that is %LOCALAPPDATA%\Stowe; a legacy %APPDATA%\Stowe folder is migrated
# the first time the frozen app starts.
DATA_DIR = _resolve_data_dir()

DATABASE_PATH = DATA_DIR / "database" / "stowe.db"
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
