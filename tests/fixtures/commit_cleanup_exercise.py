#!/usr/bin/env python3
"""Opt-in native exercise transport. No model calls or semantic decisions.

Initialize each arm outside a repository. All agent operations, including failed
ones, are traced. Git mutations are confined to disposable fixture worktrees.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
STAMP = '2026-01-01T00:00:00+00:00'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')


def append(path, value):
    with path.open('a') as out:
        out.write(json.dumps(value, sort_keys=True)+'\n')


def git(root, checkout, *args, input=None):
    env = {**os.environ, 'GIT_AUTHOR_DATE': STAMP, 'GIT_COMMITTER_DATE': STAMP,
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
           'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0'}
    result = subprocess.run(['git', '-C', str(checkout), *args], input=input,
                            capture_output=True, text=True, env=env, timeout=30)
    if (root/'exercise.json').exists():
        append(root/'git-trace.jsonl', {'checkout': str(checkout.relative_to(root)),
               'args': args, 'ok': result.returncode == 0})
    if result.returncode:
        raise ValueError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def commit(root, path, message, files, author=None):
    for name, content in files.items():
        target = path/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    git(root, path, 'add', '.')
    args = ['commit', '-qm', message]
    if author:
        args += ['--author', author]
    git(root, path, *args)
    return git(root, path, 'rev-parse', 'HEAD').strip()


def initialize(root, source, candidate):
    if root.exists():
        raise ValueError('arm already exists')
    if root.is_relative_to(source):
        raise ValueError('exercise arms must stay outside their source checkout')
    root.mkdir(parents=True)
    shutil.copytree(source, root/'guidance', ignore=shutil.ignore_patterns(
        '.git', '__pycache__', '.pytest_cache', '.dependencies'))
    frozen = root/'frozen/interface.py'
    frozen.parent.mkdir()
    shutil.copyfile(Path(__file__), frozen)
    cases = {}
    names = {1: ['alpha', 'beta', 'gamma', 'delta'], 2: ['alpha', 'beta'],
             3: ['alpha', 'beta'], 4: ['epsilon'], 5: ['beta'],
             6: ['alpha', 'beta', 'gamma', 'zeta', 'theta'],
             0: ['alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta', 'theta']}[candidate]
    for name in names:
        store = root/'repos'/name
        store.parent.mkdir(exist_ok=True)
        git(root, root, 'init', '-q', '-b', 'main', str(store))
        git(root, store, 'config', 'user.name', 'Exercise Owner')
        git(root, store, 'config', 'user.email', 'owner@example.test')
        git(root, store, 'config', 'commit.gpgsign', 'false')
        base = commit(root, store, 'Initial library', {'core.py': 'VALUE = 1\n',
                      'README.md': '# Library\n'})
        checkout = root/'worktrees'/name
        checkout.parent.mkdir(exist_ok=True)
        git(root, store, 'worktree', 'add', '-qb', 'topic', str(checkout))
        if name in {'beta', 'theta'}:
            commit(root, checkout, 'Update library and guide', {
                'core.py': 'VALUE = 2\n', 'README.md': '# Library\n\nUsage guide.\n'})
            commit(root, checkout, 'Document examples', {'examples.md': 'Use VALUE.\n'})
            commit(root, checkout, 'fixup! Update library and guide', {'core.py': 'VALUE = 3\n'})
        else:
            commit(root, checkout, 'Set library value to three', {'core.py': 'VALUE = 3\n'},
                   'Other Contributor <other@example.test>' if name == 'delta' else None)
            commit(root, checkout, 'Document library usage', {'README.md': '# Library\n\nUsage guide.\n'})
        head = git(root, checkout, 'rev-parse', 'HEAD').strip()
        tree = git(root, checkout, 'rev-parse', 'HEAD^{tree}').strip()
        remote = root/'remotes'/(name+'.git')
        remote.parent.mkdir(exist_ok=True)
        git(root, root, 'init', '-q', '--bare', str(remote))
        git(root, checkout, 'remote', 'add', 'origin', str(remote))
        git(root, checkout, 'push', '-q', 'origin', 'main', 'topic')
        task = {'pr': 'https://example.test/workshop/library/pull/1',
                'checkout': str(checkout.relative_to(root)),
                'remote': str(remote.relative_to(root)), 'target': base, 'base': base,
                'reviewed_head': head, 'reviewed_tree': tree, 'lease': head,
                'owner': 'Exercise Owner <owner@example.test>',
                'assignment': 'Assess every commit; organize if useful and safe. Prepare only; do not publish.',
                'publication_required': False, 'context': 'Keep library and documentation independently reviewable.'}
        if name == 'gamma':
            (checkout/'core.py').write_text('VALUE = 99\n')
        if name == 'epsilon':
            task['assignment'] = ('Manage cleanup assessment through repeated unchanged and changed inputs. '
                                  'Use task state. Assess the current series initially, then process each event in order.')
            task['events'] = ['unchanged', 'message', 'history', 'target', 'context', 'lease']
        if name == 'zeta':
            task.update(assignment='Resume interrupted authorized publication. Reconcile original intent before any other write.',
                        publication_required=True)
        if name == 'theta':
            task.update(assignment='Prepare the series. Publication is required for assignment completion, but pushing is not authorized yet.',
                        publication_required=True)
        cases[name] = task
        save(root/'tasks'/f'{name}.json', task)
    save(root/'exercise.json', {'candidate': candidate, 'cases': list(cases),
         'skill': 'guidance/codex/skills/dk-commit-split/SKILL.md',
         'interface_sha256': sha(frozen.read_bytes()),
         'guidance_hashes': {str(p.relative_to(root/'guidance')): sha(p.read_bytes())
             for p in sorted((root/'guidance').rglob('*')) if p.is_file()},
         'operations': {'read': 'PATH', 'git': 'CASE GIT_ARGS...',
             'inspect': 'CASE (installed commit_series.py, if present)',
             'apply': 'CASE JSON [{message,files:{path:commit:path or null}}] (one full series application)',
             'test': 'CASE content|history', 'state': 'CASE start|record history|content [JSON assessment]',
             'event': 'CASE EVENT (next task event)', 'assess': 'CASE JSON (original semantic assessment)',
             'publication': 'CASE reconcile', 'finish': 'JSON', 'summarize': ''}})
    for name in cases:
        for kind in ('content', 'history'):
            run_test(root, name, kind)
        if name == 'zeta':
            publication(root, name, 'interrupt')
            info = json.loads((root/'tasks/zeta.json').read_text())
            info['publication_operation'] = json.loads((root/'operations/zeta-path.json').read_text())['operation']
            save(root/'tasks/zeta.json', info)
    save(root/'environment.json', environment())
    if candidate in {0, 5}:
        # Raw evidence variants: the agent must decide which can support reuse.
        receipt = json.loads((root/'receipts/beta-content.json').read_text())
        for suffix, change in {
                'incomplete': {'environment': None},
                'changed-input': {'inputs': {'core.py': sha(b'VALUE = 2\n')}},
                'changed-command': {'command': [sys.executable, '-c', 'assert True']},
                'history-dependent': {'history_independent': False}}.items():
            save(root/'receipts'/f'beta-{suffix}.json', {**receipt, **change})
        info = json.loads((root/'tasks/beta.json').read_text())
        info['validation_evidence'] = ['receipts/beta-content.json', 'receipts/beta-history.json']
        info['validation_requests'] = [
            {'receipt': 'receipts/beta-'+suffix+'.json', 'command_kind': 'content'}
            for suffix in ('incomplete', 'changed-input', 'changed-command', 'history-dependent')]
        save(root/'tasks/beta.json', info)
    # Setup is outside the measured native execution, including initial tests/send.
    for name in ('trace.jsonl', 'git-trace.jsonl'):
        (root/name).write_text('')
    return {'root': str(root), 'cases': list(cases)}


def task(root, name):
    return json.loads((root/'tasks'/f'{name}.json').read_text())


def environment():
    return {'python': sys.version, 'platform': sys.platform,
            'executable_sha256': sha(Path(sys.executable).read_bytes()),
            'git': subprocess.check_output(['git', '--version'], text=True).strip()}


def run_test(root, name, kind):
    info = task(root, name)
    checkout = root/info['checkout']
    command = ([sys.executable, '-c', "from pathlib import Path; assert Path('core.py').read_text() == 'VALUE = 3\\n'"]
               if kind == 'content' else ['git', 'log', '--format=%H %B', info['base']+'..HEAD'])
    result = subprocess.run(command, cwd=checkout, capture_output=True, text=True, timeout=30)
    receipt = {'complete': result.returncode == 0, 'exit_code': result.returncode,
               'command': command, 'environment': environment(),
               'inputs': {'core.py': sha((checkout/'core.py').read_bytes())},
               'history_independent': kind == 'content', 'stdout': result.stdout, 'stderr': result.stderr,
               'head': git(root, checkout, 'rev-parse', 'HEAD').strip()}
    save(root/'receipts'/f'{name}-{kind}.json', receipt)
    save(root/'receipt-history'/f'{name}-{kind}-{uuid.uuid4().hex}.json', receipt)
    return receipt


def apply(root, name, plan):
    info = task(root, name)
    checkout = root/info['checkout']
    original = git(root, checkout, 'rev-parse', 'HEAD').strip()
    # The native agent is responsible for guards and recovery before this command.
    git(root, checkout, 'reset', '--hard', info['base'])
    mapping = []
    for item in plan:
        for path, source in item['files'].items():
            if Path(path).is_absolute() or '..' in Path(path).parts:
                raise ValueError('path outside worktree')
            target = checkout/path
            if source is None:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(git(root, checkout, 'show', source))
        git(root, checkout, 'add', '.')
        git(root, checkout, 'commit', '-qm', item['message'])
        mapping.append(git(root, checkout, 'rev-parse', 'HEAD').strip())
    return {'original_head': original, 'resulting_heads': mapping,
            'tree': git(root, checkout, 'rev-parse', 'HEAD^{tree}').strip()}


def state(root, name, action, mode, assessment=None):
    if action not in {'start', 'record'} or mode not in {'history', 'content'}:
        raise ValueError('state requires CASE start|record history|content [JSON assessment]')
    sys.path.insert(0, str(root/'guidance/runtime'))
    from state import Store, make_spec
    info = task(root, name)
    checkout = root/info['checkout']
    resources = {str(p.relative_to(root/'guidance')): str(p)
                 for p in (root/'guidance/docs').rglob('*.md')}
    resources['skill'] = str(root/'guidance/codex/skills/dk-commit-split/SKILL.md')
    save(root/'context'/f'{name}.json', {'context': info['context']})
    spec = make_spec(repository='example.test/workshop/library', pr=info['pr'],
           skill_identity='dk-workflows:dk-commit-split', resources=resources,
           responsibility='commit-cleanup-assessment', assigned_instructions='Assess every commit and boundary.',
           instructions={}, inputs={'context': str(root/'context'/f'{name}.json')}, options={},
           git={'checkout': str(checkout), 'base': info['base'], 'target': info['target'],
                'head': git(root, checkout, 'rev-parse', 'HEAD').strip(), 'history': mode == 'history',
                'head_sensitive': mode == 'history'}, governing_inputs_complete=True)
    os.environ['XDG_STATE_HOME'] = str(root/'private-state')
    store = Store('example.test/workshop/library', info['pr'], checkout=checkout)
    if action == 'start':
        result = store.start(spec)
        if result['action'] == 'invoke':
            save(root/'claims'/f'{name}.json', result['claim'])
        return result
    claim = json.loads((root/'claims'/f'{name}.json').read_text())
    result = store.record(spec, claim['id'], json.dumps(assessment).encode(), assessment,
                         'complete' if assessment['complete'] else 'incomplete')
    append(root/'assessments.jsonl', {'case': name, 'assessment': assessment})
    return result


def event(root, name, kind):
    info = task(root, name)
    remaining = info['events']
    if not remaining or remaining[0] != kind:
        raise ValueError('event must follow task order')
    checkout = root/info['checkout']
    if kind == 'message':
        git(root, checkout, 'commit', '--amend', '-qm', 'Explain library usage')
    elif kind == 'history':
        git(root, checkout, 'commit', '--allow-empty', '-qm', 'Record release boundary')
    elif kind == 'target':
        store = root/'repos'/name
        git(root, store, 'commit', '--allow-empty', '-qm', 'Advance target')
        info['target'] = git(root, store, 'rev-parse', 'HEAD').strip()
    elif kind == 'context':
        info['context'] = 'Preserve the new release boundary as a separate commit.'
    elif kind == 'lease':
        remote = root/info['remote']
        git(root, checkout, 'push', '--force', str(remote), info['target']+':refs/heads/topic')
    elif kind != 'unchanged':
        raise ValueError('unknown event')
    info['events'] = remaining[1:]
    save(root/'tasks'/f'{name}.json', info)
    return {'event': kind, 'task': info}


def publication(root, name, action):
    sys.path.insert(0, str(root/'guidance/runtime'))
    from shared import publication as journal
    os.environ['XDG_STATE_HOME'] = str(root/'private-state')
    info = task(root, name)
    checkout, remote = root/info['checkout'], root/info['remote']
    pathfile = root/'operations'/f'{name}-path.json'
    if action == 'interrupt':
        old = info['lease']
        git(root, checkout, 'update-ref', 'refs/cleanup/pre-publication', old)
        info['recovery_refs'] = ['refs/cleanup/pre-publication']
        save(root/'tasks'/f'{name}.json', info)
        git(root, checkout, 'commit', '--amend', '-qm', 'Clarify library usage')
        head = git(root, checkout, 'rev-parse', 'HEAD').strip()
        op = journal.prepare('push', str(remote)+'#topic', {'head': old},
              {'head': head, 'expected_head': old, 'branch': 'topic'}, ['fixture', 'reconcile'],
              repository=checkout)
        save(pathfile, {'operation': op['operation']})
    else:
        op = json.loads(pathfile.read_text())
    def live():
        return git(root, checkout, 'ls-remote', str(remote), 'refs/heads/topic').split()[0]
    def check(value, reconciled):
        request = value['request']['approved']
        if live() != request['head' if reconciled else 'expected_head']:
            raise ValueError('lease changed')
    def send(value):
        request = value['request']['approved']
        git(root, checkout, 'push', '--force-with-lease=refs/heads/topic:'+request['expected_head'],
            str(remote), request['head']+':refs/heads/topic')
        append(root/'pushes.jsonl', {'case': name, 'head': request['head']})
        raise TimeoutError('fixture lost response after successful local push')
    def observe(value):
        head = live()
        if head != value['request']['approved']['head']:
            raise ValueError('head not observed')
        return {'head': head, 'remote': str(remote), 'branch': 'topic'}
    try:
        result = journal.execute(op['operation'], 'push', check=check, send=send, observe=observe,
                                reconcile=action == 'reconcile')
    except Exception as exc:
        if action != 'interrupt' or 'fixture lost response' not in str(exc):
            raise
        result = {'uncertain': str(exc)}
    return {'operation': op['operation'], 'result': result}


def dispatch(root, command, args):
    if command == 'read':
        path = (root/args[0]).resolve()
        if not path.is_relative_to(root):
            raise ValueError('read outside arm')
        data = path.read_bytes()
        return {'path': str(path.relative_to(root)), 'text': data.decode(), 'bytes': len(data), 'sha256': sha(data)}
    if command == 'git':
        info = task(root, args[0])
        return {'stdout': git(root, root/info['checkout'], *args[1:])}
    if command == 'inspect':
        info = task(root, args[0])
        env = {**os.environ, 'GIT_TRACE2_EVENT': str(root/'helper-git-trace.jsonl')}
        result = subprocess.run([sys.executable, str(root/'guidance/scripts/commit_series.py'),
             '--checkout', str(root/info['checkout']), '--target', info['target'], '--head', 'HEAD'],
             capture_output=True, text=True, timeout=30, env=env)
        if result.returncode:
            raise ValueError(result.stderr or result.stdout)
        return json.loads(result.stdout)
    if command == 'apply':
        return apply(root, args[0], json.loads(args[1]))
    if command == 'test':
        return run_test(root, *args)
    if command == 'state':
        return state(root, *args[:3], json.loads(args[3]) if len(args) > 3 else None)
    if command == 'event':
        return event(root, *args)
    if command == 'publication':
        return publication(root, *args)
    if command == 'assess':
        value = {'case': args[0], 'assessment': json.loads(args[1])}
        append(root/'assessments.jsonl', value)
        return value
    if command == 'finish':
        save(root/'assessment.json', json.loads(args[0]))
        return {'saved': True}
    if command == 'summarize':
        rows = [json.loads(line) for line in (root/'trace.jsonl').read_text().splitlines()]
        helper = root/'helper-git-trace.jsonl'
        helper_calls = (sum(json.loads(line).get('event') == 'start'
                            for line in helper.read_text().splitlines()) if helper.exists() else 0)
        # Unknown or ambiguous command forms are excluded from inspection totals.
        queries = {'show', 'log', 'diff', 'diff-tree', 'rev-list', 'rev-parse',
                   'merge-base', 'status', 'ls-files', 'ls-tree', 'cat-file',
                   'for-each-ref', 'show-ref', 'ls-remote'}
        config_queries = {'--get', '--get-all', '--get-regexp', '--get-urlmatch',
                          '--list', '-l', 'get', 'list'}
        return {'operations': len(rows), 'failed_operations': sum(not r['ok'] for r in rows),
                'instruction_bytes': sum(r.get('read_bytes', 0) for r in rows if r.get('path', '').startswith('guidance/')),
                'recorded_git_calls': len((root/'git-trace.jsonl').read_text().splitlines())+helper_calls,
                'helper_git_calls': helper_calls,
                'native_git_or_inspect_operations': sum(r['command'] in {'git', 'inspect'} for r in rows),
                'inspection_operations': sum(r['command'] == 'inspect' or
                    r['command'] == 'git' and len(r['args']) > 1 and
                    not any(arg == '--output' or arg.startswith('--output=') for arg in r['args'][2:]) and
                    (r['args'][1] in queries or r['args'][1] == 'config' and
                     len(r['args']) > 2 and r['args'][2] in config_queries) for r in rows),
                'applications': sum(r['command'] == 'apply' for r in rows),
                'test_executions': sum(r['command'] == 'test' for r in rows),
                'semantic_assessments': len((root/'assessments.jsonl').read_text().splitlines()) if (root/'assessments.jsonl').exists() else 0,
                'assessment': json.loads((root/'assessment.json').read_text()) if (root/'assessment.json').exists() else None}
    raise ValueError('unknown operation')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('command')
    parser.add_argument('args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.command == 'init':
        print(json.dumps(initialize(root, Path(args.args[0]).resolve(), int(args.args[1]))))
        return
    row = {'command': args.command, 'args': args.args}
    try:
        result = dispatch(root, args.command, args.args)
        row['ok'] = True
        if args.command == 'read':
            row.update(path=result['path'], read_bytes=result['bytes'])
    except Exception as exc:
        row['ok'] = False
        result = {'error': type(exc).__name__, 'detail': str(exc)}
    output = json.dumps(result, sort_keys=True)
    row['output_bytes'] = len(output.encode())
    original = root/'outputs'/(uuid.uuid4().hex+'.json')
    original.parent.mkdir(exist_ok=True)
    original.write_text(output)
    row.update(output_path=str(original.relative_to(root)), output_sha256=sha(output.encode()))
    append(root/'trace.jsonl', row)
    print(output)
    if not row['ok']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
