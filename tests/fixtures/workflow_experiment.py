"""Prepare explicit native workflow exercises; never launch or simulate agents.

Preparation is not a measured trial. A native caller must freeze complete inputs,
transport coverage and host conditions before starting any paired arm. Unlike the
historical instruction-only fixture, this assignment retains default skill lists.
"""
import hashlib
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile


ROOT = Path(__file__).resolve().parents[2]
PR = 'https://github.com/owner/repo/pull/7'
BASELINE = '1befef223aca04f481a0417d8368238b5cc9b5df'
CANDIDATE = 'b478a6689fa65ff3412cfc158f54741f40b9599c'
ORDERS = [['baseline', 'candidate'], ['candidate', 'baseline'], ['baseline', 'candidate']]
SIMPLIFICATION_CONTEXT = ('Keep changes within the greeting feature and its original requirements.\n'
                          'Preserve safe HTML text and independent per-call history.\n')


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2)+'\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resource_hash(path):
    path = Path(path)
    if path.is_file():
        return sha(path)
    files = {str(item.relative_to(path)): sha(item) for item in sorted(path.rglob('*'))
             if item.is_file() and '__pycache__' not in item.parts and item.suffix != '.pyc'}
    if not files:
        raise ValueError('Missing or empty frozen resource')
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def git_environment():
    return {**{key: value for key, value in os.environ.items() if not key.startswith('GIT_')},
            'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_ATTR_NOSYSTEM': '1',
            'GIT_TERMINAL_PROMPT': '0', 'GIT_ALLOW_PROTOCOL': 'file'}


def git(checkout, *args):
    return subprocess.check_output([os.environ.get('DK_RECIPE_GIT', 'git'), '--no-lazy-fetch',
                                   '-C', str(checkout), *args],
                                   env=git_environment())


def default_plan(checkout, revision, route):
    if route not in ('fix', 'review'):
        raise ValueError('Unknown workflow route')
    return json.loads(git(checkout, 'show', revision+':codex/skills/dk-pr-'+route+'/config.json'))


def initialize(root, source, revision, route):
    """Export pinned helpers and a real local PR; never reset an existing arm."""
    root, source = Path(root).resolve(), Path(source).resolve()
    if root.exists():
        raise ValueError('Never replace an existing experiment arm')
    default_plan(source, revision, route)
    root.mkdir(parents=True)
    package = root/'package'
    package.mkdir()
    archive = git(source, 'archive', '--format=tar', revision)
    with tarfile.open(fileobj=io.BytesIO(archive)) as files:
        files.extractall(package, filter='data')
    for folder in ('artifacts', 'inputs', 'bin', 'state', 'config', 'data', 'git-template', 'control'):
        (root/folder).mkdir()
    checkout = root/'checkout'
    checkout.mkdir()
    real_git = shutil.which('git')
    env = {**git_environment(), 'GIT_AUTHOR_DATE': '2026-11-01T00:00:00Z',
           'GIT_COMMITTER_DATE': '2026-11-01T00:00:00Z',
           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_TEMPLATE_DIR': str(root/'git-template'), 'GIT_TERMINAL_PROMPT': '0'}

    def local(*args):
        return subprocess.check_output([real_git, '-C', str(checkout), *args], env=env).decode().strip()

    local('init', '-q', '-b', 'main')
    local('config', 'user.name', 'Fixture author')
    local('config', 'user.email', 'fixture@example.test')
    (checkout/'greeting.py').write_text('def greeting(name):\n    return "Hello, " + name\n')
    (checkout/'AGENTS.md').write_text('Keep changes within the greeting feature. Run python3 -m unittest discover.\n')
    (checkout/'.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    local('add', '.')
    local('commit', '-qm', 'Add plain greeting')
    base = local('rev-parse', 'HEAD')
    local('switch', '-qc', 'pr-branch')
    (checkout/'greeting.py').write_text('def greeting(name, history=[]):\n    history.append(name)\n'
                                      '    return "<p>Hello, " + name + "</p>", list(history)\n')
    (checkout/'README.md').write_text('# HTML greeting\n\n'
        'For a string name, `greeting(name)` returns an HTML paragraph and a list containing only that call\'s name. '
        'Render user-supplied names as text, including HTML metacharacters. Calls are independent.\n')
    (checkout/'test_greeting.py').write_text('import unittest\nfrom greeting import greeting\n\n'
        'class GreetingTest(unittest.TestCase):\n'
        '    def test_greeting(self):\n'
        '        self.assertEqual(greeting("Ada"), ("<p>Hello, Ada</p>", ["Ada"]))\n')
    local('add', '.')
    local('commit', '-qm', 'Add HTML greeting and call history')
    head = local('rev-parse', 'HEAD')
    bare = root/'remote.git'
    subprocess.run([real_git, 'clone', '--bare', '-q', str(checkout), str(bare)], check=True, env=env)
    local('remote', 'add', 'origin', 'https://github.com/owner/repo.git')
    spec = importlib.util.spec_from_file_location('workflow_fixture_remote',
                                                ROOT/'tests/pr/fixtures/github_remote.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    remote = fixture.initial()
    remote['metadata'].update(headRefOid=head, title='Add HTML greeting',
                              body='Return a safe HTML greeting and independent per-call history.')
    remote['pr_author'] = {'id': 1 if route == 'fix' else 2,
                           'login': 'author' if route == 'fix' else 'contributor'}
    remote['merge_gate'].update(baseRefOid=base, reviewDecision='')
    remote['pull']['base']['sha'] = base
    remote['branches'] = {'main': base, 'pr-branch': head}
    remote['run'].update(head_sha=head, status='in_progress', conclusion=None)
    remote['checks'][0].update(bucket='pending', state='PENDING')
    remote['lose_response'] = ['push' if route == 'fix' else 'post-review']
    patch = local('diff', base, head, '--', 'greeting.py')
    remote['files'] = [{'filename': 'greeting.py', 'patch': patch}]
    remote['thread']['comments'] = []
    save(root/'remote.json', remote)
    save(root/'control/phase.json', {'phase': 'initial', 'checkpoint': 'run'})
    (root/'inputs/simplification-context.md').write_text(SIMPLIFICATION_CONTEXT)
    (root/'inputs/protocol.json').write_bytes((ROOT/'tests/fixtures/workflow_protocol.json').read_bytes())
    for name in ('git', 'gh'):
        script = root/'bin'/name
        script.write_text('#!'+os.sys.executable+'\n'+
                          (ROOT/'tests/fixtures/recipe/remote_cli.py').read_text())
        script.chmod(0o755)
    environment = {'XDG_STATE_HOME': str(root/'state'), 'XDG_CONFIG_HOME': str(root/'config'),
                   'XDG_DATA_HOME': str(root/'data'), 'DK_RECIPE_REMOTE': str(root/'remote.json'),
                   'DK_RECIPE_FIXTURE': str(ROOT/'tests/fixtures/workflow_remote.py'),
                   'DK_RECIPE_BARE': str(bare), 'DK_RECIPE_GIT': real_git,
                   'PYTHONDONTWRITEBYTECODE': '1',
                   **{key: env[key] for key in ('GIT_CONFIG_GLOBAL', 'GIT_CONFIG_SYSTEM',
                       'GIT_CONFIG_NOSYSTEM', 'GIT_ATTR_NOSYSTEM', 'GIT_ALLOW_PROTOCOL',
                       'GIT_TEMPLATE_DIR', 'GIT_TERMINAL_PROMPT')}}
    reset = ('for dk_workflow_git_name in $(env | sed -n '
             "'s/^\\(GIT_[A-Za-z0-9_]*\\)=.*/\\1/p'); "
             'do unset "$dk_workflow_git_name"; done\n')
    (root/'environment.sh').write_text(reset+''.join('export '+key+'='+shlex.quote(value)+'\n'
        for key, value in environment.items())+'export PATH='+shlex.quote(str(root/'bin'))+':"$PATH"\n')
    plan_path = package/'codex/skills'/('dk-pr-'+route)/'config.json'
    scenario = {'schema': 1, 'route': route, 'pr': PR, 'base': base, 'head': head,
                'revision': revision, 'default_plan_source': str(plan_path),
                'default_plan_sha256': sha(plan_path),
                'recovery_cases': ['unchanged-fresh-session', 'partial-failure-with-completed-siblings',
                                   'relevant-input-invalidation', 'uncertain-publication'],
                'status': 'preparation-only; transport and host freeze required before native trials'}
    save(root/'inputs/scenario.json', scenario)
    (root/'inputs/assignment.txt').write_text(
        'Complete the default dk-workflows:dk-pr-'+route+' workflow for '+PR+'.\n'
        'Use the pinned package under '+str(package)+' and the real local PR checkout at '+str(checkout)+'. '
        'Read inputs/scenario.json, the selected entry skill, lifecycle contract and relevant stage guidance. '
        'Source environment.sh for every helper/Git/GitHub command. The local transports are authorized; '
        'real network access or publication is not. Keep the default task/finalizer selections, alternatives '
        'and simplification. Execute real native skills and supported delegation; instruction-only reports '
        'or synthetic native observations cannot substitute for execution. Preserve original results, '
        'claims, completed siblings, budgets and publication operations. Native tools own execution; '
        'never start another model session through a command or helper.\n'
        'Do not start a paired arm until freeze.json and independent transport/host preflight are complete. '
        'Read inputs/protocol.json and the external caller phase in control/phase.json. Follow its '
        'controlled recovery checkpoints, communicating with your native caller before dispatch when requested. '
        'Report stage evidence, findings, unresolved requirements and readiness accurately. '
        'Recovery is subsequent work on the original state, never a new trial or a reset.\n')
    return scenario


def phase(root, name, *, checkpoint='run'):
    protocol = json.loads((Path(root)/'inputs/protocol.json').read_text())
    if name not in protocol['phases']:
        raise ValueError('Unknown exercise phase')
    if checkpoint not in ('run', 'missing-context-dispatch', 'context-restored'):
        raise ValueError('Unknown controlled checkpoint')
    save(Path(root)/'control/phase.json', {'phase': name, 'checkpoint': checkpoint})


def partial_context(root, *, available):
    if type(available) is not bool:
        raise ValueError('Context availability must be boolean')
    path = Path(root)/'inputs/simplification-context.md'
    if path.exists() and path.read_text() != SIMPLIFICATION_CONTEXT:
        raise ValueError('Original simplification context changed')
    if available:
        path.write_text(SIMPLIFICATION_CONTEXT)
    elif path.exists():
        path.unlink()


def release_ci(root):
    root = Path(root)
    with (root/'remote.json.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return _release_ci(root)


def _release_ci(root):
    path = root/'remote.json'
    state = json.loads(path.read_text())
    head = state['metadata']['headRefOid']
    reads = state.get('check_reads', [])
    if not reads or reads[-1] != head:
        raise ValueError('No observed current-head CI read')
    if git(root/'checkout', 'rev-parse', 'HEAD').decode().strip() != head:
        raise ValueError('CI checkout differs from observed head')
    if git(root/'checkout', 'status', '--porcelain', '--untracked-files=all').strip():
        raise ValueError('CI checkout has unpublished changes')
    run = state['run']
    evidence_path = root/'artifacts'/('ci-controller-'+head+'-'+str(run['id'])+'-'+
                                      str(run.get('run_attempt', 1))+'.json')
    if evidence_path.exists():
        evidence = json.loads(evidence_path.read_text())
        expected = 'success' if evidence['returncode'] == 0 else 'failure'
        if run['status'] != 'completed' or run['conclusion'] != expected:
            raise ValueError('Retained CI result differs from current run')
        return evidence
    result = subprocess.run([sys.executable, '-m', 'unittest', 'discover'],
        cwd=root/'checkout', env={**git_environment(), 'PYTHONDONTWRITEBYTECODE': '1'},
        text=True, capture_output=True)
    if git(root/'checkout', 'rev-parse', 'HEAD').decode().strip() != head:
        raise ValueError('CI checkout changed during tests')
    if git(root/'checkout', 'status', '--porcelain', '--untracked-files=all').strip():
        raise ValueError('CI checkout changed during tests')
    evidence = {'head': head, 'returncode': result.returncode,
                'stdout': result.stdout, 'stderr': result.stderr}
    save(evidence_path, evidence)
    success = result.returncode == 0
    state['run'].update(status='completed', conclusion='success' if success else 'failure')
    for check in state['checks']:
        check.update(bucket='pass' if success else 'fail', state='SUCCESS' if success else 'FAILURE')
    save(path, state)
    return evidence


def freeze(root, *, conditions, external_resources):
    """Seal prepared inputs once. Conditions reference observed host preflight."""
    root = Path(root)
    if (root/'freeze.json').exists():
        raise ValueError('Never replace an experiment freeze')
    conditions = Path(conditions)
    value = json.loads(conditions.read_text())
    required = ('instructions_sha256', 'tools_sha256', 'controls_sha256',
                'assignment_sha256', 'quality_oracle_sha256', 'transport_verification_sha256')
    if not all(isinstance(value.get(key), str) and len(value[key]) == 64 for key in required):
        raise ValueError('Missing observed host, assignment, quality or transport freeze')
    paths = [root/'inputs', root/'package', root/'bin', root/'environment.sh', conditions,
             ROOT/'tests/fixtures/workflow_experiment.py', ROOT/'tests/fixtures/workflow_protocol.json',
             ROOT/'tests/fixtures/workflow_cost.py', ROOT/'tests/fixtures/native_cost_observations.py',
             ROOT/'tests/fixtures/workflow_quality.py',
             ROOT/'tests/fixtures/native_usage.py', ROOT/'tests/fixtures/dollar_usage.py',
             ROOT/'tests/fixtures/workflow_remote.py', ROOT/'tests/pr/fixtures/github_remote.py',
             ROOT/'tests/fixtures/recipe/remote_cli.py',
             *map(Path, external_resources)]
    snapshot = {'schema': 1, 'resources': {str(path.resolve()): resource_hash(path) for path in paths},
                'initial_remote_sha256': sha(root/'remote.json'),
                'conditions': str(conditions.resolve()), 'orders': ORDERS}
    save(root/'freeze.json', snapshot)
    return snapshot


def verify_freeze(root):
    value = json.loads((Path(root)/'freeze.json').read_text())
    return {'passed': all(resource_hash(Path(path)) == expected
                          for path, expected in value['resources'].items())}
