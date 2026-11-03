"""Small, host-independent task cache and retained workflow budgets (stdlib only)."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit
import base64
import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import uuid

CONTRACT = 2


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    """Durable replace; a failed write never clears an existing claim."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, indent=2, sort_keys=True, allow_nan=False)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def git(checkout, *args):
    return subprocess.check_output(['git', '-C', str(checkout), *args], stderr=subprocess.PIPE)


def identity(repository, pr):
    repository = repository.lower().removeprefix('remote:').removesuffix('.git')
    if not re.fullmatch(r'[a-z0-9.-]+/[a-z0-9_.-]+/[a-z0-9_.-]+', repository):
        raise ValueError('repository must be HOST/OWNER/REPO')
    url = urlsplit(pr)
    parts = url.path.rstrip('/').split('/')
    if (url.scheme != 'https' or url.username or url.password or url.query or url.fragment
            or len(parts) != 5 or parts[3] != 'pull' or not parts[4].isdigit()
            or int(parts[4]) <= 0 or '/'.join([url.netloc.lower(), parts[1].lower(), parts[2].lower()]) != repository):
        raise ValueError('PR URL must identify the canonical repository')
    return {'repository': repository, 'pr': f'https://{repository}/pull/{int(parts[4])}'}


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


def _git_context(source):
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
    checkout = Path(source['checkout']).resolve()
    try:
        directory = checkout/'.git'
        if directory.is_file():
            value = directory.read_text().strip()
            if not value.startswith('gitdir: '):
                return None
            directory = (checkout/value[8:]).resolve()
        if not directory.is_dir():
            return None
        common = directory
        if (directory/'commondir').is_file():
            common = (directory/(directory/'commondir').read_text().strip()).resolve()
        for path in (common/'refs/replace', common/'info/grafts', common/'shallow'):
            if path.exists() and (not path.is_dir() or any(path.iterdir())):
                return None
        # A hex spelling may also name a mutable ref. Do not treat that
        # ambiguous spelling as an immutable object input, even if Git happens
        # to prefer the object on this version.
        ref_names = {name for pin in (source[key] for key in ('target', 'base', 'head'))
                     for name in (pin, 'refs/'+pin, 'refs/tags/'+pin, 'refs/heads/'+pin,
                                  'refs/remotes/'+pin, 'refs/remotes/'+pin+'/HEAD')}
        if any((root/name).exists() for root in (directory, common) for name in ref_names):
            return None
        packed = common/'packed-refs'
        if packed.exists():
            encoded_names = {name.encode() for name in ref_names}
            with packed.open('rb') as refs:
                for line in refs:
                    fields = line.split()
                    if len(fields) == 2 and (fields[1].startswith(b'refs/replace/')
                                             or fields[1] in encoded_names):
                        return None
        # Ask this Git installation for its actual paths, including relocatable
        # system prefixes and global overrides. Older Git without these vars
        # takes the ordinary independent-capture path.
        variables = {key: [] for key in ('GIT_CONFIG_SYSTEM', 'GIT_CONFIG_GLOBAL',
                                         'GIT_ATTR_SYSTEM', 'GIT_ATTR_GLOBAL')}
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
            content = path.read_bytes() if path.exists() else None
            # Git skips an initial UTF-8 BOM. Indirect configuration, alternate
            # attribute roots and signature verification need a wider context.
            normalized = content.removeprefix(b'\xef\xbb\xbf') if content else b''
            if (re.search(rb'(?im)^\s*\[\s*(?:include|attr)', normalized)
                    or any(word in normalized.lower() for word in
                           (b'reftable', b'attributesfile', b'orderfile', b'submodule', b'worktree'))
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
            snapshots.append((str(path.resolve()), path.read_bytes() if path.exists() else None))
        # Retain indexed attribute state as part of the checkout context, too.
        index = directory/'index'
        index_hash = None
        if index.exists():
            with index.open('rb') as indexed:
                index_hash = hashlib.file_digest(indexed, 'sha256').digest()
        snapshots.append((str(index.resolve()), index_hash))
        return (str(checkout), str(directory.resolve()), str(common), tuple(snapshots),
                tuple(sorted(os.environ.items())))
    except (OSError, ValueError, subprocess.CalledProcessError):
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
               '--no-ext-diff', '--no-textconv', '--no-renames', commits['base'], commits['head'], '--')
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
    """Share only identical immutable Git calculations within one status call."""
    def __init__(self):
        self.git_captures = {}

    def git(self, source):
        pins = tuple(source[key] for key in ('target', 'base', 'head'))
        options = tuple(source.get(key, False) for key in ('history', 'head_sensitive'))
        context = None
        if (all(isinstance(pin, str) and re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', pin)
                for pin in pins) and all(type(value) is bool for value in options)):
            context = _git_context(source)
        if context is None:
            return _capture_git(source)
        key = context, pins, options
        if key not in self.git_captures:
            captured = _capture_git(source, batched=True)
            if tuple(captured[1]['commits'][name] for name in ('target', 'base', 'head')) != pins:
                return captured
            self.git_captures[key] = captured
        return deepcopy(self.git_captures[key])


def capture(spec, *, _context=None):
    subject = identity(spec['repository'], spec['pr'])
    task, skill = spec['task'], spec['skill']
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
        inputs['git'], provenance = (_context.git(spec['git']) if _context is not None
                                     else _capture_git(spec['git']))
    fingerprint_inputs = {'contract': CONTRACT, **subject, 'task': task, 'skill': {
        'identity': skill['identity'], 'resources': resources}, 'instructions': governing,
        'inputs': inputs, 'options': spec.get('options', {})}
    key = digest({**subject, 'task': task['id'], 'skill': skill['identity']})
    return {'key': key, 'fingerprint': digest(fingerprint_inputs), 'components': fingerprint_inputs,
            'provenance': provenance, 'cacheable': cacheable, 'subject': subject}


class Store:
    def __init__(self, repository, pr, *, checkout=None):
        self.subject = identity(repository, pr)
        base = Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state')
        if not base.is_absolute():
            raise ValueError('XDG_STATE_HOME must be absolute')
        self.root = (base/'dk-workflows/v2'/digest(self.subject)).resolve()
        roots = [Path(__file__).resolve().parents[1], Path(checkout or Path.cwd()).resolve()]
        # Also reject state nested in any other Git checkout.
        probe = self.root
        while not probe.exists():
            probe = probe.parent
        check = subprocess.run(['git', '-C', str(probe), 'rev-parse', '--show-toplevel'],
                               capture_output=True, text=True)
        if check.returncode == 0:
            roots.append(Path(check.stdout.strip()).resolve())
        if any(self.root.is_relative_to(p) for p in roots):
            raise ValueError('Workflow state must be outside repositories and the plugin')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.manifest = self.root/'manifest.json'

    @contextmanager
    def locked(self):
        with (self.root/'.lock').open('a') as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            manifest = read(self.manifest) if self.manifest.exists() else {
                'contract': CONTRACT, 'subject': self.subject, 'entries': {}, 'runs': {}}
            if manifest.get('contract') != CONTRACT or manifest.get('subject') != self.subject:
                raise ValueError('Unsupported or damaged manifest; do not reset retained budgets')
            yield manifest

    def saved(self, entry):
        reference = entry.get('result')
        if not reference:
            return None
        try:
            path = self.root/'results'/reference['file']
            if path.parent.resolve() != (self.root/'results').resolve():
                return None
            result = read(path)
            original = base64.b64decode(result['original'], validate=True)
            if (digest(result) != reference['hash'] or result.get('contract') != CONTRACT
                    or result['status'] not in {'complete','failed','incomplete'}
                    or type(result['normalized']['complete']) is not bool
                    or (result['status'] == 'complete') != result['normalized']['complete']
                    or hashlib.sha256(original).hexdigest() != result['original_sha256']
                    or result['fingerprint'] != reference['fingerprint']
                    or result['key'] != entry['key']):
                return None
            return {'path': str(path), **result}
        except (ValueError, OSError, KeyError, TypeError):
            return None

    def describe(self, captured, entry):
        saved = self.saved(entry)
        changed = []
        old = entry.get('components', {})
        for key, value in captured['components'].items():
            if value != old.get(key):
                changed.append(key)
        reusable = bool(captured['cacheable'] and not entry.get('invalidated') and not entry.get('claim')
                        and saved and saved['status'] == 'complete' and saved.get('cacheable') is True and saved['fingerprint'] == captured['fingerprint'])
        return {'key': captured['key'], 'task': captured['components']['task']['id'],
                'reusable': reusable, 'changed': changed,
                'unfinished': entry.get('claim'), 'attempts': len(entry.get('attempts', [])),
                'result': saved['path'] if saved else None, 'completion': saved['status'] if saved else None,
                'findings': saved['normalized'].get('findings', []) if reusable else None,
                'invalidated': entry.get('invalidated'),
                'reason': ('reusable' if reusable else 'unfinished' if entry.get('claim') else
                           'invalidated' if entry.get('invalidated') else
                           'changed_inputs' if changed else saved['status'] if saved and saved['status'] != 'complete' else 'missing_or_incomplete_result')}

    def status(self, specs, *, compact=False, run=None):
        context = _CaptureContext()
        captures = [capture(spec, _context=context) for spec in specs]
        if any(c['subject'] != self.subject for c in captures):
            raise ValueError('Specs refer to another PR')
        with self.locked() as manifest:
            if run is not None and run not in manifest['runs']:
                raise ValueError('Unknown run id: ' + run)
            runs = manifest['runs'] if run is None else {run: manifest['runs'][run]}
            if compact:
                runs = {
                    name: {
                        **{key: value for key, value in state.items() if key != 'progress'},
                        'cycle': state['cycles'][-1] if state['cycles'] else None,
                        'progress_count': len(state['progress']),
                        'progress_ref': {
                            'manifest': str(self.manifest),
                            'pointer': '/runs/' + name.replace('~', '~0').replace('/', '~1') + '/progress',
                        },
                    }
                    for name, state in runs.items()
                }
            return {'contract': CONTRACT, 'subject': self.subject, 'manifest': str(self.manifest),
                    'tasks': [self.describe(c, manifest['entries'].get(c['key'], {})) for c in captures],
                    'runs': runs,
                    'unfinished': [e['claim'] for e in manifest['entries'].values() if e.get('claim')]}

    @staticmethod
    def budget(manifest, *, run, cycle, retry, ci_repair, ci_rerun, head, task):
        if not run:
            if any((cycle, retry, ci_repair, ci_rerun)):
                raise ValueError('Budgeted execution requires a retained run id')
            return None
        state = manifest['runs'].setdefault(run, {'cycles': [], 'retries': {}, 'ci_repairs': {},
                                                 'ci_reruns': {}, 'review_tasks': {}, 'progress': []})
        if cycle and cycle not in state['cycles']:
            if ci_repair or ci_rerun:
                raise ValueError('CI work must retain an existing cycle or use cycle zero')
            if retry:
                raise ValueError('Retry requires an existing cycle')
            if len(state['cycles']) >= 5:
                raise ValueError('Five review-and-fix cycle limit reached')
            state['cycles'].append(cycle)
        if retry:
            if not cycle:
                raise ValueError('Same-cycle retry requires its cycle id')
            key = cycle + ':' + task
            if state['retries'].get(key, 0) >= 1:
                raise ValueError('Same-cycle retry already consumed')
            state['retries'][key] = state['retries'].get(key, 0) + 1
        if ci_repair:
            key = cycle or '0'
            if state['ci_repairs'].get(key, 0) >= 5:
                raise ValueError('Five CI repair attempts per cycle reached')
            state['ci_repairs'][key] = state['ci_repairs'].get(key, 0) + 1
        if ci_rerun:
            if not head:
                raise ValueError('CI rerun requires exact head')
            if state['ci_reruns'].get(head, 0) >= 3:
                raise ValueError('Three CI reruns per head reached')
            state['ci_reruns'][head] = state['ci_reruns'].get(head, 0) + 1
        return state

    def start(self, spec, *, recover=None, run=None, cycle=None, retry=False,
              ci_repair=False, ci_rerun=False, head=None):
        captured = capture(spec)
        if captured['subject'] != self.subject:
            raise ValueError('Spec refers to another PR')
        with self.locked() as manifest:
            entry = manifest['entries'].setdefault(captured['key'], {'key': captured['key'], 'attempts': []})
            previous = self.describe(captured, entry)
            if entry.get('claim'):
                if recover != entry['claim']['id']:
                    return {'action': 'recover_required', **previous}
                if run != entry['claim']['run'] or (
                        captured['fingerprint'] == entry['claim']['fingerprint']
                        and cycle != entry['claim']['cycle']):
                    raise ValueError('Recovery must retain the original run and unchanged-input cycle budgets')
                entry['attempts'][-1]['status'] = 'interrupted'
                entry['attempts'][-1]['recovery'] = 'explicit recovery by coordinating agent'
                del entry['claim']
            elif recover:
                raise ValueError('Recovery id is not the active claim')
            if previous['reusable']:
                return {'action': 'reuse', **previous}
            state = self.budget(manifest, run=run, cycle=cycle, retry=retry, ci_repair=ci_repair,
                                ci_rerun=ci_rerun, head=head, task=captured['key'])
            if cycle and not (ci_repair or ci_rerun):
                tasks = state['review_tasks'].setdefault(cycle, {})
                previous_fingerprint = tasks.get(captured['key'])
                if previous_fingerprint and previous_fingerprint != captured['fingerprint']:
                    raise ValueError('Changed review inputs require a new cycle; retain the existing budget')
                if previous_fingerprint and not (retry or recover):
                    raise ValueError('Repeated same-cycle execution requires explicit retry or claim recovery')
                if retry and not previous_fingerprint:
                    raise ValueError('Same-cycle retry requires a recorded original task attempt')
                tasks[captured['key']] = captured['fingerprint']
            claim = {'id': uuid.uuid4().hex, 'key': captured['key'], 'fingerprint': captured['fingerprint'],
                     'cacheable': captured['cacheable'], 'started': time.time(), 'run': run, 'cycle': cycle,
                     'provenance': captured['provenance']}
            entry.update(claim=claim, components=captured['components'])
            entry['attempts'].append({**claim, 'status': 'started'})
            write(self.manifest, manifest)
            return {'action': 'invoke', 'claim': claim, 'manifest': str(self.manifest),
                    'changed': previous['changed']}

    def record(self, spec, claim_id, original, normalized, status, *, progress=None):
        captured = capture(spec)
        if captured['subject'] != self.subject:
            raise ValueError('Spec refers to another PR')
        if (status not in {'complete', 'failed', 'incomplete'} or not isinstance(normalized, dict)
                or type(normalized.get('complete')) is not bool
                or not isinstance(normalized.get('findings', []), list)
                or (status == 'complete') != normalized['complete']):
            raise ValueError('Explicit completion status and normalized complete/findings are required')
        with self.locked() as manifest:
            entry = manifest['entries'].get(captured['key'], {})
            claim = entry.get('claim')
            if not claim or claim['id'] != claim_id:
                raise ValueError('Claim is absent or owned by another attempt')
            if claim['fingerprint'] != captured['fingerprint']:
                raise ValueError('Inputs changed during execution; retain claim and explicitly recover')
            envelope = {'contract': CONTRACT, 'key': captured['key'], 'fingerprint': captured['fingerprint'],
                        'cacheable': claim['cacheable'], 'provenance': claim['provenance'], 'claim': claim_id, 'status': status,
                        'original': base64.b64encode(original).decode(),
                        'original_sha256': hashlib.sha256(original).hexdigest(), 'normalized': normalized}
            fingerprint = digest(envelope)
            filename = fingerprint + '.json'
            path = self.root/'results'/filename
            if path.exists():
                if read(path) != envelope:
                    raise ValueError('Immutable result collision')
            else:
                write(path, envelope)
            entry['result'] = {'file': filename, 'hash': fingerprint, 'fingerprint': captured['fingerprint']}
            entry['attempts'][-1].update(status=status, result=filename, finished=time.time())
            entry.pop('claim')
            entry.pop('invalidated', None)
            if progress is not None:
                if not claim['run']:
                    raise ValueError('Progress requires a retained run id')
                manifest['runs'][claim['run']]['progress'].append({'claim': claim_id, 'value': progress})
            write(self.manifest, manifest)
            return {'status': status, 'result': str(path), 'reusable': status == 'complete' and captured['cacheable']}

    def invalidate(self, keys, reason, *, recover=None):
        if not reason:
            raise ValueError('An invalidation reason is required')
        with self.locked() as manifest:
            for key in keys:
                if key not in manifest['entries']:
                    raise ValueError('Unknown task key: ' + key)
                active = manifest['entries'][key].get('claim')
                if active and recover != active['id']:
                    raise ValueError('Active claim requires its explicit recovery id for invalidation')
                if recover and (not active or recover != active['id']):
                    raise ValueError('Recovery id is not the active claim')
            for key in keys:
                entry = manifest['entries'][key]
                if entry.pop('claim', None):
                    entry['attempts'][-1].update(status='interrupted', recovery=reason, finished=time.time())
                manifest['entries'][key]['invalidated'] = {'reason': reason, 'at': time.time()}
            write(self.manifest, manifest)
        return {'invalidated': keys}
