"""Opt-in handoffs preserve evidence, legacy providers, retries and failure gates."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'dk-pr-decisions/tests'))
from preparation_fixtures import Package, analysis, prep

spec = importlib.util.spec_from_file_location('compact_runner', ROOT / 'dk-pr-review/scripts/run_reviewers.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    package = Package(tmp_path)
    script = tmp_path / 'provider.py'
    script.write_text('''import hashlib, json, os, pathlib, sys
h = json.loads(pathlib.Path(sys.argv[1]).read_text())
log = pathlib.Path(sys.argv[2])
with log.open('a') as stream:
    stream.write(json.dumps(h) + '\\n')
if h['contract'] == 'git-diff-v2':
    for key in ('full_preparation', 'full_decisions'):
        ref = h[key]
        assert hashlib.sha256(pathlib.Path(ref['path']).read_bytes()).hexdigest() == ref['sha256']
    if sys.argv[3] in ('corrupt', 'delete'):
        path = pathlib.Path(h['full_preparation']['path'])
        if sys.argv[3] == 'delete':
            path.unlink()
        else:
            path.chmod(0o600)
            path.write_text('corrupt')
if sys.argv[3] == 'correct' and os.environ['DK_PROVIDER_ATTEMPT'] == '0':
    print('invalid json')
else:
    print(json.dumps({'findings': []}))
''')
    def provider(label, *, compact=True, mode='normal'):
        log = tmp_path / (label + '.jsonl')
        binding = {'command': [sys.executable, str(script), '{handoff}', str(log), mode]}
        if compact:
            binding['handoff_contract'] = 'git-diff-v2'
        return binding, log
    def prepare(providers):
        names = [runner.bindings.key(value) for value in providers]
        package.manifest['evidence']['reviewers'] = [str(package.write('reviewer-' + str(i) + '.json',
            runner.describe_reviewer(value))) for i, value in enumerate(providers)]
        package.write('manifest.json', package.manifest)
        saved = package.prepare(analysis(names))
        return prep.read(saved['artifact'])
    return package, provider, prepare


def test_mixed_contracts_preserve_legacy_and_deliver_only_assigned_addendum(delivery):
    package, provider, prepare = delivery
    compact, compact_log = provider('compact')
    legacy, legacy_log = provider('legacy', compact=False)
    prepared = prepare([compact, legacy])
    before = copy.deepcopy(prepared)
    result = runner.review(package.context, [compact, legacy], 'Exact scope', package.decisions, 'Rules', prepared)
    assert result['complete'], result
    small = json.loads(compact_log.read_text())
    full = json.loads(legacy_log.read_text())
    assert full['contract'] == 'git-diff-v1' and full['preparation'] == prepared
    assert full['decisions'] == package.decisions and 'full_preparation' not in full
    assert small['contract'] == 'git-diff-v2'
    name = runner.bindings.key(compact)
    assert small['preparation']['analysis'] == {
        **prepared['analysis'], 'reviewer_addenda': {name: prepared['analysis']['reviewer_addenda'][name]}}
    assert small['reviewer_addendum'] == prepared['analysis']['reviewer_addenda'][name]
    assert 'workflow_dir' not in small['preparation'] and 'graph' not in small['preparation']
    assert small['decisions']['decisions'] == package.decisions['decisions']
    assert 'live_decisions' not in small['decisions']
    assert small['scope'] == 'Exact scope' and small['rules'] == 'Rules'
    for field, original in (('full_preparation', prepared), ('full_decisions', package.decisions)):
        reference = small[field]
        raw = Path(reference['path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == reference['sha256']
        assert json.loads(raw) == original
    assert len(json.dumps(small)) < len(json.dumps(full))
    assert prepared == before
    assert prep.git(package.repo, 'status', '--porcelain=v1') == ''


def test_compact_correction_reuses_the_same_handoff_and_evidence(delivery):
    package, provider, prepare = delivery
    compact, log = provider('corrected', mode='correct')
    prepared = prepare([compact])
    result = runner.review(package.context, [compact], '', package.decisions, '', prepared)
    assert result['complete'], result
    requests = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(requests) == 2 and requests[0] == requests[1]


@pytest.mark.parametrize('mode', ['corrupt', 'delete'])
def test_changed_full_evidence_blocks_success(delivery, mode):
    package, provider, prepare = delivery
    compact, log = provider(mode, mode=mode)
    prepared = prepare([compact])
    result = runner.review(package.context, [compact], '', package.decisions, '', prepared)
    assert not result['complete'] and result['guard_failure']
    if mode == 'corrupt':
        assert 'compact handoff evidence changed' in result['reason']
    else:
        assert 'No such file' in result['reason']
    assert log.exists()


def test_contract_change_blocks_before_reusing_a_checkpoint_or_launching(delivery):
    package, provider, prepare = delivery
    compact, log = provider('changed')
    prepared = prepare([compact])
    legacy = {key: value for key, value in compact.items() if key != 'handoff_contract'}
    session = Mock()
    session.cached.return_value = {'findings': []}
    result = runner.review(package.context, [legacy], '', package.decisions, '', prepared, session=session)
    assert not result['complete'] and result['guard_failure']
    assert 'handoff contract changed' in result['reason']
    session.cached.assert_not_called()
    assert not log.exists()


def test_active_decisions_keep_exact_conditions_and_sources(delivery):
    package, provider, prepare = delivery
    compact, log = provider('decisions')
    active = runner.decision_api.record('Keep compatibility until the dependency is upgraded.',
                                       'user', [], source='Exact user answer: until the dependency is upgraded.')
    old = runner.decision_api.record('Earlier scope.', 'user', [], source='Earlier answer.')
    old.update(status='superseded', superseded_by=active['id'])
    package.decisions['local_decisions'] = [{'text': active['text'], 'source': active['source']}]
    package.decisions['decisions'] = [old, active]
    package.write('decisions.json', package.decisions)
    prepared = prepare([compact])
    decisions = package.decisions
    handoff = runner.compact_handoff({'preparation': prepared, 'decisions': decisions}, runner.bindings.key(compact))
    assert handoff['decisions']['decisions'] == [active]
    assert decisions['decisions'] == [old, active]
    result = runner.review(package.context, [compact], '', decisions, '', prepared)
    assert result['complete'], result
    delivered = json.loads(log.read_text())
    assert delivered['decisions']['decisions'] == [active]
    assert json.loads(Path(delivered['full_decisions']['path']).read_text())['decisions'] == [old, active]
    with pytest.raises(ValueError, match='fresh preparation'):
        runner.compact_handoff({'preparation': {**prepared, 'preparation_version': 1},
                                'decisions': decisions}, runner.bindings.key(compact))


def test_native_prompt_names_the_selected_contract_and_evidence_rules():
    legacy = runner.build_skill_prompt(Path('example/SKILL.md'), 'handoff.json')
    compact = runner.build_skill_prompt(Path('example/SKILL.md'), 'handoff.json', 'git-diff-v2')
    assert 'git-diff-v1 handoff' in legacy and 'full_preparation' not in legacy
    assert 'git-diff-v2 handoff' in compact and 'verify the referenced SHA-256' in compact
    assert 'Read preparation.analysis and reviewer_addendum' in compact
    assert 'nested handoffs and correction retries' in compact
