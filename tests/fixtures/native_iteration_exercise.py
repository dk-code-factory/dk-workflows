"""Opt-in offline operations for a native coordinator; never chooses its next step.

The native agent chooses reads, collection, claims, invocation and recording. This
fixture records only observable operations, not tokens or hidden context reads.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
BASE = '620f8df51b1066e44faeb69878235adcd3c49317'
REPO = 'github.com/fixture/iteration'
PR = 'https://github.com/fixture/iteration/pull/7'
TASKS = ('reviewer-a', 'reviewer-b', 'history-review', 'optional-review',
         'scope-partition', 'series-cleanup', 'ci', 'publication')


def encoded(value):
    return json.dumps(value, sort_keys=True).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run(argv, **kwargs):
    return subprocess.check_output(list(map(str, argv)), stderr=subprocess.PIPE, **kwargs)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(value))


def initialize(root, guidance, runtime=BASE, guidance_revision=BASE):
    if root.exists():
        raise ValueError('Use a new external directory; never reset an exercise arm')
    if root.is_relative_to(ROOT):
        raise ValueError('Exercise state must be outside the repository')
    if guidance not in ('before','after'):
        raise ValueError('Guidance must be before or after')
    runtime = run(['git', '-C', ROOT, 'rev-parse', '--verify', runtime+'^{commit}']).decode().strip()
    guidance_revision = run(['git', '-C', ROOT, 'rev-parse', '--verify',
                             guidance_revision+'^{commit}']).decode().strip()
    runtime_paths = run(['git', '-C', ROOT, 'ls-tree', '-r', '--name-only', runtime]).decode().splitlines()
    root.mkdir(parents=True)
    interface = Path(__file__).read_bytes()
    (root/'frozen').mkdir()
    (root/'frozen/interface.py').write_bytes(interface)
    # Freeze the runtime's own dependencies, rather than relying on imports from
    # the launching checkout or a particular historical state's import graph.
    runtime_hashes = {}
    for name in runtime_paths:
        if name != 'scripts/dk.py' and not (name.startswith('runtime/') and name.endswith('.py')
                                           and 'tests' not in Path(name).parts):
            continue
        path = root/'frozen'/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(run(['git', '-C', ROOT, 'show', runtime+':'+name]))
        runtime_hashes[name] = sha(path.read_bytes())
    markdown = ([str(p.relative_to(ROOT)) for folder in ('runtime', 'docs', 'codex')
                 for p in sorted((ROOT/folder).rglob('*.md'))] if guidance == 'after' else
                run(['git', '-C', ROOT, 'ls-tree', '-r', '--name-only', guidance_revision]).decode().splitlines())
    for name in markdown:
        if not name.endswith('.md') or not name.startswith(('docs/', 'runtime/', 'codex/')):
            continue
        path = root/'guidance'/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT/name).read_bytes() if guidance == 'after' else
                         run(['git', '-C', ROOT, 'show', guidance_revision+':'+name]))
    checkout = root/'checkout'
    checkout.mkdir()
    def git(*args):
        return run(['git', '-C', checkout, *args], env={**os.environ,
                   'GIT_AUTHOR_DATE':'2026-11-03T12:00:00Z',
                   'GIT_COMMITTER_DATE':'2026-11-03T12:00:00Z'}).decode().strip()
    git('init', '-q'); git('config', 'user.name', 'Fixture'); git('config', 'user.email', 'fixture@example.test')
    git('config', 'commit.gpgsign', 'false'); git('config', 'core.hooksPath', str(root/'no-hooks'))
    (checkout/'code.py').write_text('VALUE = 1\n')
    git('add', '.'); git('commit', '-qm', 'base'); base = git('rev-parse', 'HEAD')
    (checkout/'code.py').write_text('VALUE = 2\n')
    git('commit', '-qam', 'return the settled value'); old = git('rev-parse', 'HEAD')
    # A commit-message rewrite with an identical complete tree refreshes history consumers.
    git('commit', '--amend', '-qm', 'Return the settled value'); head = git('rev-parse', 'HEAD')
    save(root/'pins.json', {'base':base, 'reviewed_head':old, 'head':head,
                           'tree':git('rev-parse', 'HEAD^{tree}'), 'runtime':runtime})
    source = {'head':head, 'scope':'Return two.', 'feedback':[{'id':'F1', 'text':'Rename the value.'}],
              'decisions':[{'source':PR+'#issuecomment-9',
                            'text':'Accept F1 without changing the name; VALUE is the existing public interface.'}],
              'instructions':'Keep VALUE public. No remote writes in this exercise.'}
    save(root/'sources.json', source)
    (root/'governing.txt').write_text('Return two. Keep VALUE public. No remote writes.\n')
    # Explicit selections keep the fixture independent of the operator's private
    # preference catalog and preserve the handoff even when no records apply.
    for mode in ('implementation', 'review'):
        save(root/('preferences-'+mode+'.json'), {
            'selection': {'repository': REPO, 'mode': mode, 'paths': ['code.py'], 'records': []},
            'condition_context': {}, 'decisions': []})
    (root/'skills').mkdir()
    for task in TASKS:
        name = 'series' if task in ('scope-partition', 'series-cleanup') else task
        (root/'skills'/(name+'.md')).write_text('Offline fixture role '+name+'. Read sources.\n')
    for task in TASKS:
        name = 'series' if task in ('scope-partition', 'series-cleanup') else task
        spec = {'repository':REPO, 'pr':PR,
                'skill':{'identity':'fixture:'+name, 'resources':{'skill':str(root/'skills'/(name+'.md'))}},
                'task':{'id':task, 'instructions':'Assess '+task+' for the pinned fixture scope.'},
                'instructions':{'user':str(root/'governing.txt')},
                'inputs':{'preferences':str(root/('preferences-'+
                           ('review' if task in TASKS[:4] else 'implementation')+'.json'))},
                'options':{}, 'governing_inputs_complete':True,
                'cacheable':task in TASKS[:4],
                'git':{'checkout':str(checkout), 'target':base, 'base':base, 'head':old,
                       'history':task == 'history-review', 'head_sensitive':task == 'ci'}}
        save(root/'specs'/(task+'.json'), spec)
    # Retained attempts are an initial fixture, not native-agent invocations.
    for task in TASKS[:4]:
        started = helper(root, ['start', '--spec', task, '--run', 'exercise', '--cycle', 'c1'])
        output = {'complete':task != 'optional-review', 'findings':
                  [{'id':'F1', 'text':'Rename the value.'}] if task == 'reviewer-a' else [],
                  'reason':'Fixture optional provider unavailable' if task == 'optional-review' else ''}
        path = root/'initial'/ (task+'.json'); save(path, output)
        args = ['record', '--spec', task, '--claim', started['claim']['id'],
                '--original', str(path), '--normalized', str(path),
                '--status', 'complete' if output['complete'] else 'incomplete']
        if task == 'reviewer-a':
            progress = root/'initial/progress.json'
            save(progress, {'finding':'F1', 'disposition':'accepted', 'source':PR+'#issuecomment-9',
                            'reason':source['decisions'][0]['text']})
            args += ['--progress', str(progress)]
        helper(root, args)
    for task in TASKS:
        path = root/'specs'/(task+'.json'); spec = json.loads(path.read_text())
        spec['git']['head'] = head; save(path, spec)
    with (root/'skills/reviewer-b.md').open('a') as out:
        out.write('Installed resource changed after retained review.\n')
    save(root/'exercise.json', {'run':'exercise', 'prior_cycle':'c1', 'tasks':list(TASKS),
                               'required_reviewers':list(TASKS[:3]), 'optional_reviewers':['optional-review'],
                               'prior_worker_stopped':True, 'remote_writes_permitted':False,
                               'guidance_arm':guidance,
                               'guidance_revision':guidance_revision if guidance == 'before' else None,
                               'runtime':runtime, 'runtime_hashes':runtime_hashes,
                               'source_sha256':sha((root/'sources.json').read_bytes()),
                               'preference_files':{'implementation':'preferences-implementation.json',
                                                   'review':'preferences-review.json'},
                               'interface_sha256':sha(interface),
                               'guidance_hashes':{str(p.relative_to(root/'guidance')):sha(p.read_bytes())
                                                  for p in sorted((root/'guidance').rglob('*.md'))}})
    return {'root':str(root), 'guidance':guidance, 'pins':json.loads((root/'pins.json').read_text())}


def helper(root, arguments):
    arguments = list(arguments)
    for index, arg in enumerate(arguments):
        if arg == '--spec':
            task = arguments[index+1]
            if task not in TASKS:
                raise ValueError('Unknown task')
            arguments[index+1] = str(root/'specs'/(task+'.json'))
    env = {**os.environ, 'XDG_STATE_HOME':str(root/'state'), 'PYTHONDONTWRITEBYTECODE':'1'}
    return json.loads(run([sys.executable, root/'frozen/scripts/dk.py', '--repository', REPO,
                          '--pr', PR, '--checkout', root/'checkout', *arguments], env=env))


def operate(root, command, args):
    if command == 'read':
        path = Path(args[0])
        if not path.is_absolute(): path = root/path
        path = path.resolve()
        if not path.is_relative_to(root): raise ValueError('Read only this arm')
        return path.read_text()
    if command == 'resolve':
        return {task:{'identity':json.loads((root/'specs'/(task+'.json')).read_text())['skill']['identity'],
                      'installed':True} for task in TASKS}
    if command == 'collect':
        data = (root/'sources.json').read_bytes(); path = root/'snapshot.json'
        path.write_bytes(data)
        receipt = {'path':str(path), 'sha256':sha(data), 'bytes':len(data), 'complete':True,
                   'pr':PR, 'head':json.loads(data)['head']}
        save(root/'receipt.json', receipt)
        return receipt
    if command == 'verify':
        receipt = json.loads((root/'receipt.json').read_text())
        same = sha((root/'sources.json').read_bytes()) == receipt['sha256']
        return {'complete':same, 'fresh':same, 'receipt':receipt}
    if command == 'state':
        return helper(root, args)
    if command == 'invoke':
        task, claim = args
        if task not in TASKS: raise ValueError('Unknown task')
        manifest = next((root/'state').rglob('manifest.json'))
        entries = json.loads(manifest.read_text())['entries'].values()
        if not any(e.get('claim', {}).get('id') == claim and
                   e['components']['task']['id'] == task for e in entries):
            raise ValueError('Invoke requires this task\'s live claim')
        complete = task != 'optional-review'
        result = {'complete':complete, 'findings':[], 'task':task,
                  'head':json.loads((root/'pins.json').read_text())['head'],
                  'blockers':[] if complete else ['Fixture optional provider unavailable']}
        if task in ('scope-partition', 'series-cleanup'):
            result['outcome'] = 'retain' if task == 'scope-partition' else 'no-op'
        if task == 'ci': result['result'] = 'success'
        if task == 'publication': result.update(result='reconciled-no-op', remote_writes=0)
        path = root/'outputs'/(claim+'.json'); save(path, result)
        return {'path':str(path), 'result':result}
    if command == 'finish':
        result = json.loads(args[0]); save(root/'native-result.json', result)
        return {'saved':True, 'path':str(root/'native-result.json')}
    if command == 'summarize':
        trace = [json.loads(line) for line in (root/'trace.jsonl').read_text().splitlines()]
        counts = {kind:sum(row['command'] == kind for row in trace)
                  for kind in ('read','resolve','collect','verify','state','invoke','finish')}
        reads = [row for row in trace if row['command'] == 'read' and row.get('ok', True)]
        guidance = [row for row in reads
                    if row.get('read_path', row['args'][0]).startswith('guidance/')]
        manifest = next((root/'state').rglob('manifest.json'))
        state = json.loads(manifest.read_text())['runs']['exercise']
        return {'operation_counts':counts,
                'failed_operations':sum(not row.get('ok', True) for row in trace),
                'guidance_reads':len(guidance),
                'guidance_read_bytes':sum(row['output_bytes'] for row in guidance),
                'evidence_reads':len(reads)-len(guidance),
                'evidence_read_bytes':sum(row['output_bytes'] for row in reads if row not in guidance),
                'output_bytes':sum(row['output_bytes'] for row in trace if row['command'] != 'summarize'),
                'task_invocations':[row['args'][0] for row in trace
                                    if row['command'] == 'invoke' and row.get('ok', True)],
                'counters':{key:state[key] for key in ('cycles','retries','ci_repairs','ci_reruns')},
                'pins':json.loads((root/'pins.json').read_text()),
                'source_sha256':sha((root/'sources.json').read_bytes()),
                'assessment':json.loads((root/'native-result.json').read_text())}
    raise ValueError('Unknown command')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('command', choices=['init','read','resolve','collect','verify','state','invoke','finish','summarize'])
    parser.add_argument('args', nargs=argparse.REMAINDER)
    options = parser.parse_args(); root = options.root.resolve()
    if options.command == 'init':
        result = initialize(root, *options.args)
    else:
        ok = True
        try:
            result = operate(root, options.command, options.args)
        except (ValueError, OSError, LookupError, RecursionError, subprocess.CalledProcessError) as error:
            ok = False
            result = {'error':type(error).__name__, 'detail':str(error)}
        data = result.encode() if isinstance(result, str) else encoded(result)
        row = {'command':options.command, 'args':options.args, 'output_bytes':len(data), 'ok':ok}
        if ok and options.command == 'read':
            row['read_path'] = str((root/options.args[0]).resolve().relative_to(root))
        with (root/'trace.jsonl').open('a') as out:
            out.write(json.dumps(row)+'\n')
        if not ok:
            print(json.dumps(result, sort_keys=True))
            raise SystemExit(2)
    print(result if isinstance(result, str) else json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
