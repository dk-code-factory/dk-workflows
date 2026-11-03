"""Synthetic offline compatibility controls never establish native adoption."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from shared import architecture, bindings, compact_compatibility as compatibility, review_execution as execution
from shared.preparation_views import reviewer_view
from test_review_execution import claude_rows, codex_rows, stream


def retain(path, value):
    path.write_text(json.dumps(value))
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.fixture
def captures(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    repository = tmp_path / 'repository'
    repository.mkdir()
    patch = tmp_path / 'patch.diff'
    patch.write_text('diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n')
    patch_ref = {'path': str(patch), 'sha256': hashlib.sha256(patch.read_bytes()).hexdigest()}
    decisions = {'decisions': [{'id': 'choice', 'status': 'active', 'text': 'Preserve exact scope.',
                               'conditions': 'Until upgraded.', 'rationale': 'Compatibility.', 'sources': ['user']}],
                 'local_decisions': [], 'discussion_decisions': [], 'live_decisions': []}
    manifest = {'version': 1, 'provenance': 'synthetic', 'pairs': []}
    for label, skill, host in compatibility.COHORT:
        directory = tmp_path / label
        directory.mkdir()
        source = directory / 'SKILL.md'
        source.write_text('Synthetic offline installed procedure: ' + label)
        source_ref = {'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
        # Synthetic procedure registry, never installed-provider compatibility.
        monkeypatch.setitem(execution._METHODS, (skill, host),
                            {'version': 'test', 'sources': {'SKILL.md': source_ref['sha256']}})
        pair = {'id': label, 'route': 'native' if host == 'codex' else 'run-claude-skill'}
        for mode in ('full', 'compact'):
            contract = 'git-diff-v1' if mode == 'full' else 'git-diff-v2'
            binding = {'skill': skill, 'host': host, 'handoff_contract': contract}
            reviewer = bindings.key(binding)
            descriptor = {'descriptor_version': 1, 'complete': True, 'missing': [], 'reviewer': reviewer,
                          'handoff_contract': contract, 'aspects': ['Spec'], 'effective_prompt': 'Read the pinned scope.',
                          'sources': [source_ref], 'execution': {'executor': host, 'root': str(directory),
                              'procedure': str(source), 'version': 'test', 'bundle_digest': 'synthetic',
                              'binding': binding}}
            prepared = {'preparation_version': 4, 'preparation_id': mode,
                        'identity': {'base_sha': 'base', 'head_sha': 'head', 'pr_url': 'https://example.test/pull/1'},
                        'sources': {'patch': patch_ref}, 'reviewers': {reviewer: descriptor},
                        'analysis': {'boundaries': ['Preserve all shared guidance.'], 'questions': [],
                            'reviewer_addenda': {reviewer: 'Assigned addendum.'},
                            'worker_instructions': 'Read original history only when needed.'}}
            handoff = {'contract': contract, **prepared['identity'], 'scope': 'Exact requested scope.',
                       'rules': 'Exact rules.', 'patch': str(patch), 'checkout': str(repository),
                       'preparation': prepared, 'decisions': decisions, 'reviewer_addendum': 'Assigned addendum.',
                       'decision_instructions': 'Active choices govern; history is available on demand.'}
            if mode == 'compact':
                handoff.update(full_preparation=retain(directory / 'preparation.json', prepared),
                               full_decisions=retain(directory / 'decisions.json', decisions),
                               preparation_view_version=1, preparation=reviewer_view(prepared, reviewer),
                               decisions=compatibility.active_decisions(decisions))
            roles = execution.requirements(binding, handoff)
            # Execute conditional roles as well to avoid synthetic applicability claims.
            roles += [r for r in execution.conditional(binding)[1] if r not in roles]
            rows = codex_rows(roles) if host == 'codex' else claude_rows(roles)
            for row in rows:
                item = row.get('item', {})
                if item.get('tool') == 'spawn_agent':
                    role = item['prompt'].splitlines()[0].split('=', 1)[1]
                    item['prompt'] += '\nDK_REVIEW_DELIVERY_JSON=' + json.dumps(compatibility.delivery_payload(handoff, role))
                for block in row.get('message', {}).get('content', []):
                    if block.get('type') == 'tool_use':
                        role = block['input']['prompt'].splitlines()[0].split('=', 1)[1]
                        block['input']['prompt'] += '\nDK_REVIEW_DELIVERY_JSON=' + json.dumps(compatibility.delivery_payload(handoff, role))
            path = directory / (mode + '-handoff.json')
            handoff_ref = retain(path, handoff)
            result = {'findings': []}
            with execution.collect(binding, descriptor, path, repository) as collector:
                execution.record(host, stream(rows), '', 0, 0)
                proof_ref = execution.finish(collector, result)
            proof = compatibility.read(proof_ref)
            deliveries = []
            for index, row in enumerate(rows, 1):
                prompt_pointer = None
                if row.get('item', {}).get('tool') == 'spawn_agent':
                    prompt_pointer = '/item/prompt'
                    prompt = row['item']['prompt']
                elif row.get('type') == 'assistant':
                    prompt_pointer = '/message/content/0/input/prompt'
                    prompt = row['message']['content'][0]['input']['prompt']
                if prompt_pointer:
                    deliveries.append({'recipient': prompt.splitlines()[0].split('=', 1)[1], 'attempt': 0,
                        'kind': 'native-prompt', 'source': proof['attempts'][0], 'event': index, 'pointer': prompt_pointer})
            prompt_ref = retain(directory / (mode + '-parent-prompt.json'), {'prompt': 'Retained parent delivery record.'})
            deliveries.append({'recipient': 'reviewer', 'attempt': 0, 'kind': 'source-bound-observation',
                'source': prompt_ref, 'pointer': '/prompt', 'excerpt': 'Retained parent delivery record.',
                'received': compatibility.delivery_payload(handoff, 'reviewer')})
            pair[mode] = {'binding': binding, 'handoff': handoff_ref,
                         'descriptor': retain(directory / (mode + '-descriptor.json'), descriptor),
                         'result': retain(directory / (mode + '-result.json'), result),
                         'execution': proof_ref, 'deliveries': deliveries}
        manifest['pairs'].append(pair)
    path = tmp_path / 'manifest.json'
    retain(path, manifest)
    return path, manifest


def compare(captures):
    path, manifest = captures
    retain(path, manifest)
    return compatibility.compare(path)


def refresh(run, *, slots=(0,), findings=None):
    """Regenerate actual captures/proof after a negative-control edit."""
    handoff = compatibility.read(run['handoff'])
    descriptor = compatibility.read(run['descriptor'])
    previous = compatibility.read(run['execution'])
    native = compatibility.read(previous['attempts'][0])
    rows = [json.loads(line) for line in native['stdout'].splitlines()]
    for row in rows:
        item = row.get('item', {})
        if item.get('tool') == 'spawn_agent':
            role = item['prompt'].splitlines()[0].split('=', 1)[1]
            item['prompt'] = 'DK_REVIEW_PASS=' + role + '\nDK_REVIEW_DELIVERY_JSON=' + json.dumps(compatibility.delivery_payload(handoff, role))
        for block in row.get('message', {}).get('content', []):
            if block.get('type') == 'tool_use':
                role = block['input']['prompt'].splitlines()[0].split('=', 1)[1]
                block['input']['prompt'] = 'DK_REVIEW_PASS=' + role + '\nDK_REVIEW_DELIVERY_JSON=' + json.dumps(compatibility.delivery_payload(handoff, role))
    result = {'findings': [] if findings is None else findings}
    run['result'] = retain(Path(run['result']['path']), result)
    with execution.collect(run['binding'], descriptor, run['handoff']['path'], handoff['checkout']) as collector:
        for slot in slots:
            execution.record(native['host'], stream(rows), '', 0, slot)
        run['execution'] = execution.finish(collector, result)
    proof = compatibility.read(run['execution'])
    for record in run['deliveries']:
        if 'event' in record:
            record['source'] = proof['attempts'][0]
        else:
            record['received'] = compatibility.delivery_payload(handoff, record['recipient'])


def test_synthetic_pair_matrix_retains_no_adoption_authority(captures):
    report = compare(captures)
    assert report['status'] == 'observed', report
    assert report['provenance'] == 'synthetic'
    assert report['adoption_authorized'] is report['publication_authorized'] is False
    assert report['live_acceptance'] == 'pending'
    assert [p['id'] for p in report['pairs']] == ['codex-ad', 'claude-toolkit', 'claude-ad']
    assert report['pairs'][0]['runs']['compact']['completed'] == ['Performance', 'Spec', 'Standards', 'Toolkit']
    assert report['pairs'][0]['findings'] == {'shared': [], 'full_only': [], 'compact_only': []}


@pytest.mark.parametrize('action', ['changed', 'missing', 'inaccessible'])
def test_original_evidence_failures_are_explicitly_incomplete(captures, monkeypatch, action):
    run = captures[1]['pairs'][0]['compact']
    handoff = compatibility.read(run['handoff'])
    path = Path(handoff['full_preparation']['path'])
    if action == 'changed':
        path.write_text('changed')
    elif action == 'missing':
        path.unlink()
    else:
        original = Path.read_bytes
        def denied(self):
            if self == path:
                raise PermissionError('original inaccessible')
            return original(self)
        monkeypatch.setattr(Path, 'read_bytes', denied)
    report = compare(captures)
    assert report['status'] == 'incomplete'
    assert report['pairs'][0]['issues']


def test_native_completion_does_not_establish_guidance_delivery(captures):
    captures[1]['pairs'][0]['compact']['deliveries'] = []
    row = compare(captures)['pairs'][0]
    assert row['status'] == 'incomplete'
    assert row['runs']['compact']['completed']
    assert any('delivery unknown' in issue for issue in row['issues'])


def test_changed_nested_active_choice_is_rejected(captures):
    run = captures[1]['pairs'][0]['compact']
    observation = run['deliveries'][-1]
    observation['received']['decisions']['decisions'][0]['conditions'] = 'Changed condition.'
    row = compare(captures)['pairs'][0]
    assert row['status'] == 'incomplete'
    assert 'guidance delivery' in row['issues'][0]


def test_observation_requires_accessible_source_excerpt(captures):
    captures[1]['pairs'][1]['compact']['deliveries'][-1]['excerpt'] = 'Not present.'
    assert 'excerpt unavailable' in compare(captures)['pairs'][1]['issues'][0]


def test_native_role_evidence_cannot_be_replaced_by_findings(captures):
    captures[1]['pairs'][2]['compact']['execution'] = captures[1]['pairs'][2]['compact']['result']
    assert compare(captures)['pairs'][2]['status'] == 'incomplete'


@pytest.mark.parametrize('change', ['contract', 'scope', 'method', 'guidance', 'decisions'])
def test_regenerated_capture_hashes_do_not_hide_changed_governing_inputs(captures, change):
    run = captures[1]['pairs'][0]['compact']
    value = compatibility.read(run['handoff'])
    if change == 'contract':
        value['contract'] = 'git-diff-v3'
    elif change == 'scope':
        value['scope'] = 'Different requested scope.'
    elif change == 'method':
        descriptor = compatibility.read(run['descriptor'])
        descriptor['execution']['version'] = 'changed'
        run['descriptor'] = retain(Path(run['descriptor']['path']), descriptor)
    elif change == 'guidance':
        value['preparation']['analysis']['worker_instructions'] = 'Omitted shared constraint.'
    else:
        value['decisions']['decisions'][0]['text'] = 'Changed active choice.'
    run['handoff'] = retain(Path(run['handoff']['path']), value)
    row = compare(captures)['pairs'][0]
    assert row['status'] == 'incomplete'
    assert row['issues']


def test_cohort_omission_and_route_drift_are_invalid(captures):
    captures[1]['pairs'].pop()
    with pytest.raises(ValueError, match='cohort required'):
        compare(captures)


def test_correction_needs_independent_delivery_observations(captures):
    run = captures[1]['pairs'][0]['compact']
    refresh(run, slots=(0, 1))
    row = compare(captures)['pairs'][0]
    assert row['runs']['compact']['response_slots'] == [0, 1]
    assert row['status'] == 'incomplete'
    assert any('response 1' in message for message in row['issues'])


def test_corrected_delivery_can_be_observed_without_replacing_initial_evidence(captures):
    run = captures[1]['pairs'][0]['compact']
    refresh(run, slots=(0, 1))
    proof = compatibility.read(run['execution'])
    corrected = json.loads(json.dumps(run['deliveries']))
    for record in corrected:
        record['attempt'] = 1
        if 'event' in record:
            record['source'] = proof['attempts'][1]
    run['deliveries'].extend(corrected)
    row = compare(captures)['pairs'][0]
    assert row['status'] == 'observed', row['issues']
    assert {record['attempt'] for record in row['runs']['compact']['delivery']} == {0, 1}


def test_paired_scope_drift_survives_regenerated_native_proof(captures):
    run = captures[1]['pairs'][0]['compact']
    handoff = compatibility.read(run['handoff'])
    handoff['scope'] = 'Narrowed silently.'
    run['handoff'] = retain(Path(run['handoff']['path']), handoff)
    refresh(run)
    assert compare(captures)['pairs'][0]['issues'] == ['paired governing evidence or requested scope changed']


def test_separate_claude_methods_keep_identical_requested_scope(captures):
    for label in ('full', 'compact'):
        run = captures[1]['pairs'][2][label]
        handoff = compatibility.read(run['handoff'])
        handoff['scope'] = 'Different Claude AD scope.'
        run['handoff'] = retain(Path(run['handoff']['path']), handoff)
        refresh(run)
    assert compare(captures)['pairs'][2]['issues'] == ['cohort governing evidence or requested scope changed']


def test_retained_findings_are_compared_without_recall_claims(captures):
    finding = {'severity': 'Major', 'path': 'a.py', 'line': 1, 'message': 'A retained sample finding.', 'fix': 'Preserve it.'}
    refresh(captures[1]['pairs'][0]['compact'], findings=[finding])
    row = compare(captures)['pairs'][0]
    assert row['status'] == 'observed'
    assert row['findings']['compact_only'] == [finding]
    assert 'recall' not in row


def test_architecture_recipient_and_revision_are_checked(tmp_path):
    packet = architecture.seal({'agent': 'Spec', 'head': 'head', 'baseline': 'base'})
    path = tmp_path / 'packet.json'
    path.write_text(json.dumps(packet))
    ref = {'path': str(path), 'fingerprint': packet['fingerprint']}
    handoff = {'head_sha': 'head', 'base_sha': 'base', 'nested_architecture_packets': {'Spec': ref}}
    assert compatibility.packets(handoff, 'parent')['Spec'] == packet
    handoff['nested_architecture_packets'] = {'Standards': ref}
    with pytest.raises(ValueError, match='recipient mismatch'):
        compatibility.packets(handoff, 'parent')
    handoff['nested_architecture_packets'] = {'Spec': ref}
    handoff['head_sha'] = 'moved'
    with pytest.raises(ValueError, match='revisions mismatch'):
        compatibility.packets(handoff, 'parent')


def test_cli_writes_private_non_overwriting_diagnostic_report(captures, tmp_path):
    # CLI sees the actual supported-method registry, so synthetic profiles remain
    # incomplete in a fresh process. This also proves it never invokes a provider.
    target = tmp_path / 'report.json'
    command = [sys.executable, str(ROOT / 'scripts/compare_review_handoffs.py'), str(captures[0]), '--report', str(target)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 1, result.stderr
    assert json.loads(target.read_text())['status'] == 'incomplete'
    assert target.stat().st_mode & 0o777 == 0o600
    assert subprocess.run(command, capture_output=True).returncode == 2


def test_report_path_inside_repository_is_rejected():
    with pytest.raises(ValueError, match='private paths'):
        compatibility.private_path(ROOT / 'accidental-capture.json')
