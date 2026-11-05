#!/usr/bin/env python3
"""Prepare isolated exhausted legacy state for a native continuation exercise.

Fixture records are synthetic bookkeeping setup, not native review coverage.
No remote or production workflow state is read or written.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'runtime'))
from state import Store, make_spec


@contextmanager
def fixture_environment(directory):
    # Store and its nested capture calls read the process environment, so keep
    # the entire setup in this scope, including their internal Git subprocesses.
    isolated = {'XDG_STATE_HOME': str(directory/'state'),
                'XDG_CONFIG_HOME': str(directory/'config'),
                'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_SYSTEM': os.devnull,
                'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_ATTR_NOSYSTEM': '1'}
    names = {name for name in os.environ if name.startswith('GIT_')} | isolated.keys()
    original = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ.pop(name, None)
        os.environ.update(isolated)
        yield
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def prepare(directory):
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    with fixture_environment(directory):
        return prepare_isolated(directory)


def prepare_isolated(directory):
    repo = directory/'repo'
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()

    git('init', '-q', '--template=')
    git('config', 'core.hooksPath', os.devnull)
    git('config', 'commit.gpgsign', 'false')
    git('config', 'tag.gpgsign', 'false')
    git('config', 'user.name', 'Exercise')
    git('config', 'user.email', 'exercise@example.test')
    (repo/'code').write_text('base\n')
    git('add', '.')
    git('commit', '-qm', 'base')
    base = git('rev-parse', 'HEAD')
    (repo/'code').write_text('head\n')
    git('commit', '-qam', 'head')
    head = git('rev-parse', 'HEAD')
    resource = directory/'fixture-resource.txt'
    resource.write_text('Synthetic state setup, not a reviewer result.\n')
    spec = make_spec(repository='github.com/exercise/continuation',
                     pr='https://github.com/exercise/continuation/pull/1',
                     skill_identity='exercise:bookkeeping', resources={'entry': str(resource)},
                     responsibility='synthetic-analysis', assigned_instructions='Exercise fixture only.',
                     instructions={}, inputs={}, options={}, governing_inputs_complete=True,
                     git={'checkout': str(repo), 'base': base, 'target': base, 'head': head})
    store = Store(spec['repository'], spec['pr'], checkout=repo)
    for index in range(5):
        spec['options'] = {'stage': index}
        claim = store.start(spec, run='retained-run', cycle=f'c{index}')['claim']
        store.record(spec, claim['id'], b'synthetic setup', {'complete': True, 'findings': []}, 'complete')
    # Advance only target after the final clean synthetic record.
    git('checkout', '-q', '--detach', base)
    (repo/'target-change').write_text('advanced target\n')
    git('add', '.')
    git('commit', '-qm', 'advance target')
    spec['git']['target'] = git('rev-parse', 'HEAD')
    git('checkout', '-q', '--detach', head)
    (directory/'spec.json').write_text(json.dumps(spec, indent=2)+'\n')
    (directory/'before.json').write_bytes(store.manifest.read_bytes())
    return {'directory': str(directory), 'state_home': os.environ['XDG_STATE_HOME'],
            'manifest': str(store.manifest), 'spec': str(directory/'spec.json'),
            'run': 'retained-run', 'next_cycle': 'c5', 'synthetic_setup': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='New private directory outside all repositories')
    print(json.dumps(prepare(parser.parse_args().directory), sort_keys=True))
