"""Shared local storage, logging and resilient event delivery (no Tk dependency)."""
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from queue import Empty
from threading import Lock
import ctypes
import errno
import logging
import os
import stat
import sys
import tempfile

_logger_lock = Lock()


def user_cache_dir():
    base = os.getenv("LOCALAPPDATA") or os.getenv("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "geoask"


def get_logger():
    logger = logging.getLogger("geoask")
    with _logger_lock:
        if not logger.handlers:
            logger.setLevel(logging.INFO)
            try:
                directory = user_cache_dir() / "logs"
                directory.mkdir(parents=True, exist_ok=True)
                handler = RotatingFileHandler(directory / "geoask.log", maxBytes=2_000_000,
                                              backupCount=3, encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            except OSError:
                handler = logging.NullHandler()
            logger.addHandler(handler)
    return logger


def publish_new(source, destination):
    """Atomic rename without replacement or an empty destination reservation.

    Use native no-replace rename; no hard links (FAT/SMB may lack them). Fail
    closed when a platform/filesystem cannot provide the required semantics.
    """
    if os.name == "nt":
        os.rename(source, destination)  # Windows rename fails if target exists.
        return
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux") and hasattr(library, "renameat2"):
        operation = library.renameat2
        operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        operation.restype = ctypes.c_int
        result = operation(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    elif sys.platform == "darwin" and hasattr(library, "renamex_np"):
        operation = library.renamex_np
        operation.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        operation.restype = ctypes.c_int
        result = operation(os.fsencode(source), os.fsencode(destination), 4)
    else:
        raise OSError(errno.ENOTSUP, "当前系统不支持安全的新文件发布，请使用支持原子重命名的本地目录。")
    if result:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


@contextmanager
def atomic_output(path, *, mode="w", encoding="utf-8", newline=None, overwrite=True, parents=False):
    """Publish a complete file on the same filesystem, cleaning up on failure.

    New destinations appear only after the complete temporary file is closed.
    Existing POSIX permission bits survive a replacement.
    """
    path = Path(path)
    temporary = None
    if parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    permission = stat.S_IMODE(path.stat().st_mode) if overwrite and path.exists() else None
    try:
        with tempfile.NamedTemporaryFile(mode=mode, encoding=encoding if "b" not in mode else None,
                                         newline=newline, dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            yield output
            output.flush()
            os.fsync(output.fileno())
        if permission is not None and os.name != "nt":
            temporary.chmod(permission)
        if overwrite:
            os.replace(temporary, path)
        else:
            publish_new(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                get_logger().warning("Could not clean temporary output file")


def drain_events(queue, handle, on_error, *, limit=100):
    """Isolate each event; bound the batch so Tk can still service input/timers."""
    count = 0
    while count < limit:
        try:
            event = queue.get_nowait()
        except Empty:
            break
        try:
            handle(event)
        except Exception:
            # Malformed/non-subscriptable events must not break error recovery.
            get_logger().exception("Event handler failed (type=%s)", type(event).__name__)
            try:
                on_error()
            except Exception:
                get_logger().exception("Event recovery failed")
        count += 1
    return count
