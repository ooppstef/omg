"""用户可写的运行时数据路径。"""

import errno
import os
import shutil
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


DATA_DIR_ENV = "OMG_DATA_DIR"


def _absolute_env_path(name: str):
    value = os.getenv(name)
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else None


def user_data_dir() -> Path:
    configured = os.getenv(DATA_DIR_ENV)
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            raise ValueError(f"{DATA_DIR_ENV} 必须是绝对路径")
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "omg"
    elif os.name == "nt":
        root = _absolute_env_path("LOCALAPPDATA")
        if root is None:
            root = Path.home() / "AppData" / "Local"
        path = root / "omg"
    else:
        root = _absolute_env_path("XDG_DATA_HOME")
        if root is None:
            root = Path.home() / ".local" / "share"
        path = root / "omg"
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def exclusive_file_lock(path: Path):
    """跨平台阻塞式文件锁。"""
    lock_file = path.open("a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            while True:
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EDEADLK):
                        raise
                    time.sleep(0.1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            acquired = True
        yield
    finally:
        if acquired and os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        elif acquired:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


def temporary_path_for(target: Path) -> Path:
    return target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")


def _copy_legacy_file(legacy: Path, target: Path) -> None:
    temporary = temporary_path_for(target)
    if legacy.suffix == ".db":
        source = sqlite3.connect(f"{legacy.resolve().as_uri()}?mode=ro", uri=True)
        try:
            destination = sqlite3.connect(temporary)
            try:
                source.backup(destination)
            finally:
                destination.close()
            os.replace(temporary, target)
        finally:
            source.close()
            temporary.unlink(missing_ok=True)
        return

    try:
        shutil.copy2(legacy, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def data_file(name: str, legacy_path=None) -> Path:
    target = user_data_dir() / name
    if (
        not os.getenv(DATA_DIR_ENV)
        and legacy_path is not None
        and not target.exists()
    ):
        legacy = Path(legacy_path)
        if legacy.exists():
            with exclusive_file_lock(user_data_dir() / ".migration.lock"):
                if not target.exists():
                    _copy_legacy_file(legacy, target)
    return target
