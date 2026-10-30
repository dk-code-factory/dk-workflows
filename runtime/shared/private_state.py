"""Locked atomic private JSON storage shared by iteration and flake tracking."""
import fcntl
import json
import os
import re
import tempfile
import sys
import time
from pathlib import Path

from shared.ci_deadline import ACTIVE_BUDGET

def root(repo):
    parts = repo.split("/")
    if len(parts) != 3 or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", p) or p in {".", ".."} for p in parts):
        raise ValueError("repo must be HOST/OWNER/REPO")
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    if not data.is_absolute():
        raise ValueError("XDG_DATA_HOME must be absolute")
    return data / "dk-pr-iter" / Path(*parts)


def read(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def write(path, value):
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n')


def sync_directory(path):
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def atomic_text(path, content, *, prefix='.state-', sync=None):
    path = Path(path)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=prefix)
    stream = None
    try:
        stream = os.fdopen(fd, "w", encoding='utf-8', errors='backslashreplace')
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            try:
                stream.close()
            except OSError:
                pass
            raise
        stream.close()
        os.chmod(name, 0o600)
        os.replace(name, path)
        (sync or sync_directory)(path.parent)
    finally:
        original = sys.exception()
        try:
            if stream is None:
                os.close(fd)
            if os.path.exists(name):
                os.unlink(name)
        except OSError:
            if original is None:
                raise


def mutate(path, default, operation):
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / (path.name + ".lock")).open("w") as lock:
        budget = ACTIVE_BUDGET.get()
        if budget is None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        else:
            # Try once without waiting even after expiry, so an uncontended
            # timeout-status write can finish. Contended locks share the budget.
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(min(0.05, budget.remaining()))
        value = read(path, default)
        result = operation(value)
        write(path, value)
        return result
