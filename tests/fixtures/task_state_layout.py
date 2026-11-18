"""Stable offline sources for the pre-extraction contract-v2 compatibility fixture."""
from copy import deepcopy
import os
from pathlib import Path
import subprocess

REPOSITORY = 'github.com/acme/project'
PR = 'https://github.com/acme/project/pull/7'


def sources(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for name, text in [('skill.md', 'Fixture worker instructions\n'),
                       ('user.txt', 'Original fixture assignment\n'),
                       ('context.txt', 'Original governing evidence\n')]:
        (root/name).write_text(text)
        (root/name).chmod(0o600)


def definition(task='complete'):
    return {'repository': REPOSITORY, 'pr': PR, 'skill_identity': 'fixture:worker',
            'resources': {'skill': 'skill.md'}, 'responsibility': task,
            'assigned_instructions': 'Inspect the supplied fixture and return original evidence.',
            'instructions': {'user': 'user.txt'}, 'inputs': {'context': 'context.txt'},
            'options': {}, 'governing_inputs_complete': True,
            'native_review': {'schema': 2, 'required': False}}


def specifications(api, root):
    """Identical resources, identity aliases and Git objects on every run."""
    values = {}
    for name in ('complete', 'failed', 'pending', 'incomplete', 'missing', 'damaged', 'legacy', 'managed'):
        values[name] = api.make_spec(**definition(name))
    ordinary = values['complete']
    values['noncacheable'] = {**deepcopy(ordinary), 'cacheable': False}
    values['no-declaration'] = {k: v for k, v in ordinary.items() if k != 'native_review'}
    values['git-label'] = deepcopy(ordinary)
    values['git-label']['inputs'] = {'git': 'context.txt'}
    values['hook'] = deepcopy(ordinary)
    values['hook']['options'] = {'hook_phase': {'occurrence': {'run': 'fixture'}, 'hooks': []}}
    for name, identity in [('old-assessment', 'dk-workflows:dk-pr-assessment'),
                            ('new-assessment', 'dk-workflows:dk-issue-alternatives'),
                            ('renamed-review', 'dk-workflows:dk-pr-review-architect')]:
        values[name] = deepcopy(ordinary)
        values[name]['skill']['identity'] = identity
        values[name]['task']['id'] = 'pr-purpose-assessment' if 'assessment' in name else 'alternatives-alignment-review'

    checkout = Path(root)/'checkout'
    checkout.mkdir()
    environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_AUTHOR_DATE='2020-01-01T00:00:00+00:00',
                       GIT_COMMITTER_DATE='2020-01-01T00:00:00+00:00')

    def git(*args):
        return subprocess.check_output(['git', '-C', str(checkout), *args], env=environment, text=True).strip()

    git('init', '-q', '--initial-branch=main')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.test')
    (checkout/'code.txt').write_text('original\n')
    git('add', 'code.txt')
    git('commit', '-qm', 'baseline')
    base = git('rev-parse', 'HEAD')
    (checkout/'code.txt').write_text('changed\n')
    git('commit', '-qam', 'candidate')
    head = git('rev-parse', 'HEAD')
    for history, head_sensitive in ((False, False), (True, False), (False, True), (True, True)):
        spec = deepcopy(ordinary)
        spec['git'] = {'checkout': str(checkout), 'base': base, 'target': base, 'head': head,
                       'history': history, 'head_sensitive': head_sensitive}
        values[f'git-{history}-{head_sensitive}'] = spec
    return values
