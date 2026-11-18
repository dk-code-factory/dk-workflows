"""Deferred native reads retain the complete existing managed capture boundary.

Offline stand-ins establish protocol behavior, not native instruction adherence.
The optional CLI compares bounded instruction-byte diagnostics without model calls.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from checklist_reports import accept, finish
from state import capture, read, write
from tasks import Tasks
from test_batch_state import cli, definition
from test_state import case, git, REPO, PR, ROOT


@pytest.fixture(params=['fix', 'review'])
def guidance(case, tmp_path, request):
    spec, store = case
    route = request.param
    paths = {'entry': f'codex/skills/dk-pr-{route}/SKILL.md',
             'recipe': 'runtime/pr-iter.md' if route == 'fix' else 'runtime/pr-review.md',
             'lifecycle': 'docs/task-state.md', 'native': 'docs/native-review.md'}
    resources = {}
    for label, relative in paths.items():
        destination = tmp_path/'installed'/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT/relative).read_bytes())
        resources[label] = str(destination)
    spec = deepcopy(spec)
    spec['skill']['resources'] = resources
    spec['task']['instructions'] = 'Execute the selected ' + route + ' stage only; preserve original evidence.'
    return spec, store, resources


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def prepare(tasks, spec, **options):
    return tasks.prepare({'tasks': [{'definition': definition(spec)}], **options})['tasks'][0]


def complete(tasks, task):
    return finish(tasks, task['key'], task['claim']['id'], b'Original stage evidence',
                  {'complete': True, 'findings': [{'id': 'retained', 'body': 'Original finding'}]})


def test_deferred_resources_are_captured_before_read_and_reused_in_fresh_process(guidance, tmp_path):
    spec, store, resources = guidance
    # Native reading may start with entry/routing. Capture still includes the
    # indirect native contract and lifecycle before either is semantically read.
    Path(resources['entry']).read_text()
    before_read = capture(spec)
    for label in ('native', 'lifecycle', 'recipe'):
        expected = hashlib.sha256(Path(resources[label]).read_bytes()).hexdigest()
        assert before_read['components']['skill']['resources'][label]['sha256'] == expected
    for path in reversed(list(resources.values())):
        Path(path).read_text()
    assert capture(spec) == before_read
    tasks = Tasks(REPO, PR)
    task = prepare(tasks, spec, run='retained', cycle='c1')
    assert complete(tasks, task)['status'] == 'complete'
    before = snapshot(store.root)
    resumed = json.loads(subprocess.check_output(cli('resume', '--run', 'retained'), text=True))
    assert resumed['tasks'][0]['coverage_complete']
    assert resumed['tasks'][0]['finding_count'] == 1
    source = tmp_path/'request.json'
    write(source, {'run': 'retained', 'cycle': 'c1', 'tasks': [{'definition': definition(spec)}]})
    assert json.loads(subprocess.check_output(cli('prepare', '--compact', '--file', str(source)),
                                             text=True))['tasks'][0]['action'] == 'reuse'
    assert snapshot(store.root) == before


def test_pending_checks_and_stopped_claim_keep_completed_sibling(guidance):
    spec, store, _ = guidance
    tasks = Tasks(REPO, PR)
    first = prepare(tasks, spec, run='retained')
    assert complete(tasks, first)['status'] == 'complete'
    sibling = deepcopy(spec)
    sibling['skill']['identity'] = 'reviewer:pending'
    pending = prepare(tasks, sibling, run='retained')
    check = tasks.finish(pending['key'], pending['claim']['id'], b'Original pending result',
                         {'complete': True})
    assert check['action'] == 'recheck'
    before = snapshot(store.root)
    resumed = Tasks(REPO, PR).resume()
    rows = {row['key']: row for row in resumed['tasks']}
    assert rows[first['key']]['coverage_complete']
    assert rows[pending['key']]['checklist']['pending_round'] == check['round']
    assert not rows[pending['key']]['coverage_complete']
    assert snapshot(store.root) == before
    assert accept(tasks, check)['checklist_complete']
    # Explicit recovery closes only a confirmed stopped task; it does not rerun
    # either completed sibling or erase their original results/check rounds.
    stopped = deepcopy(spec)
    stopped['skill']['identity'] = 'reviewer:stopped'
    claim = prepare(tasks, stopped, run='retained')
    recovered = prepare(tasks, stopped, run='retained', recover={claim['key']: claim['claim']['id']})
    assert recovered['action'] == 'invoke'
    assert recovered['claim']['id'] != claim['claim']['id']
    assert prepare(tasks, spec)['action'] == 'reuse'
    assert prepare(tasks, sibling)['action'] == 'reuse'
    retained = read(store.manifest)['entries']
    assert retained[first['key']]['result']
    assert len(retained[pending['key']]['checklist']['rounds']) == 1
    assert retained[claim['key']]['attempts'][0]['status'] == 'interrupted'


def test_unread_indirect_drift_refreshes_only_affected_task(guidance, tmp_path):
    spec, store, resources = guidance
    sibling = deepcopy(spec)
    sibling['skill']['identity'] = 'reviewer:unaffected'
    original_native = tmp_path/'original-native.md'
    original_native.write_bytes(Path(resources['native']).read_bytes())
    sibling['skill']['resources']['native'] = str(original_native)
    tasks = Tasks(REPO, PR)
    first, other = prepare(tasks, spec), prepare(tasks, sibling)
    complete(tasks, first)
    complete(tasks, other)
    original = read(store.manifest)['entries']
    Path(resources['native']).write_text('Changed governing native evidence requirement')
    status = {row['key']: row for row in tasks.resume()['tasks']}
    assert not status[first['key']]['coverage_complete']
    assert status[other['key']]['coverage_complete']
    assert prepare(tasks, sibling)['action'] == 'reuse'
    retained = read(store.manifest)['entries']
    assert retained[first['key']]['result'] == original[first['key']]['result']
    assert retained[other['key']] == original[other['key']]


def test_missing_deferred_resource_blocks_claim_and_inflight_record(guidance):
    spec, store, resources = guidance
    tasks = Tasks(REPO, PR)
    path = Path(resources['native'])
    original = path.read_bytes()
    path.unlink()
    with pytest.raises((OSError, ValueError)):
        prepare(tasks, spec)
    assert not store.manifest.exists()
    path.write_bytes(original)
    prepared = prepare(tasks, spec)
    before = snapshot(store.root)
    path.unlink()
    with pytest.raises((OSError, ValueError)):
        complete(tasks, prepared)
    assert snapshot(store.root) == before


def test_missing_promisor_object_never_fetches_during_byte_comparison(case, tmp_path, monkeypatch):
    spec, _ = case
    checkout = Path(spec['git']['checkout'])
    paths = ['codex/skills/dk-pr-fix/SKILL.md', 'codex/skills/dk-pr-review/SKILL.md',
             'runtime/pr-iter.md', 'runtime/pr-review.md', 'docs/task-state.md']
    for relative in paths:
        destination = checkout/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT/relative).read_bytes())
    git(checkout, 'add', '.')
    git(checkout, 'commit', '-qm', 'frozen guidance')
    revision = git(checkout, 'rev-parse', 'HEAD')
    blob = git(checkout, 'rev-parse', revision + ':' + paths[0])
    (checkout/'.git/objects'/blob[:2]/blob[2:]).unlink()
    for key, value in [('core.repositoryformatversion', '1'),
                       ('extensions.partialClone', 'origin'),
                       ('remote.origin.url', 'probe::offline'),
                       ('remote.origin.promisor', 'true'), ('protocol.probe.allow', 'always')]:
        git(checkout, 'config', key, value)
    marker = tmp_path/'fetch-attempted'
    helper = tmp_path/'git-remote-probe'
    helper.write_text('#!' + sys.executable + '\nfrom pathlib import Path\nPath(' +
                      repr(str(marker)) + ').write_text("attempted")\nraise SystemExit(1)\n')
    helper.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('GIT_ALLOW_PROTOCOL', 'probe')
    # Prove this is an actual promised-object miss that normally invokes Git's
    # remote helper. The fixture helper only writes a local marker and exits.
    control = subprocess.run(['git', '-C', str(checkout), 'show', revision + ':' + paths[0]],
                             capture_output=True)
    assert control.returncode != 0 and marker.exists()
    marker.unlink()
    monkeypatch.setitem(instruction_bytes.__globals__, 'ROOT', checkout)
    with pytest.raises(subprocess.CalledProcessError):
        instruction_bytes(revision)
    assert not marker.exists()


def instruction_bytes(baseline, candidate=None):
    """Compare identical declared reads; never infer token prices or native use."""
    def source(path, revision):
        if revision:
            return subprocess.check_output(['git', '--no-lazy-fetch', '-C', str(ROOT),
                                            'show', '--end-of-options', revision+':'+path])
        return (ROOT/path).read_bytes()
    def section(text, heading):
        match = re.search(r'^## '+re.escape(heading)+r'\n(.*?)(?=^## |\Z)', text,
                          flags=re.MULTILINE | re.DOTALL)
        return match.group(0) if match else ''
    result = {'baseline': baseline, 'candidate': candidate or 'worktree',
              'scope': 'Entry, recipe and lifecycle only. Unchanged supporting contracts, tool schemas, model reasoning and native responses excluded.',
              'api_dollars': None, 'native_measurement': False, 'routes': {}}
    for route in ('fix', 'review'):
        paths = [f'codex/skills/dk-pr-{route}/SKILL.md',
                 'runtime/pr-iter.md' if route == 'fix' else 'runtime/pr-review.md', 'docs/task-state.md']
        arms = {}
        for name, revision in [('baseline', baseline), ('candidate', candidate)]:
            sources = {path: source(path, revision) for path in paths}
            text = {path: value.decode() for path, value in sources.items()}
            # Frozen full selected path needs every core section. A new session
            # resuming pending completion reads routing, the review/verification
            # stage and the relevant lifecycle sections, including new authority.
            recipe = text[paths[1]]
            intro = re.split(r'^1\. ', recipe, maxsplit=1, flags=re.MULTILINE)[0]
            stage = re.search(r'^4\. .*?(?=^5\. |\Z)', recipe, re.MULTILINE | re.DOTALL)
            lifecycle = text[paths[2]]
            resumed = text[paths[0]] + intro + stage.group(0) + lifecycle.split('## ', 1)[0]
            for heading in ('Lifecycle authority and guidance loading', 'Completion checks',
                            'Resume in a new session', 'Governing inputs and freshness', 'Retained budgets'):
                resumed += section(lifecycle, heading)
            arms[name] = {'initial_bytes': sum(map(len, sources.values())),
                          'fresh_session_pending_check_bytes': len(resumed.encode()),
                          'same_context_unchanged_reread_bytes': 0,
                          'governing_capture_bytes': sum(map(len, sources.values())),
                          'source_sha256': {path: hashlib.sha256(value).hexdigest() for path, value in sources.items()}}
        result['routes'][route] = arms
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Offline core instruction-byte comparison; no native dollar claim.')
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--candidate')
    args = parser.parse_args()
    print(json.dumps(instruction_bytes(args.baseline, args.candidate), indent=2))
