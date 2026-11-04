"""Private durable publication storage. Nothing here is auto-deleted."""
import os
from pathlib import Path
import tempfile


def operations_root(*, repository=None):
    base = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
    if not base.is_absolute():
        raise ValueError('XDG_STATE_HOME must be absolute')
    base = base / 'dk-pr-skills/v2/operations'
    from shared.repository_scope import inside_checkout
    if (base.resolve().is_relative_to(Path(__file__).resolve().parents[2])
            or inside_checkout(base, repository or Path.cwd())):
        raise ValueError('private workflow storage must stay outside the plugin and repository')
    return base


def allocate(*, repository=None):
    base = operations_root(repository=repository)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return Path(tempfile.mkdtemp(prefix='workflow-', dir=base))


def owned(path):
    base = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
    path = Path(path).resolve()
    if not path.is_relative_to((base / 'dk-pr-skills/v2/operations').resolve()):
        raise ValueError('workflow directory must be in private workflow storage')
    return path
