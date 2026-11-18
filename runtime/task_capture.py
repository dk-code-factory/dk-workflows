"""Exact resource/Git capture without workflow selection or durable state."""
from copy import deepcopy
from pathlib import Path
import hashlib
import mmap
import os
import re
import stat
import subprocess
import invocation
from task_primitives import CONTRACT, digest, git, identity, task_key

def resource(path):
    """Hash exact installed resource bytes, including directory names and symlink targets."""
    path = Path(path).absolute()
    def visit(item, ancestors):
        resolved = item.resolve(strict=True)
        if resolved in ancestors:
            raise ValueError('Cyclic resource symlink')
        if item.is_dir():
            return {'kind': 'directory', 'link': str(resolved) if item.is_symlink() else None,
                    'children': {p.name: visit(p, ancestors | {resolved}) for p in sorted(item.iterdir())
                                 if p.name not in {'.git', '__pycache__', '.pytest_cache'}}}
        if not item.is_file():
            raise ValueError('Resource is not a regular file: ' + str(item))
        return {'kind': 'file', 'sha256': hashlib.sha256(item.read_bytes()).hexdigest(),
                'executable': bool(item.stat().st_mode & 0o111),
                'link': str(resolved) if item.is_symlink() else None}
    return visit(path, set())


def _packed_refs_safe(path, names):
    """Fresh bounded lookups in Git's sorted packed-ref representation.

    Unknown formats decline sharing. Mapping avoids a Python copy of the file;
    each binary-search step reads one ref and its optional peeled row.
    """
    if not path.is_file():
        return False
    with path.open('rb') as source:
        header = source.readline(256)
        if not header:
            return True
        if (not header.endswith(b'\n') or not header.startswith(b'# pack-refs with: ')
                or b'sorted' not in header[len(b'# pack-refs with: '):].split()):
            return False
        with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as data:
            start, end = len(header), len(data)
            if data[-1:] != b'\n':
                return False

            def record(offset):
                stop = data.find(b'\n', offset)
                if stop < 0:
                    raise ValueError('Unterminated packed ref')
                oid, separator, name = data[offset:stop].partition(b' ')
                if (not separator or not re.fullmatch(rb'[0-9a-f]{40}|[0-9a-f]{64}', oid)
                        or not name.startswith(b'refs/')):
                    raise ValueError('Unsupported packed ref')
                following = stop + 1
                if data[following:following+1] == b'^':
                    stop = data.find(b'\n', following)
                    if stop < 0:
                        raise ValueError('Unterminated peeled ref')
                    following = stop + 1
                return name, following

            def lower_bound(wanted):
                low, high = start, end
                while low < high:
                    middle = (low + high)//2
                    boundary = data.rfind(b'\n', low, middle)
                    offset = low if boundary < 0 else boundary + 1
                    if data[offset:offset+1] == b'^':
                        boundary = data.rfind(b'\n', low, offset - 1)
                        offset = low if boundary < 0 else boundary + 1
                    name, following = record(offset)
                    if name < wanted:
                        low = following
                    else:
                        high = offset
                return record(low)[0] if low < end else b''

            if lower_bound(b'refs/replace/').startswith(b'refs/replace/'):
                return False
            return all(lower_bound(name) != name for name in names)


class _GitContextGuard:
    """Observe context versions without retaining a path-based content cache."""
    def __init__(self):
        self.environment = tuple(sorted(os.environ.items()))
        self.versions = {}

    @staticmethod
    def version(path):
        def stamp(value):
            return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
                    value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        try:
            own = path.lstat()
        except FileNotFoundError:
            return None
        target = None
        if stat.S_ISLNK(own.st_mode):
            try:
                target = stamp(path.stat())
            except FileNotFoundError:
                pass
        return stamp(own), target

    def watch(self, path):
        path = Path(path).absolute()
        # Ancestors also detect creation/removal of absent inputs and temporary
        # symlink or directory replacement that restores the original bytes.
        for candidate in (path, path.resolve()):
            for item in (candidate, *candidate.parents):
                if item not in self.versions:
                    self.versions[item] = self.version(item)
        return path

    def stable(self):
        try:
            return (self.environment == tuple(sorted(os.environ.items()))
                    and all(self.version(path) == version for path, version in self.versions.items()))
        except (OSError, ValueError, RuntimeError):
            return False


def _git_context(source, *, _guard=None):
    """Cheap context check for ordinary repositories and linked worktrees.

    Unusual Git layouts and indirection fall back to independent captures. This
    is only a command-local optimization; resource capture never uses this key.
    """
    if (os.environ.get('XDG_CONFIG_HOME') and not Path(os.environ['XDG_CONFIG_HOME']).is_absolute()
            or any('\n' in os.environ.get(name, '') or '\r' in os.environ.get(name, '')
                   for name in ('HOME', 'XDG_CONFIG_HOME', 'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_SYSTEM'))):
        return None
    if any(name in os.environ for name in (
            'GIT_DIR', 'GIT_COMMON_DIR', 'GIT_WORK_TREE', 'GIT_OBJECT_DIRECTORY',
            'GIT_ALTERNATE_OBJECT_DIRECTORIES',
            'GIT_CONFIG_COUNT', 'GIT_CONFIG_PARAMETERS', 'GIT_REPLACE_REF_BASE',
            'GIT_SHALLOW_FILE', 'GIT_GRAFT_FILE', 'GIT_INDEX_FILE', 'GIT_NAMESPACE',
            'GIT_ATTR_SOURCE')):
        return None
    guard = _guard or _GitContextGuard()
    try:
        checkout = guard.watch(source['checkout']).resolve()
        directory = guard.watch(checkout/'.git')
        if directory.is_file():
            value = os.fsdecode(directory.read_bytes().rstrip(b'\r\n'))
            if not value.startswith('gitdir: '):
                return None
            directory = guard.watch(checkout/value[8:]).resolve()
        if not directory.is_dir():
            return None
        common = directory
        metadata = guard.watch(directory/'commondir')
        if metadata.is_file():
            common = guard.watch(directory/os.fsdecode(metadata.read_bytes().rstrip(b'\r\n'))).resolve()
        for path in (common/'refs/replace', common/'info/grafts', common/'shallow'):
            guard.watch(path)
            if path.exists() and (not path.is_dir() or any(path.iterdir())):
                return None
        # A hex spelling may also name a mutable ref. Do not treat that
        # ambiguous spelling as an immutable object input, even if Git happens
        # to prefer the object on this version.
        ref_names = {name for pin in (source[key] for key in ('target', 'base', 'head'))
                     for name in (pin, 'refs/'+pin, 'refs/tags/'+pin, 'refs/heads/'+pin,
                                  'refs/remotes/'+pin, 'refs/remotes/'+pin+'/HEAD')}
        if any(guard.watch(root/name).exists() for root in (directory, common) for name in ref_names):
            return None
        packed = guard.watch(common/'packed-refs')
        if packed.exists() and not _packed_refs_safe(packed, {name.encode() for name in ref_names}):
            return None
        # Ask this Git installation for its actual paths, including relocatable
        # system prefixes and global overrides. Older Git without these vars
        # takes the ordinary independent-capture path.
        variables = {key: [] for key in ('GIT_CONFIG_SYSTEM', 'GIT_CONFIG_GLOBAL',
                                         'GIT_ATTR_SYSTEM', 'GIT_ATTR_GLOBAL')}
        # Local/global configuration can influence Git's path-variable receipt.
        # Register known paths before invoking Git, then the discovered paths
        # before reading their contents.
        for path in (common/'config', directory/'config.worktree'):
            guard.watch(path)
        if 'GIT_CONFIG_GLOBAL' not in os.environ:
            for path in (Path.home()/'.gitconfig',
                         Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')/'git/config'):
                guard.watch(path)
        for name in ('GIT_CONFIG_SYSTEM', 'GIT_CONFIG_GLOBAL'):
            if name in os.environ:
                guard.watch(os.environ[name])
        for line in git(checkout, 'var', '-l').splitlines():
            key, separator, value = line.partition(b'=')
            name = key.decode(errors='replace')
            if separator and name in variables:
                variables[name].append(Path(os.fsdecode(value)))
        for key, disabled in (('GIT_CONFIG_SYSTEM', 'GIT_CONFIG_NOSYSTEM'),
                              ('GIT_ATTR_SYSTEM', 'GIT_ATTR_NOSYSTEM')):
            if (os.environ.get(disabled, '').lower() not in {'1', 'true', 'yes', 'on'}
                    and len(variables[key]) != 1):
                return None
        if not variables['GIT_CONFIG_GLOBAL'] or len(variables['GIT_ATTR_GLOBAL']) != 1:
            return None
        if any(not path.is_absolute() for paths in variables.values() for path in paths):
            return None
        configs = [*variables['GIT_CONFIG_SYSTEM'], *variables['GIT_CONFIG_GLOBAL'],
                   common/'config', directory/'config.worktree']
        snapshots = []
        for path in configs:
            guard.watch(path)
            content = path.read_bytes() if path.exists() else None
            # Git skips an initial UTF-8 BOM. Indirect configuration, alternate
            # attribute roots and signature verification need a wider context.
            normalized = content.removeprefix(b'\xef\xbb\xbf') if content else b''
            if (re.search(rb'(?i)\[\s*(?:include|attr)', normalized)
                    or any(word in normalized.lower() for word in
                           (b'refstorage', b'attributesfile', b'orderfile', b'submodule', b'worktree'))
                    or (source.get('history') and b'showsignature' in normalized.lower())):
                return None
            snapshots.append((str(path.resolve()), content))
        # A committed diff still reads attributes from the working tree. Probe
        # changed paths, then read only their attribute ancestry, not the tree.
        names = git(checkout, '-c', 'diff.renames=false', 'diff', '--name-only', '-z',
                    '--no-relative', '--no-ext-diff', '--no-textconv', '--no-renames',
                    source['base'], source['head'], '--')
        attributes = {common/'info/attributes', directory/'info/attributes',
                      *variables['GIT_ATTR_SYSTEM'], *variables['GIT_ATTR_GLOBAL']}
        for name in names.split(b'\0'):
            if not name:
                continue
            parent = checkout/Path(os.fsdecode(name)).parent
            if not parent.is_relative_to(checkout):
                return None
            while parent.is_relative_to(checkout):
                attributes.add(parent/'.gitattributes')
                parent = parent.parent
        for path in sorted(attributes):
            guard.watch(path)
            snapshots.append((str(path.resolve()), path.read_bytes() if path.exists() else None))
        # Indexed attributes and checkout submodule configuration also affect diffs.
        for path in (directory/'index', checkout/'.gitmodules'):
            guard.watch(path)
            file_hash = None
            if path.exists():
                with path.open('rb') as source_file:
                    file_hash = hashlib.file_digest(source_file, 'sha256').digest()
            snapshots.append((str(path.resolve()), file_hash))
        if not guard.stable():
            return None
        return (str(checkout), str(directory.resolve()), str(common), tuple(snapshots),
                hashlib.sha256(names).digest(),
                tuple(sorted(os.environ.items())))
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError):
        return None


def _capture_git(source, *, batched=False):
    checkout = source['checkout']
    def resolve(values, kind):
        if batched:
            resolved = git(checkout, 'rev-parse', '--revs-only', '--end-of-options',
                           *(value+'^{'+kind+'}' for value in values.values())).decode().splitlines()
            if len(resolved) != len(values) or any(not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', sha)
                                                   for sha in resolved):
                raise ValueError('Git did not resolve every pinned '+kind)
            return dict(zip(values, resolved))
        resolved = {}
        for key, value in values.items():
            args = ('--verify',) if kind == 'commit' else ()
            resolved[key] = git(checkout, 'rev-parse', *args, value + '^{'+kind+'}').decode().strip()
        return resolved
    commits = resolve({key: source[key] for key in ('target', 'base', 'head')}, 'commit')
    trees = resolve(commits, 'tree')
    diff = git(checkout, '-c', 'diff.renames=false', 'diff', '--binary', '--full-index',
               '--no-ext-diff', '--no-textconv', '--no-relative', '--no-renames', commits['base'], commits['head'], '--')
    inputs = {'trees': trees, 'diff_sha256': hashlib.sha256(diff).hexdigest()}
    for option in ('history', 'head_sensitive'):
        if type(source.get(option, False)) is not bool:
            raise ValueError(option + ' must be boolean')
    if source.get('history'):
        history = git(checkout, 'log', '--format=raw', '--no-abbrev', '--no-decorate', '--no-notes',
                      commits['target'] + '..' + commits['head'])
        inputs['history'] = hashlib.sha256(history).hexdigest()
    if source.get('head_sensitive'):
        inputs['commits'] = commits
    return inputs, {'checkout': str(Path(checkout).resolve()), 'commits': commits}


class _CaptureContext:
    """Share only identical immutable Git calculations within one command."""
    def __init__(self):
        self.git_captures = {}

    def git(self, source):
        pins = tuple(source[key] for key in ('target', 'base', 'head'))
        options = tuple(source.get(key, False) for key in ('history', 'head_sensitive'))
        context = None
        guard = _GitContextGuard()
        if (all(isinstance(pin, str) and re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', pin)
                for pin in pins) and all(type(value) is bool for value in options)):
            context = _git_context(source, _guard=guard)
        if context is None:
            return _capture_git(source)
        key = context, pins, options
        if key not in self.git_captures:
            captured = _capture_git(source, batched=True)
            if tuple(captured[1]['commits'][name] for name in ('target', 'base', 'head')) != pins:
                return captured
            if guard.stable():
                self.git_captures[key] = captured
            return deepcopy(captured)
        captured = self.git_captures[key]
        if not guard.stable():
            return _capture_git(source)
        return deepcopy(captured)


def capture(spec, *, _context=None):
    """Capture a validated spec; workflow policy owns live selection checks."""
    policy = spec.get('instruction_policy')
    subject = identity(spec['repository'], spec['pr'])
    task, skill = spec['task'], spec['skill']
    invocation.skill_name(skill.get('identity') if isinstance(skill, dict) else None)
    if (not isinstance(task, dict) or not isinstance(task.get('id'), str) or not task['id']
            or not isinstance(task.get('instructions'), str) or not task['instructions']
            or not isinstance(skill, dict) or not isinstance(skill.get('identity'), str) or not skill['identity']):
        raise ValueError('Task id, assigned instructions and skill identity are required')
    def files(values):
        if not isinstance(values, dict) or any(not isinstance(k, str) for k in values):
            raise ValueError('Expected a label-to-resource-path map')
        return {key: resource(value) for key, value in sorted(values.items())}
    resources = files(skill.get('resources', {}))
    cacheable = spec.get('cacheable', True)
    if type(cacheable) is not bool:
        raise ValueError('cacheable must be boolean')
    if cacheable and (not resources or spec.get('governing_inputs_complete') is not True):
        raise ValueError('Cache requires captured resources and complete governing inputs; use cacheable:false otherwise')
    inputs = files(spec.get('inputs', {}))
    governing = files(spec.get('instructions', {}))
    provenance = {}
    if 'git' in spec:
        git_inputs, provenance = (_context.git(spec['git']) if _context is not None
                                  else _capture_git(spec['git']))
        if 'git' in inputs:
            # Input labels are unrestricted; retain a resource named git too.
            git_inputs = {**git_inputs, 'resource': inputs['git']}
        inputs['git'] = git_inputs
    fingerprint_inputs = {'contract': CONTRACT, **subject, 'task': task, 'skill': {
        'identity': skill['identity'], 'resources': resources}, 'instructions': governing,
        'inputs': inputs, 'options': {key: value for key, value in spec.get('options', {}).items()
                                      if key != 'hook_phase'}}
    if policy is not None:
        fingerprint_inputs['instruction_policy'] = policy['fingerprint']
    if 'native_review' in spec:
        from native_review import contract
        fingerprint_inputs['native_review'] = deepcopy(contract(spec['native_review']))
    key = task_key(subject, task['id'], skill['identity'], options=spec.get('options'))
    return {'key': key, 'fingerprint': digest(fingerprint_inputs), 'components': fingerprint_inputs,
            'provenance': provenance, 'cacheable': cacheable, 'subject': subject,
            'hook_phase': deepcopy(spec.get('options', {}).get('hook_phase'))}
