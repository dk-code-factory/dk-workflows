#!/usr/bin/env python3
"""Prepare reviewer arms for the agent-context dollar trial (#256).

Test-only. Never imported by production code; never launches models.
`init` builds an isolated greeting PR, captures governing inputs and claims one
content-review task through the real `dk.py prepare`. The baseline handoff is
that prepare response's assignment and checklist, verbatim. The candidate
handoff replaces the assignment with a hand-built agent-context document
projected from the same captured inputs; the checklist is identical.
`get-item` is the trial-only stand-in for the proposed resolver. `check` is the
correctness oracle, and `collect` reads native usage after a completed arm.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
REPO = 'github.com/owner/repo'
PR = 'https://github.com/owner/repo/pull/7'
TASK_ID = '/task/pr/github.com/owner/repo/7/content-review'
ARMS = ('baseline', 'candidate')
ORDERS = [['baseline', 'candidate'], ['candidate', 'baseline'], ['baseline', 'candidate']]
GLOBAL_RULES = ('Report each finding with file, line, the violated requirement and a concrete '
                'reproduction. Do not report style preferences as defects.\n')
AGENTS = 'Keep changes within the greeting feature. Run python3 -m unittest discover.\n'
CONTEXT = {'purpose': 'Return a safe HTML greeting and independent per-call history.',
           'decisions': [
               {'id': 'escape-names', 'statement': 'User-supplied names are rendered as HTML text: '
                'escape &, <, >, " and \' before insertion.'},
               {'id': 'independent-history', 'statement': 'Each call returns a list containing only that '
                "call's name; no state is shared between calls."}]}
ASSIGNMENT = '''You are a native reviewer in a controlled offline dollar trial. Work only within ARM.
Read ARM/handoff.json and follow ARM/reviewer.md. Review the pull request checked out at ARM/checkout.
Write your report to ARM/artifacts/report.json, then reply with one line: done.
Do not edit ARM/checkout, launch agents, access a network, change personal settings or write outside ARM/artifacts.
'''
REVIEWER = {
    'baseline': '''# Trial reviewer

`handoff.json` holds `assignment` (your task definition) and `checklist`.
`assignment.task.instructions` is your task. `assignment.instructions` and
`assignment.inputs` map names to files holding governing instructions and context;
read them before reviewing. `assignment.git` pins the checkout, base and head.
''',
    'candidate': '''# Trial reviewer

`handoff.json` holds `context` (an agent context document) and `checklist`.
`context.governing` holds the instructions and decisions that govern you, complete.
`context.task.instructions` is your task. `context.evidence` lists optional reading
by address; read an item with `python3 ARM/bin/get-item --task TASK_ID ADDRESS`,
using `context.task.id` as TASK_ID. The checkout's HEAD is the pinned head and
`evidence/pr/diff` is the pinned diff.
''',
}
REPORT = '''
Review the complete diff against every governing instruction and decision.
Report JSON: {"complete": true|false, "findings": [{"title", "path", "line",
"requirement", "reproduction"}], "checklist": {ITEM_ID: EVIDENCE}}. Cover every
checklist item. Report no finding without a violated requirement.
'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2)+'\n')


def environment(root):
    keep = {k: v for k, v in os.environ.items() if not k.startswith(('GIT_', 'XDG_'))}
    return {**keep, 'XDG_STATE_HOME': str(root/'state'), 'XDG_CONFIG_HOME': str(root/'config'),
            'XDG_DATA_HOME': str(root/'data'), 'PYTHONDONTWRITEBYTECODE': '1',
            'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0',
            'GIT_AUTHOR_DATE': '2026-11-01T00:00:00Z', 'GIT_COMMITTER_DATE': '2026-11-01T00:00:00Z'}


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root/'checkout'), *args],
                                   env=environment(root)).decode()


def checkout(root):
    (root/'checkout').mkdir()
    git(root, 'init', '-q', '-b', 'main')
    git(root, 'config', 'user.name', 'Fixture author')
    git(root, 'config', 'user.email', 'fixture@example.test')
    files = root/'checkout'
    (files/'greeting.py').write_text('def greeting(name):\n    return "Hello, " + name\n')
    (files/'AGENTS.md').write_text(AGENTS)
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'Add plain greeting')
    base = git(root, 'rev-parse', 'HEAD').strip()
    git(root, 'switch', '-qc', 'pr-branch')
    (files/'greeting.py').write_text('def greeting(name, history=[]):\n    history.append(name)\n'
                                     '    return "<p>Hello, " + name + "</p>", list(history)\n')
    (files/'README.md').write_text('# HTML greeting\n\nFor a string name, `greeting(name)` returns an HTML '
        "paragraph and a list containing only that call's name. Render user-supplied names as text, "
        'including HTML metacharacters. Calls are independent.\n')
    (files/'test_greeting.py').write_text('import unittest\nfrom greeting import greeting\n\n'
        'class GreetingTest(unittest.TestCase):\n    def test_greeting(self):\n'
        '        self.assertEqual(greeting("Ada"), ("<p>Hello, Ada</p>", ["Ada"]))\n')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'Add HTML greeting and call history')
    return base, git(root, 'rev-parse', 'HEAD').strip()


def prepare(root, base, head):
    inputs = root/'inputs'
    (inputs/'global-rules.txt').write_text(GLOBAL_RULES)
    save(inputs/'context.json', CONTEXT)
    skill = root/'reviewer.md'
    definition = {
        'skill_identity': 'trial:content-review', 'resources': {'skill': str(skill)},
        'responsibility': 'content-review',
        'assigned_instructions': ('Review the complete pinned diff for correctness against the PR purpose, '
                                  'decisions and repository rules. Inspect every changed file; cite file, '
                                  'line and the violated rule or behavior for each finding.'),
        'instructions': {'global': str(inputs/'global-rules.txt'), 'repository': str(root/'checkout/AGENTS.md')},
        'inputs': {'context': str(inputs/'context.json')},
        'git': {'checkout': str(root/'checkout'), 'target': base, 'base': base, 'head': head},
        'options': {}, 'native_review': {'schema': 1, 'skill_version': 'trial-1', 'passes': []},
        'cacheable': True, 'governing_inputs_complete': True}
    save(root/'control/request.json', {'run': 'trial', 'cycle': 'c1', 'tasks': [{'definition': definition}]})
    output = subprocess.run([sys.executable, str(ROOT/'scripts/dk.py'), '--repository', REPO, '--pr', PR,
                             '--checkout', str(root/'checkout'), 'prepare', '--file',
                             str(root/'control/request.json')],
                            env=environment(root), capture_output=True, text=True, check=True)
    task = json.loads(output.stdout)['tasks'][0]
    if task['action'] != 'invoke':
        raise ValueError('Trial task was not claimed for invocation')
    save(root/'control/prepared.json', task)
    return task


def items(root):
    """Address → (owner item, backing file) for every captured input; the trial resolver's index."""
    context = json.loads((root/'inputs/context.json').read_text())
    found = {'instruction/global/rules': ({'text': GLOBAL_RULES}, root/'inputs/global-rules.txt'),
             'instruction/repo/files/AGENTS.md': ({'text': AGENTS}, root/'checkout/AGENTS.md'),
             'instruction/pr/purpose': ({'text': context['purpose']}, root/'inputs/context.json')}
    for decision in context['decisions']:
        found['decision/pr/'+decision['id']] = ({'statement': decision['statement']}, root/'inputs/context.json')
    return found


def document(root, assignment):
    governing = {address: item for address, (item, _) in items(root).items()}
    return {'governing': governing,
            'task': {'id': TASK_ID, 'instructions': assignment['task']['instructions']},
            'evidence': {'evidence/pr/diff': {},
                         **{'instruction/repo/files/'+quote(path, safe='/'): {} for path in ('README.md',)}}}


def init(root, arm):
    root = Path(root).resolve()
    if arm not in ARMS:
        raise ValueError('Unknown arm')
    if root.exists():
        raise ValueError('Never replace an existing trial arm')
    for folder in ('inputs', 'artifacts', 'control', 'state', 'config', 'data', 'bin'):
        (root/folder).mkdir(parents=True)
    (root/'reviewer.md').write_text(REVIEWER[arm].replace('ARM', str(root))+REPORT)
    base, head = checkout(root)
    task = prepare(root, base, head)
    handoff = {'checklist': task['checklist']}
    if arm == 'baseline':
        handoff['assignment'] = task['assignment']
    else:
        handoff['context'] = document(root, task['assignment'])
        script = root/'bin/get-item'
        script.write_text('#!'+sys.executable+'\nimport sys\nsys.path.insert(0, '+repr(str(Path(__file__).parent))+
                          ')\nimport agent_context_trial\nsys.exit(agent_context_trial.main('
                          "['get-item', '--root', "+repr(str(root))+'] + sys.argv[1:]))\n')
        script.chmod(0o755)
    save(root/'handoff.json', handoff)
    (root/'assignment.txt').write_text(ASSIGNMENT.replace('ARM', str(root)))
    return freeze(root, arm)


def freeze(root, arm):
    pinned = {name: sha((root/name).read_bytes()) for name in ('handoff.json', 'reviewer.md', 'assignment.txt')}
    head = git(root, 'rev-parse', 'HEAD').strip()
    value = {'arm': arm, 'head': head, 'files': pinned,
             'runtime': sha(git_tree(ROOT, 'runtime').encode()),
             'guidance': pinned['reviewer.md'], 'resources': sha(git_tree(ROOT, 'scripts').encode()),
             'input_hash': sha(json.dumps(pinned, sort_keys=True).encode()+head.encode())}
    context = json.loads((root/'handoff.json').read_text()).get('context')
    if context:
        # Diagnostic: the cacheable prefix is governing; tokens come from native telemetry.
        value['governing_bytes'] = len(json.dumps(context['governing']).encode())
    save(root/'freeze.json', value)
    return value


def git_tree(source, path):
    try:
        return subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD:'+path], text=True).strip()
    except subprocess.CalledProcessError:
        return 'untracked'


def pointer(value, path):
    for token in [t.replace('~1', '/').replace('~0', '~') for t in path.split('/')[1:]] if path else []:
        if isinstance(value, list) and token.isdigit() and int(token) < len(value):
            value = value[int(token)]
        elif isinstance(value, dict) and token in value:
            value = value[token]
        else:
            return None
    return value


def get_item(root, task, address):
    root = Path(root).resolve()
    if task != TASK_ID:
        return {'outcome': 'not_found'}
    address, _, path = address.partition('#')
    if address == 'evidence/pr/diff':
        pins = json.loads((root/'control/prepared.json').read_text())['assignment']['git']
        value = {'text': git(root, 'diff', pins['base'], pins['head'])}
    elif address.startswith('instruction/repo/files/'):
        name = address[len('instruction/repo/files/'):]
        try:
            value = {'text': git(root, 'show', 'HEAD:'+name)}
        except subprocess.CalledProcessError:
            return {'outcome': 'not_found'}
    elif address in items(root):
        value = items(root)[address][0]
    else:
        return {'outcome': 'not_found'}
    selected = pointer(value, path)
    return {'outcome': 'not_found'} if selected is None else {'item': selected}


ESCAPE = ('escap', 'xss', 'inject', 'metachar', 'html.escape', 'sanitiz')
HISTORY = ('mutable default', 'default argument', 'shared', 'persist', 'accumulat', 'independent', 'history=[]')


def check(root):
    """Pass only when both seeded defects are reported against greeting.py and the report is complete."""
    report = json.loads((Path(root)/'artifacts/report.json').read_text())
    findings = [f for f in report.get('findings', []) if str(f.get('path', '')).endswith('greeting.py')]
    text = [json.dumps(f).lower() for f in findings]
    found = {'escape': any(any(k in t for k in ESCAPE) for t in text),
             'history': any(any(k in t for k in HISTORY) for t in text)}
    return {'complete': report.get('complete') is True, 'defects': found,
            'finding_count': len(report.get('findings', [])),
            'passed': report.get('complete') is True and all(found.values())}


def collect(root):
    """Read native usage for a completed arm through the existing fail-closed collector."""
    sys.path.insert(0, str(Path(__file__).parent))
    import dollar_trials
    root = Path(root).resolve()
    dollar_trials.ASSIGNMENT = (root/'assignment.txt').read_text().replace(str(root), 'ARM')
    exit_code = json.loads((root/'host-outcome.json').read_text())['exit_code']
    return dollar_trials.collect(root, exit_code)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'check', 'collect', 'get-item'):
        command = commands.add_parser(name)
        command.add_argument('--root', type=Path, required=True)
        if name == 'init':
            command.add_argument('--arm', choices=ARMS, required=True)
        if name == 'get-item':
            command.add_argument('--task', required=True)
            command.add_argument('address')
    args = parser.parse_args(argv)
    if args.command == 'init':
        result = init(args.root, args.arm)
    elif args.command == 'check':
        result = check(args.root)
    elif args.command == 'collect':
        result = collect(args.root)
    else:
        result = get_item(args.root, args.task, args.address)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get('outcome') is None and result.get('passed', True) else 1


if __name__ == '__main__':
    sys.exit(main())
