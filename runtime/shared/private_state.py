"""Locked atomic private JSON storage shared by iteration and flake tracking."""
import fcntl
import json
import os
import re
import tempfile
import sys
from pathlib import Path

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


def atomic_bytes(path, content):
    """Persist exact bytes privately and durably, without newline translation."""
    path = Path(path)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.state-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def mutate(path, default, operation):
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / (path.name + ".lock")).open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = read(path, default)
        result = operation(value)
        write(path, value)
        return result
