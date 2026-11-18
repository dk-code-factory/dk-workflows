"""Existing private manifest, lock and immutable-result storage contract."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import base64
import fcntl
import hashlib
import os
from task_primitives import CONTRACT, digest, has_output_content, identity, read
from shared.storage_paths import location

class TaskStorage:
    def __init__(self, repository, pr, *, checkout=None, read_only=False):
        self.subject = identity(repository, pr)
        self.root = (location('tasks')/digest(self.subject)).resolve()
        from shared.repository_scope import checkout_roots
        roots = [Path(__file__).resolve().parents[1], *checkout_roots(checkout or Path.cwd())]
        # Also reject state nested in any other Git checkout.
        probe = self.root
        while not probe.exists():
            probe = probe.parent
        roots.extend(checkout_roots(probe))
        if any(self.root.is_relative_to(p) for p in roots):
            raise ValueError('Workflow state must be outside repositories and the plugin')
        if type(read_only) is not bool:
            raise ValueError('Read-only mode must be boolean')
        if not read_only:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.manifest = self.root/'manifest.json'
        self._read_view = self._manifest() if read_only else None


    def _manifest(self):
        manifest = read(self.manifest) if self.manifest.exists() else {
            'contract': CONTRACT, 'subject': self.subject, 'entries': {}, 'runs': {}}
        if manifest.get('contract') != CONTRACT or manifest.get('subject') != self.subject:
            raise ValueError('Unsupported or damaged manifest; do not reset retained budgets')
        return manifest


    def _writable(self):
        if self._read_view is not None:
            raise ValueError('Read-only inspection cannot change retained state')


    @contextmanager
    def locked(self):
        if self._read_view is not None:
            # Atomic manifest replacement supplies one immutable command-local
            # view. No lock file, directory or persisted observation is created.
            yield deepcopy(self._read_view)
            return
        with (self.root/'.lock').open('a') as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield self._manifest()


    def saved(self, entry):
        return self._load_saved(entry)[0]


    def _load_saved(self, entry):
        reference = entry.get('result')
        if not reference:
            return None, 'missing_result'
        try:
            path = self.root/'results'/reference['file']
            if path.parent.resolve() != (self.root/'results').resolve():
                return None, 'damaged_result'
            result = read(path)
            original = base64.b64decode(result['original'], validate=True)
            if (digest(result) != reference['hash'] or result.get('contract') != CONTRACT
                    or result['status'] not in {'complete','failed','incomplete'}
                    or type(result['normalized']['complete']) is not bool
                    or (result['status'] == 'complete') != result['normalized']['complete']
                    or (result['status'] == 'complete' and not has_output_content(original))
                    or hashlib.sha256(original).hexdigest() != result['original_sha256']
                    or result['fingerprint'] != reference['fingerprint']
                    or result['key'] != entry['key']):
                return None, 'damaged_result'
            return {'path': str(path), **result}, None
        except FileNotFoundError:
            return None, 'missing_result_artifact'
        except (ValueError, OSError, KeyError, TypeError):
            return None, 'damaged_result'
