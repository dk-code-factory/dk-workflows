"""Locked atomic private JSON storage shared by iteration and flake tracking."""
import fcntl
import json
import os
import re
import tempfile
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
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".state-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
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
