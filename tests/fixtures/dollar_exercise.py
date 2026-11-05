"""Frozen native dollar workloads: inputs, production transports and outcome checks.

This test-only interface never selects the agent's next operation. Native agents
may use the frozen helpers directly and author wrappers in artifacts/. Read-only
frozen input hashes and private traces are verified after each arm.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
REPO = 'github.com/fixture/dollars'
PR = 'https://github.com/fixture/dollars/pull/7'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True)+'\n')


def run(argv, **kwargs):
    return subprocess.check_output(list(map(str, argv)), stderr=subprocess.PIPE, **kwargs).decode().strip()


def initialize(root, source, experiment):
    if root.exists():
        raise ValueError('Never reset a trial directory')
    root.mkdir(parents=True)
    for folder in ('runtime', 'scripts', 'docs', 'codex'):
        shutil.copytree(source/folder, root/'frozen'/folder,
                        ignore=shutil.ignore_patterns('__pycache__', 'tests', 'evidence'))
    shutil.copy2(Path(__file__), root/'interface.py')
    (root/'frozen/.codex-plugin').mkdir()
    shutil.copy2(source/'.codex-plugin/plugin.json', root/'frozen/.codex-plugin/plugin.json')
    (root/'artifacts').mkdir()
    (root/'checkout').mkdir()
    checkout = root/'checkout'
    env = {**os.environ, 'GIT_AUTHOR_DATE': '2026-11-05T12:00:00Z',
           'GIT_COMMITTER_DATE': '2026-11-05T12:00:00Z'}
    def git(*args):
        return run(['git', '-C', checkout, *args], env=env)
    git('init', '-q'); git('config', 'user.name', 'Fixture'); git('config', 'user.email', 'fixture@example.test')
    git('config', 'commit.gpgsign', 'false'); git('config', 'core.hooksPath', str(root/'no-hooks'))
    (checkout/'value.py').write_text('VALUE = 1\n')
    git('add', '.'); git('commit', '-qm', 'Fixture base'); base = git('rev-parse', 'HEAD')
    (checkout/'value.py').write_text('VALUE = 2\n')
    git('commit', '-qam', 'Return two'); head = git('rev-parse', 'HEAD')
    (root/'inputs').mkdir()
    (root/'inputs/instructions.txt').write_text('Keep VALUE public. No remote writes.\r\n')
    (root/'inputs/skill.md').write_text('Assess the pinned fixture value and retain original findings.\n')
    save(root/'inputs/preferences.json', {'records': [{'id': 'P1', 'scope': 'implementation',
         'paths': ['value.py'], 'instruction': 'Keep VALUE public.'}], 'applicable_paths': ['value.py'],
         'condition_decisions': [{'id': 'P1', 'applies': True}]})
    definition = {'skill_identity': 'fixture:review', 'resources': {'skill': str(root/'inputs/skill.md')},
                  'responsibility': 'content-review', 'assigned_instructions': 'Assess VALUE and retained F1.',
                  'instructions': {'user': str(root/'inputs/instructions.txt')},
                  'inputs': {'preferences': str(root/'inputs/preferences.json')},
                  'git': {'checkout': str(checkout), 'base': base, 'target': base, 'head': head},
                  'options': {}, 'governing_inputs_complete': True}
    save(root/'inputs/definition.json', definition)
    sys.path.insert(0, str(root/'frozen/runtime'))
    import state
    prior_env = {key: os.environ.get(key) for key in ('XDG_STATE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME')}
    for key, folder in [('XDG_STATE_HOME', 'state'), ('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data')]:
        os.environ[key] = str(root/folder)
    store = state.Store(REPO, PR, checkout=checkout)
    specs = {}
    source = PR+'#issuecomment-9'
    acceptance = 'Retain VALUE exactly; accept F1 without renaming.\r\n'
    try:
        for task in ('stable', 'changed', 'unfinished'):
            item = dict(definition, responsibility=task)
            resource = root/'inputs'/(task+'.md'); resource.write_text('Fixture '+task+' reviewer.\n')
            item['resources'] = {'skill': str(resource)}
            spec = state.make_spec(repository=REPO, pr=PR, **item)
            specs[task] = spec
            save(root/'inputs'/(task+'-spec.json'), spec)
            if experiment in (2, 3):
                started = store.start(spec, run='retained', cycle='c1')
                if task != 'unfinished':
                    store.record(spec, started['claim']['id'], b'Original F1: retain public VALUE.',
                                 {'complete': True, 'findings': [{'id': 'F1', 'text': 'Rename VALUE.'}]},
                                 'complete', progress={'finding': 'F1', 'acceptance': acceptance,
                                                       'source': source, 'task': task})
        if experiment in (2, 3):
            with (root/'inputs/changed.md').open('a') as out:
                out.write('Changed governing review rule; refresh this task.\n')
            if experiment == 3:
                with store.locked() as manifest:
                    progress = manifest['runs']['retained']['progress']
                    seed = progress[:]
                    progress[:] = [{'claim': 'historical-'+str(i), 'value': {
                        'task': 'unrelated', 'finding': 'N'+str(i), 'disposition': 'deferred',
                        'source': PR+'#discussion_r'+str(i),
                        'reason': 'Historical unrelated scope retained for audit.'}} for i in range(240)] + seed
                    state.write(store.manifest, manifest)
        save(root/'initial-manifest.json', state.read(store.manifest) if store.manifest.exists() else {})
    finally:
        for key, value in prior_env.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
    task = {
        1: 'Complete a task lifecycle using inputs/definition.json: define a spec, claim it in run lifecycle, save native handle {host:codex,id:fixture-native-1}, retain progress with finding F1 and its exact acceptance/source from inputs/scenario.json, record complete original findings, demonstrate unchanged reuse, and submit an invalid request/command that does not mutate state. Baseline may author a wrapper and save its handle/progress in artifacts; use supported candidate operations where available.',
        2: 'Resume run retained. Inspect current governing specs/inputs and retained original findings. Retain reusable stable work, refresh changed inputs in a new cycle, and finish the unfinished claim after reconciling the stopped fixture worker. Preserve run/cycle/retry/CI budgets and exact acceptance/source. The prior worker is stopped and performed no domain writes. Claim recovery is authorized for this fixture only. Do not infer missing governing inputs or authorize remote writes.',
        3: 'Assess the large retained history in run retained, retrieve relevant stable-task dispositions and exact F1 acceptance plus complete original evidence. Preserve unchanged stable work. Append progress for the already claimed unfinished task, then retrieve the appended progress without claiming complete review coverage: changed and unfinished tasks are not complete. Report precise coverage, acceptance/source, and retained progress counts. Do not recover or finish claims.',
        4: 'Observe deterministic pending CI for the fixture exact head, interrupt after retaining pending evidence, resume using the same observer/deadline and consume the terminal result. Use the local gh transport in bin. Preserve ownership and do not treat head_changed, denied access, timeout, no_checks or failure as success. Describe identity, deadline, interruption and terminal evidence in the assessment.',
        5: 'Start without command knowledge. Discover the frozen bookkeeping CLI from its supported reference/help/source. Execute an ordinary lifecycle using inputs/definition.json, generate one configured before_cycle hook spec and one configured finalizer spec using inputs/hook-definition.json and inputs/finalizer-definition.json and run discovery/cycle c1. Claim and record those tasks without consuming extra reviewer budgets. Demonstrate ordinary reuse and report generated task identities and permissions accurately.',
    }[experiment]
    scenario = {'experiment': experiment, 'repository': REPO, 'pr': PR, 'base': base, 'head': head,
                'assignment': task, 'source': source, 'acceptance': acceptance,
                'native_handle': {'host': 'codex', 'id': 'fixture-native-1'},
                'prior_worker_stopped': True, 'prior_domain_writes': [], 'remote_writes_permitted': False}
    save(root/'inputs/scenario.json', scenario)
    if experiment == 4:
        initialize_ci(root, head)
    if experiment == 5:
        initialize_config(root, definition)
    frozen = {str(p.relative_to(root)): sha(p.read_bytes()) for p in sorted((root/'frozen').rglob('*')) if p.is_file()}
    inputs = {str(p.relative_to(root)): sha(p.read_bytes().replace(str(root).encode(), b'ARM'))
              for p in sorted((root/'inputs').rglob('*')) if p.is_file()}
    save(root/'freeze.json', {'files': frozen, 'input_hash': sha(json.dumps(inputs, sort_keys=True).encode()),
         'runtime': sha(json.dumps({k:v for k,v in frozen.items() if k.endswith('.py')}, sort_keys=True).encode()),
         'guidance': sha(json.dumps({k:v for k,v in frozen.items() if k.endswith('.md')}, sort_keys=True).encode()),
         'resources': sha(json.dumps(inputs, sort_keys=True).encode())})
    return scenario


def initialize_config(root, definition):
    sys.path.insert(0, str(root/'frozen/runtime'))
    import config
    for kind, identity in [('hook', 'dk-workflows:dk-pr-fix'), ('finalizer', 'fixture:finalizer')]:
        selected = dict(definition, skill_identity=identity)
        selected.pop('responsibility'); selected.pop('assigned_instructions')
        save(root/'inputs'/(kind+'-definition.json'), selected)
    save(root/'config/dk-workflows/v2/user.json', {
        'cycle_hooks': {'before_cycle': [{'id': 'fixture-hook', 'instructions': 'Retain fixture hook receipt.'}]},
        'finalizers': ['/skill/fixture:finalizer']})


def initialize_ci(root, head):
    (root/'bin').mkdir()
    transport = '''#!/usr/bin/env python3
import json,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
args=sys.argv[1:]
p=root/'ci-transport.json'
state=json.loads(p.read_text()) if p.exists() else {'checks':0}
if args[:2]==['pr','view']:
 print(json.dumps({'headRefOid':HEAD_VALUE,'url':PR_VALUE}))
elif args[:2]==['pr','checks']:
 state['checks']+=1;p.write_text(json.dumps(state))
 bucket='pending' if state['checks']<=2 else 'pass'
 print(json.dumps([{'name':'fixture','bucket':bucket,'state':'IN_PROGRESS' if bucket=='pending' else 'SUCCESS','link':'https://github.com/fixture/dollars/actions/runs/1/job/1','workflow':'Validate','startedAt':'2026-11-05T12:00:00Z','completedAt':None}]))
else:
 print('Unsupported fixture gh call',file=sys.stderr);sys.exit(2)
'''.replace('HEAD_VALUE', repr(head)).replace('PR_VALUE', repr(PR))
    path = root/'bin/gh'; path.write_text(transport); path.chmod(0o755)


def verify(root):
    frozen = json.loads((root/'freeze.json').read_text())
    intact = all((root/name).is_file() and sha((root/name).read_bytes()) == digest
                 for name, digest in frozen['files'].items())
    paths = list((root/'state/dk-workflows/v2').glob('*/manifest.json'))
    manifest = json.loads(paths[0].read_text()) if paths else {'entries': {}, 'runs': {}}
    scenario = json.loads((root/'inputs/scenario.json').read_text())
    results = []
    for entry in manifest['entries'].values():
        reference = entry.get('result')
        if reference:
            result = json.loads((paths[0].parent/'results'/reference['file']).read_text())
            results.append({'task': entry['components']['task']['id'], 'status': result['status'],
                            'normalized': result['normalized'],
                            'original': base64.b64decode(result['original']).decode(errors='replace')})
    return {'frozen_intact': intact, 'manifest': manifest, 'results': results,
            'assessment': json.loads((root/'artifacts/assessment.json').read_text())
                          if (root/'artifacts/assessment.json').exists() else None,
            'scenario': scenario}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('command', choices=['init', 'verify'])
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--experiment', type=int, choices=range(1,6))
    args = parser.parse_args()
    print(json.dumps(initialize(args.root, args.source, args.experiment) if args.command == 'init'
                     else verify(args.root), sort_keys=True))


if __name__ == '__main__':
    main()
