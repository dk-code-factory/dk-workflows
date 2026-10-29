"""Cleanup coverage with real Git, retained preparations and bounded sessions."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from shared import architecture, executors, review_coverage as coverage, review_reconciliation as reconcile, review_sessions
from shared.command_results import classification
from shared.private_state import read, write

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'dk-pr-decisions/tests'))
from preparation_fixtures import Package, analysis, descriptor, prep, retain, architecture_review
sys.path.insert(0, str(ROOT / 'dk-pr-address-reviews/scripts'))
import fetch_pr_conversation as conversation_exporter
import fetch_unresolved_review_threads as inline_exporter


def module(name):
    spec = importlib.util.spec_from_file_location('reconciliation_' + name,
        ROOT / 'dk-pr-iter/scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


state, completion = module('run_state'), module('completion')


class Scenario:
    def __init__(self, tmp_path, monkeypatch, reviewers=('example',), cycles=5, descriptor_links=False,
                 extra_filename=None, decisions_choices=False, architecture_rule=None):
        self.package = p = Package(tmp_path)
        if architecture_rule is not None:
            architecture.edit(p.repo, architecture_rule,
                expected=architecture.current(p.repo, baseline=p.head)['fingerprint'],
                user={'actor': 'user', 'instruction': 'Keep the inspected transform boundary.', 'source': 'fixture user'})
        self.reviewers = list(reviewers)
        if len(reviewers) > 1:
            p.manifest['evidence']['reviewers'] = [str(p.write(name + '-reviewer.json',
                descriptor(name, p.repo / 'transform.py'))) for name in reviewers]
        if descriptor_links:
            self.descriptor_links = []
            for index, value in enumerate(p.manifest['evidence']['reviewers']):
                link = p.root / f'reviewer-link-{index}.json'
                link.symlink_to(p.root / value)
                self.descriptor_links.append(link)
            p.manifest['evidence']['reviewers'] = [link.name for link in self.descriptor_links]
        self.git = lambda *args: coverage.git(p.repo, *args)
        self.base = p.head
        (p.repo / 'change.txt').write_text('Reviewed change\n')
        if extra_filename is not None:
            (p.repo / extra_filename).write_text('Reviewed filename transport\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'Reviewed commit')
        self.reviewed_head = self.git('rev-parse', 'HEAD')
        p.context['head_sha'] = p.decisions['head_sha'] = self.reviewed_head
        p.write('context.json', p.context)
        p.write('decisions.json', p.decisions)
        p.manifest['mode'] = 'iteration'
        self.feedback('Before cleanup')
        p.manifest['evidence']['feedback'] = ['feedback.json']
        p.write('history.json', {'obligation': 'Inspect the supported transform'})
        p.manifest['evidence']['run_history'] = ['history.json']
        p.write('rules.json', {'rule': 'Preserve the supported transform'})
        p.manifest['evidence']['rules'] = ['rules.json']
        p.write('manifest.json', p.manifest)
        if decisions_choices:
            p.manifest['evidence']['choices'] = ['decisions.json']
            p.write('manifest.json', p.manifest)
        self.path = tmp_path / 'run.json'
        self.original_path = p.prepare(analysis(reviewers))['artifact']
        self.original = prep.read(self.original_path)
        record = {}
        state.start_run(record)
        state.attach_preparation(record, self.original_path, p.context['url'], self.reviewed_head)
        for _ in range(cycles):
            state.next_cycle(record, self.reviewed_head)
        binding = state.review_session(record, self.reviewed_head, self.path)
        self.session = review_sessions.Session(p.context, self.original, self.reviewers, path=binding['session_file'])
        self.result = {'complete': True, 'executed_reviewers': self.reviewers, 'reused_reviewers': [],
                       'architecture_review': architecture_review(self.original, p.context, p.decisions),
                       'skipped_reviewers': [], 'findings': []}
        self.reference = self.session.finish(self.result)
        state.register_review(record, {'review_artifact': self.reference}, self.reviewed_head)
        write(self.path, record)
        self.original_bytes = Path(self.reference['path']).read_bytes()
        self.retained_bytes = {path: path.read_bytes() for path in
            [Path(self.original_path), Path(binding['session_file']),
             *[p for p in (Path(self.original_path).parent / 'evidence').rglob('*') if p.is_file()]]}
        self.calls = []
        self.different = None
        self.provider_hook = None
        def provider(prompt, checkout, *, attempt=0, inherit_architecture=True):
            assert inherit_architecture is False
            self.calls.append(attempt)
            binding = json.JSONDecoder().raw_decode(prompt[prompt.index('{"binding":'):])[0]['binding']
            if self.provider_hook:
                self.provider_hook()
            return json.dumps(self.comparison(binding))
        monkeypatch.setattr(executors, 'execute', provider)
        monkeypatch.setattr(reconcile.series, 'live_snapshot', lambda *a: self.live())

    def comparison(self, binding):
        def verdict(category, key):
            refs = ['original/analysis', 'current/analysis']
            if category == 'sources':
                refs += [side + '/' + key for side in ('original', 'current')
                         if side + '/' + key in binding['evidence']]
            return {'verdict': 'different' if self.different == (category, key) else 'equivalent',
                    'rationale': 'Source inspection shows ' + ('a new obligation' if self.different == (category, key)
                                                             else 'the same engineering requirement'),
                    'evidence': refs}
        return {'equivalent': self.different is None,
                'rationale': 'New review obligation' if self.different else 'Only synchronization wording changed',
                'fields': {key: verdict('fields', key) for key in binding['fields']},
                'sources': {key: verdict('sources', key) for key in binding['changed_sources']},
                **({'decisions': {key: {**verdict('decisions', key),
                    'maps_to': binding['decision_targets'][0],
                    'discussion_sources': list(item['discussion_sources']),
                    'evidence': [binding['decision_targets'][0], 'current/decisions#' + key]
                        + [refs[0] for refs in item['discussion_sources'].values()]}
                    for key, item in binding['decision_additions'].items()}}
                   if binding.get('comparison_version') == 2 else {})}

    def feedback(self, status, items=()):
        items = list(items)
        self.package.write('feedback.json', {
            'schemaVersion': 1, 'complete': True,
            'pullRequest': {'url': self.package.context['url'], 'headRefOid': self.package.context['head_sha']},
            'threads': [], 'threadCount': 0, 'threadsSha256': prep.digest([]),
            'items': items, 'itemCount': len(items), 'itemsSha256': prep.digest(items), 'status': status})

    def refresh(self, *, published=True, change=None, feedback_items=()):
        p = self.package
        if published:
            self.git('commit', '--amend', '-qm', 'Clean series boundary')
        self.head = self.git('rev-parse', 'HEAD')
        p.context['head_sha'] = p.decisions['head_sha'] = self.head
        p.write('context.json', p.context)
        p.write('decisions.json', p.decisions)
        self.feedback('Fresh feedback after cleanup', feedback_items)
        result = analysis(self.reviewers)
        result['sync_reason'] = 'Published scope and Decisions are synchronized after cleanup.'
        if change:
            change(result)
        self.prepare(result)
        self.assess_series(published=published)

    def prepare(self, result=None):
        p = self.package
        parent_path = read(self.path, {})['preparation']['path']
        parent = prep.read(parent_path)
        full = result is not None
        result = result if full else parent['analysis']
        p.manifest['evidence']['prior_preparation'] = [parent_path]
        p.write('manifest.json', p.manifest)
        def reassess(prompt, checkout):
            inputs = json.JSONDecoder().raw_decode(prompt.split('INPUT INDEX (read original paths):\n', 1)[1])[0]
            batch = json.JSONDecoder().raw_decode(prompt.split(
                'REASSESSMENT (one batch, read original changed and governing sources):\n', 1)[1])[0]
            value = retain(parent, ids=batch['affected'], unmapped=batch['unmapped_changes'])
            conclusions = prep.graph_api.conclusions(result)
            original_conclusions = prep.graph_api.conclusions(parent['analysis'])
            for item in value['dispositions']:
                node = parent['graph'][item['id']]
                location = node['location']
                if any(key not in inputs['sources'] for key in node['sources']):
                    item['watches'] = {key: node[key] for key in ('depends_on', 'paths', 'sources', 'decisions')}
                    item['watches']['sources'] = [key for key in node['sources'] if key in inputs['sources']]
                if conclusions[location] != original_conclusions[location]:
                    item.update(action='replace', value=conclusions[location])
            return value
        refreshed = prep.prepare(p.manifest_path, p.root / 'versions',
            previous_preparation=parent_path, full_analysis=full, analyzer=reassess)
        if full:
            assert refreshed['refresh']['model_call_count'] == 1
        self.current_path = refreshed['artifact']
        self.current = prep.read(self.current_path)
        assert self.current['analysis'] == result
        if self.current['preparation_id'] != parent['preparation_id']:
            assert self.current['parent']['preparation_id'] == parent['preparation_id']
        state.mutate(self.path, {}, lambda record: state.attach_preparation(
            record, self.current_path, p.context['url'], self.head,
            expected_parent=parent['preparation_id']))
        return refreshed

    def assess_series(self, *, published):
        self.assessment = dict(schema_version=1, pr_url=self.package.context['url'], head_repo='example/fixture',
            reviewed_head=self.reviewed_head, reviewed_tree=self.git('rev-parse', f'{self.reviewed_head}^{{tree}}'),
            target_tip=self.base, target_branch='main', final_head=self.head,
            outcome='published' if published else 'no_op', commit_count=1, reasons=['One coherent change'])
        self.assessment_path = self.package.write('assessment.json', self.assessment)

    def live(self):
        return {**self.assessment, 'final_tree': self.git('rev-parse', f'{self.head}^{{tree}}')}

    def run(self):
        return reconcile.reconcile(self.path, self.head, self.assessment_path, state.reconciliation_api())

    def verification(self, prepared=None):
        prepared = prepared or self.current
        return self.package.write('verification.json', {
            'verification_version': 2, 'engineering_id': prepared['engineering_id'], 'preparation_id': prepared['preparation_id'],
            'head_sha': prepared['identity']['head_sha'], 'pr_url': prepared['identity']['pr_url'],
            'checks': {name: {'satisfied': True, 'rationale': 'Current transform and tests inspected',
                             'evidence': [prepared['sources']['production/0']]}
                       for name in ('behavior', 'placement', 'test_sufficiency', 'compatibility', 'complexity')}})


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    return Scenario(tmp_path, monkeypatch)


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_reconciliation_semantic_call_isolates_ambient_parent_packet(tmp_path, monkeypatch, host):
    native_execute = executors.execute
    s = Scenario(tmp_path, monkeypatch)
    s.refresh()
    packet = prep.architecture.packet(s.package.repo, 'worker', ['transform.py'],
        task='parent-task', baseline=s.package.head)
    monkeypatch.setenv('DK_HOST', host)
    monkeypatch.setenv('DK_ARCHITECTURE_PACKET', json.dumps(packet))
    monkeypatch.delenv('DK_ARCHITECTURE_AGENT', raising=False)
    parent = os.environ['DK_ARCHITECTURE_PACKET']
    attempts = []
    def process(prompt, checkout, selected_host, env, resolved, attempt, timeout):
        assert selected_host == host and resolved is None
        assert 'DK_ARCHITECTURE_PACKET' not in env and 'DK_ARCHITECTURE_AGENT' not in env
        assert os.environ['DK_ARCHITECTURE_PACKET'] == parent
        attempts.append(attempt)
        binding = json.JSONDecoder().raw_decode(prompt[prompt.index('{"binding":'):])[0]['binding']
        return 'invalid JSON' if attempt == 0 else json.dumps(s.comparison(binding))
    monkeypatch.setattr(executors, 'execute', native_execute)
    monkeypatch.setattr(executors, 'managed_update', lambda *_: None)
    monkeypatch.setattr(executors, 'execute_process', process)
    result = s.run()
    assert result['status'] == 'reconciled' and attempts == [0, 1]
    assert result['executed_reviewers'] == [] and len(read(s.path, {})['cycles']) == 5
    assert os.environ['DK_ARCHITECTURE_PACKET'] == parent


@pytest.mark.parametrize('legacy', [False, True])
def test_retained_manifest_relative_order_survives_missing_originals(scenario, legacy):
    s = scenario
    original, _, _, sources = prep.retained(s.original_path)
    if legacy:
        original.pop('reviewer_paths')
    s.package.manifest_path.unlink()
    (s.package.root / 'reviewer.json').unlink()
    assert reconcile.reviewer_order(sources, original, prep) == ['example']


@pytest.mark.parametrize('kind', ['missing', 'ambiguous'])
def test_retained_manifest_reviewer_mapping_must_be_unambiguous(scenario, kind):
    original, _, _, sources = prep.retained(scenario.original_path)
    if kind == 'missing':
        original['sources']['reviewers/0']['path'] += '.unmapped'
    else:
        original['sources']['reviewers/1'] = dict(original['sources']['reviewers/0'])
    with pytest.raises(ValueError, match='uniquely match retained descriptors|ambiguous retained reviewer descriptor path'):
        reconcile.reviewer_order(sources, original, prep)


@pytest.mark.parametrize('reviewed_order', [['other'], ['example', 'example']])
def test_recorded_order_must_match_the_retained_reviewer_cohort(scenario, reviewed_order):
    original, _, _, sources = prep.retained(scenario.original_path)
    with pytest.raises(ValueError, match='cover the prepared reviewers exactly once'):
        reconcile.reviewer_order(sources, original, prep, reviewed_order=reviewed_order)


@pytest.mark.parametrize('reordered', [False, True])
@pytest.mark.parametrize('current_aliases', [False, True])
def test_descriptor_links_can_disappear_without_losing_order(tmp_path, monkeypatch, reordered, current_aliases):
    s = Scenario(tmp_path, monkeypatch, reviewers=('second', 'example'), descriptor_links=True)
    if not current_aliases:
        s.package.manifest['evidence']['reviewers'] = [str(link.resolve()) for link in s.descriptor_links]
    if reordered:
        s.package.manifest['evidence']['reviewers'].reverse()
    s.refresh(published=False)
    assert s.current['reviewer_paths'] == [str(link.resolve()) for link in
        (reversed(s.descriptor_links) if reordered else s.descriptor_links)]
    for link in s.descriptor_links:
        link.unlink()
    assert prep.gate(s.current_path, pr_url=s.package.context['url'], head_sha=s.head) == s.current
    assert s.original['reviewers'] == s.current['reviewers']
    assert prep.retained(s.original_path)[0] == s.original
    receipt = s.run()
    if reordered:
        assert receipt['status'] == 'blocked' and 'ordered reviewers' in receipt['reason']
        assert s.calls == []
    else:
        assert receipt['status'] == 'reconciled' and s.calls == [0]
        retained = state.verify_review(s.path, s.current)
        assert retained['preparation_id'] == s.original['preparation_id']
        assert retained['retained_coverage']['executed_reviewers'] == []
    assert Path(s.reference['path']).read_bytes() == s.original_bytes
    assert len(read(s.path, {})['cycles']) == 5


def test_legacy_manifest_alias_order_cannot_be_inferred(tmp_path, monkeypatch):
    s = Scenario(tmp_path, monkeypatch, reviewers=('second', 'example'), descriptor_links=True)
    original, _, _, sources = prep.retained(s.original_path)
    original.pop('reviewer_paths')
    for link in s.descriptor_links:
        link.unlink()
    with pytest.raises(ValueError, match='uniquely match retained descriptors; refresh preparation'):
        reconcile.reviewer_order(sources, original, prep)


def test_reconciliation_prompt_roundtrips_non_utf8_git_filename(tmp_path, monkeypatch):
    filename_bytes = b'filename-\xff.py'
    filename = os.fsdecode(filename_bytes)
    s = Scenario(tmp_path, monkeypatch, extra_filename=filename)
    assert filename in s.original['inputs']['tree']
    s.refresh(change=lambda value: value['placement']['production']['locations'].append(filename))
    provider = executors.execute
    def utf8_provider(prompt, checkout, *, attempt=0, inherit_architecture=True):
        transported = prompt.encode('utf-8').decode('utf-8')
        payload = json.JSONDecoder().raw_decode(transported[transported.index('{"binding":'):])[0]
        assert payload['original_analysis'] == s.original['analysis']
        assert payload['current_analysis'] == s.current['analysis']
        assert os.fsencode(payload['current_analysis']['placement']['production']['locations'][-1]) == filename_bytes
        return provider(transported, checkout, attempt=attempt, inherit_architecture=inherit_architecture)
    monkeypatch.setattr(executors, 'execute', utf8_provider)
    receipt = s.run()
    assert receipt['status'] == 'reconciled' and receipt['executed_reviewers'] == []
    assert s.calls == [0] and len(read(s.path, {})['cycles']) == 5
    assert state.verify_review(s.path, s.current)['preparation_id'] == s.original['preparation_id']
    assert Path(s.reference['path']).read_bytes() == s.original_bytes


@pytest.mark.parametrize('published', [True, False])
def test_fifth_cycle_cleanup_refresh_reconciles_without_new_review(scenario, published):
    s = scenario
    s.refresh(published=published)
    assert s.current['preparation_id'] != s.original['preparation_id']
    with pytest.raises(ValueError, match='scope lacks review coverage'):
        state.verify_review(s.path, s.current)
    receipt = s.run()
    assert receipt['executed_reviewers'] == [] and receipt['retained_reviewers'] == ['example']
    assert receipt['reviewed_head'] == s.reviewed_head
    assert s.calls == [0]
    artifact = state.verify_review(s.path, s.current, assessment=s.assessment)
    assert artifact['head'] == s.reviewed_head and artifact['preparation_id'] == s.original['preparation_id']
    assert artifact['retained_coverage']['executed_reviewers'] == []
    assert Path(s.reference['path']).read_bytes() == s.original_bytes
    stale = s.verification(s.original)
    def gate(verification, **kwargs):
        return completion.completion_gate(s.reviewed_head, s.head, s.head, True, False, 'success',
            preparation=s.current_path, pr_url=s.package.context['url'], body='', run_record=s.path,
            verification=verification, assessment=s.assessment, series_snapshot=s.live(), **kwargs)
    assert gate(stale) == 'engineering_verification_invalid'
    assert gate(s.verification()) == 'clean'
    with pytest.raises(ValueError, match='five review cycles exhausted'):
        state.next_cycle(read(s.path, {}), s.head)
    state.start_run(read(s.path, {}), resume=True)
    again = s.run()
    assert again['reused_reconciliation'] and again['reconciliation'] == receipt['reconciliation']
    assert s.calls == [0] and len(read(s.path, {})['cycles']) == 5


@pytest.mark.parametrize('kind', ['boundary', 'addendum', 'feedback', 'ambiguous'])
def test_substantive_or_ambiguous_changes_block_without_correction(scenario, monkeypatch, kind):
    s = scenario
    def change(value):
        if kind == 'boundary': value['boundaries']['compatibility'][0]['text'] = 'Drop supported inputs'
        if kind == 'addendum': value['reviewer_addenda']['example'] = 'Review additional interfaces'
    s.refresh(change=change)
    field = '/boundaries/compatibility/0/text' if kind == 'boundary' else '/reviewer_addenda/example'
    s.different = ('sources', 'feedback/0') if kind == 'feedback' else ('fields', field)
    if kind == 'ambiguous':
        original = s.comparison
        def ambiguous(binding):
            value = original(binding)
            value['fields'][field]['verdict'] = 'ambiguous'
            return value
        monkeypatch.setattr(s, 'comparison', ambiguous)
    receipt = s.run()
    assert receipt['status'] == 'blocked' and 'obligation' in receipt['reason']
    assert classification('run-state', 0, receipt)[0] == 'blocked'
    assert s.calls == [0]
    assert s.run()['status'] == 'blocked' and s.calls == [0]
    with pytest.raises(ValueError, match='not semantically equivalent'):
        state.verify_review(s.path, s.current)


@pytest.mark.parametrize('kind', ['body', 'base', 'decisions', 'rules', 'reviewers', 'order', 'tree'])
def test_identity_changes_never_reach_semantic_provider(scenario, kind):
    s, p = scenario, scenario.package
    if kind == 'body':
        p.decisions['body_hash'] = hashlib.sha256(b'new body').hexdigest()
        p.write('metadata.json', {'body': 'new body', 'title': 'Transform fix'})
    if kind == 'base':
        p.context['base_sha'] = s.reviewed_head
    if kind == 'decisions':
        p.decisions['local_decisions'] = [{'text': 'Change scope', 'source': 'user'}]
        p.decisions['decisions'] = [{'id': hashlib.sha256(b'Change scope').hexdigest()[:20],
            'text': 'Change scope', 'source_type': 'local', 'references': [], 'source': 'user',
            'status': 'active', 'publication_status': 'unpublished', 'superseded_by': None}]
    if kind == 'rules':
        p.write('rules.json', {'rule': 'Additional obligations'})
    if kind == 'reviewers':
        descriptor = prep.read(p.root / 'reviewer.json')
        descriptor['effective_prompt'] = 'Different review instructions'
        p.write('reviewer.json', descriptor)
    if kind == 'order':
        # Descriptor ordering must match the originating session even when its
        # set is otherwise consistent with preparation.
        session = read(Path(read(s.path, {})['review_sessions'][-1]['session_file']), {})
        session['identity']['reviewers'] = ['other', 'example']
        write(Path(read(s.path, {})['review_sessions'][-1]['session_file']), session)
    if kind == 'tree':
        (p.repo / 'change.txt').write_text('Unreviewed fix\n')
        s.git('add', '.')
        s.git('commit', '-qm', 'Fix after fifth cycle')
    s.refresh(published=False)
    if kind == 'order':
        with pytest.raises(ValueError): s.run()
    else:
        assert s.run()['status'] == 'blocked'
    assert s.calls == []


@pytest.mark.parametrize('kind', ['historical_missing', 'historical_tamper', 'reconciliation_missing',
                                  'reconciliation_tamper', 'assessment_missing', 'assessment_tamper',
                                  'current_source', 'run', 'cycle', 'attempt', 'result', 'session'])
def test_tampered_or_stale_evidence_never_supplies_coverage(scenario, kind):
    s = scenario
    s.refresh()
    receipt = s.run()
    if kind.startswith('historical'):
        target = Path(s.original_path).parent / 'evidence/feedback/0'
    elif kind.startswith('reconciliation'):
        target = Path(receipt['reconciliation']['path'])
    elif kind.startswith('assessment'):
        target = s.assessment_path
    elif kind == 'current_source':
        target = Path(s.current['sources']['feedback/0']['snapshot'])
    else:
        record = read(s.path, {})
        if kind == 'run': record['iteration_run']['id'] = 'changed'
        if kind == 'cycle': record['cycles'][-1]['number'] = 4
        if kind == 'attempt': record.setdefault('cycle_retries', []).append({'number': 5, 'head': s.head})
        if kind == 'result': record['review_results'][-1]['sha256'] = 'a' * 64
        if kind == 'session': record['review_sessions'] = []
        write(s.path, record)
        target = None
    if target:
        if kind.endswith('missing'): target.unlink()
        else:
            target.chmod(0o600)
            target.write_text('{}')
    with pytest.raises((ValueError, OSError, KeyError)):
        state.verify_review(s.path, s.current)


@pytest.mark.parametrize('kind', ['head', 'source', 'assessment', 'run', 'target'])
def test_movement_during_comparison_registers_nothing(scenario, kind):
    s = scenario
    s.refresh()
    def move():
        if kind == 'head':
            coverage.git(s.current['workspace'], '-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '--allow-empty', '-qm', 'Concurrent movement')
        if kind == 'source':
            target = Path(s.current['sources']['feedback/0']['snapshot'])
            target.chmod(0o600)
            target.write_text('{}')
        if kind == 'assessment': s.assessment_path.write_text('{}')
        if kind == 'run':
            state.mutate(s.path, {}, lambda record: record['iteration_run'].update(id='changed'))
        if kind == 'target': s.assessment['target_tip'] = s.reviewed_head
    s.provider_hook = move
    with pytest.raises((ValueError, OSError)):
        s.run()
    assert not read(s.path, {}).get('review_reconciliations')


@pytest.mark.parametrize('kind', ['missing_field', 'missing_source', 'bad_reference', 'contradiction',
                                  'invalid_json', 'invalid_verdict'])
def test_malformed_provider_output_gets_one_correction(scenario, monkeypatch, kind):
    s = scenario
    s.refresh()
    original = s.comparison
    def malformed(binding):
        value = original(binding)
        if kind == 'missing_field': value['fields'].pop(next(iter(value['fields'])))
        if kind == 'missing_source': value['sources'] = {}
        if kind == 'bad_reference': next(iter(value['fields'].values()))['evidence'] = ['unknown']
        if kind == 'contradiction': value['equivalent'] = False
        if kind == 'invalid_verdict': next(iter(value['fields'].values()))['verdict'] = []
        if kind == 'invalid_json': return []
        return value
    monkeypatch.setattr(s, 'comparison', malformed)
    with pytest.raises(ValueError, match='invalid after one correction'): s.run()
    assert s.calls == [0, 1] and not read(s.path, {}).get('review_reconciliations')


def test_provider_failure_and_interruption_can_resume_without_a_cycle(scenario, monkeypatch):
    s = scenario
    s.refresh()
    provider = executors.execute
    for error in (RuntimeError('Provider unavailable'), KeyboardInterrupt()):
        def fail(*a, **kw): raise error
        monkeypatch.setattr(executors, 'execute', fail)
        with pytest.raises(type(error)): s.run()
        assert not read(s.path, {}).get('review_reconciliations')
        assert len(read(s.path, {})['cycles']) == 5
    monkeypatch.setattr(executors, 'execute', provider)
    assert s.run()['status'] == 'reconciled'


def test_completion_requires_same_assessment_and_live_ci(scenario):
    s = scenario
    s.refresh()
    s.run()
    other = {**s.assessment, 'reasons': ['Different assessment']}
    with pytest.raises(ValueError, match='same|reconciled cleanup assessment'):
        state.verify_review(s.path, s.current, assessment=other)
    duplicate = s.package.write('other-assessment.json', s.assessment)
    with pytest.raises(ValueError, match='same fingerprinted'):
        state.verify_review(s.path, s.current, assessment_path=duplicate)
    def gate(ci, live):
        return completion.completion_gate(s.reviewed_head, s.head, s.head, True, False, ci,
            preparation=s.current_path, pr_url=s.package.context['url'], body='', run_record=s.path,
            verification=s.verification(), assessment=s.assessment, series_snapshot=live)
    assert gate('success', {**s.live(), 'target_tip': s.head}) == 'series_target_changed'
    assert gate({'head': s.reviewed_head, 'result': 'success'}, s.live()) == 'ci_not_green'
    assert gate('success', s.live()) == 'clean'


def renewed_assessment(s):
    s.assessment_path = reconcile.series.save(s.package.root / 'first-assessment.json',
                                             s.assessment, s.package.repo)
    renewed = reconcile.series.save(s.package.root / 'renewed-assessment.json',
                                    s.assessment, s.package.repo)
    assert s.assessment_path.read_bytes() == renewed.read_bytes()
    return renewed


def test_completion_selects_newest_reconciled_assessment_path(scenario):
    s = scenario
    s.refresh()
    renewed = renewed_assessment(s)
    first = s.run()
    second = reconcile.reconcile(s.path, s.head, renewed, state.reconciliation_api())
    assert first['status'] == second['status'] == 'reconciled' and s.calls == [0, 0]
    for kwargs in ({'assessment': s.assessment, 'assessment_path': renewed}, {'assessment': s.assessment}, {}):
        result = state.verify_review(s.path, s.current, **kwargs)
        assert result['retained_coverage']['reconciliation'] == second['reconciliation']
    with pytest.raises(ValueError, match='same fingerprinted'):
        state.verify_review(s.path, s.current, assessment_path=s.assessment_path)
    assert reconcile.reconcile(s.path, s.head, renewed, state.reconciliation_api())['reused_reconciliation']
    assert s.calls == [0, 0]


def test_assessment_path_renewal_reuses_terminal_disagreement(scenario):
    s = scenario
    s.refresh()
    renewed = renewed_assessment(s)
    s.different = ('sources', 'feedback/0')
    first = s.run()
    assert first['status'] == 'blocked'
    s.different = None
    second = reconcile.reconcile(s.path, s.head, renewed, state.reconciliation_api())
    assert second['status'] == 'blocked' and second['comparison'] == first['comparison']
    assert s.calls == [0]
    with pytest.raises(ValueError, match='not semantically equivalent'):
        state.verify_review(s.path, s.current, assessment_path=renewed)


@pytest.mark.parametrize('negative_first', [True, False])
def test_cross_path_opposite_verdicts_remain_terminally_blocked(scenario, monkeypatch, negative_first):
    s = scenario
    s.refresh()
    renewed = renewed_assessment(s)
    calls = []
    def provider(prompt, checkout, *, attempt=0, inherit_architecture=True):
        assert inherit_architecture is False
        binding = json.JSONDecoder().raw_decode(prompt[prompt.index('{"binding":'):])[0]['binding']
        outer = not calls
        calls.append(attempt)
        s.different = ('sources', 'feedback/0') if outer != negative_first else None
        value = s.comparison(binding)
        if outer:
            s.run()
        return json.dumps(value)
    monkeypatch.setattr(executors, 'execute', provider)
    assert reconcile.reconcile(s.path, s.head, renewed, state.reconciliation_api())['status'] == 'blocked'
    items = read(s.path, {})['review_reconciliations']
    assert len(items) == 2 and items[0]['binding_hash'] != items[1]['binding_hash']
    assert s.run()['status'] == 'blocked' and calls == [0, 0]
    with pytest.raises(ValueError, match='not semantically equivalent'):
        state.verify_review(s.path, s.current, assessment_path=renewed)
    assert len(read(s.path, {})['cycles']) == 5
    assert Path(s.reference['path']).read_bytes() == s.original_bytes


def test_reordered_reviewers_block_even_with_identical_descriptors(tmp_path, monkeypatch):
    s = Scenario(tmp_path, monkeypatch, reviewers=('second', 'example'))
    s.package.manifest['evidence']['reviewers'].reverse()
    s.refresh()
    assert s.original['reviewers'] == s.current['reviewers']
    receipt = s.run()
    assert receipt['status'] == 'blocked' and 'ordered reviewers' in receipt['reason']
    assert s.calls == []


@pytest.mark.parametrize('source', ['feedback', 'run_history'])
@pytest.mark.parametrize('equivalent', [True, False])
def test_equal_scope_refresh_requires_source_comparison(scenario, source, equivalent):
    s = scenario
    if source == 'run_history':
        s.package.write('history.json', {'obligation': 'Inspect the newly reported failure'})
        s.package.manifest['evidence'][source] = ['history.json']
    s.refresh(published=False, change=lambda value: value.update(sync_reason=''))
    assert coverage.scope(s.original) != coverage.scope(s.current)
    with pytest.raises(ValueError, match='scope lacks review coverage'):
        state.verify_review(s.path, s.current)
    if not equivalent:
        s.different = ('sources', source + '/0')
    receipt = s.run()
    if equivalent:
        assert receipt['status'] == 'reconciled'
        assert state.verify_review(s.path, s.current)['retained_coverage']['executed_reviewers'] == []
    else:
        assert receipt['status'] == 'blocked'
        with pytest.raises(ValueError, match='not semantically equivalent'):
            state.verify_review(s.path, s.current)
    assert s.run()['status'] == receipt['status'] and s.calls == [0]
    assert Path(s.reference['path']).read_bytes() == s.original_bytes


def test_equal_scope_reordered_reviewers_cannot_retain_coverage(tmp_path, monkeypatch):
    s = Scenario(tmp_path, monkeypatch, reviewers=('second', 'example'))
    s.package.manifest['evidence']['reviewers'].reverse()
    s.refresh(published=False, change=lambda value: value.update(sync_reason=''))
    assert coverage.scope(s.original) != coverage.scope(s.current)
    assert s.run()['status'] == 'blocked'
    with pytest.raises(ValueError):
        state.verify_review(s.path, s.current)
    assert s.calls == []


@pytest.mark.parametrize('negative_first', [True, False])
def test_concurrent_opposite_verdicts_remain_terminally_blocked(scenario, monkeypatch, negative_first):
    s = scenario
    s.refresh()
    calls = []
    def provider(prompt, checkout, *, attempt=0, inherit_architecture=True):
        assert inherit_architecture is False
        binding = json.JSONDecoder().raw_decode(prompt[prompt.index('{"binding":'):])[0]['binding']
        outer = not calls
        calls.append(attempt)
        # The first invocation is suspended outside the lock while the second
        # compares and registers exactly the same retained evidence.
        s.different = ('sources', 'feedback/0') if outer != negative_first else None
        value = s.comparison(binding)
        if outer:
            s.run()
        return json.dumps(value)
    monkeypatch.setattr(executors, 'execute', provider)
    assert s.run()['status'] == 'blocked'
    items = read(s.path, {})['review_reconciliations']
    assert len(items) == 2 and items[0]['binding_hash'] == items[1]['binding_hash']
    assert s.run()['status'] == 'blocked' and calls == [0, 0]
    with pytest.raises(ValueError, match='not semantically equivalent'):
        state.verify_review(s.path, s.current)
    assert len(read(s.path, {})['cycles']) == 5
    assert Path(s.reference['path']).read_bytes() == s.original_bytes


def test_exact_reviewed_preparation_honors_its_own_blocked_comparison(scenario):
    s = scenario
    s.head = s.reviewed_head
    s.assess_series(published=False)
    assert state.verify_review(s.path, s.original)['preparation_id'] == s.original['preparation_id']
    s.different = ('fields', '/completion_criteria')
    assert s.run()['status'] == 'blocked'
    with pytest.raises(ValueError, match='not semantically equivalent'):
        state.verify_review(s.path, s.original)
    refreshed = prep.prepare(s.original['recovery_manifest']['path'], s.package.versions,
                             previous_preparation=s.original_path)
    current = prep.read(refreshed['artifact'])
    assert current['preparation_id'] != s.original['preparation_id']
    assert current['engineering_id'] == s.original['engineering_id']
    state.mutate(s.path, {}, lambda record: state.attach_preparation(
        record, refreshed['artifact'], s.package.context['url'], s.reviewed_head))
    with pytest.raises(ValueError, match='not semantically equivalent'):
        state.verify_review(s.path, current)


@pytest.mark.parametrize('refreshed', [True, False])
def test_identical_preparation_in_new_directory_preserves_disagreement(scenario, refreshed):
    s = scenario
    if refreshed:
        s.refresh()
    else:
        s.current, s.current_path, s.head = s.original, s.original_path, s.reviewed_head
        s.assess_series(published=False)
    s.different = ('fields', '/completion_criteria')
    first = s.run()
    assert first['status'] == 'blocked'
    before = Path(s.current_path)
    directory = before.parent.with_name(before.parent.name + '-relocated')
    shutil.copytree(before.parent, directory)
    s.current_path = str(directory / before.name)
    assert before != Path(s.current_path)
    assert before.read_bytes() == Path(s.current_path).read_bytes()
    assert prep.read(s.current_path)['preparation_id'] == s.current['preparation_id']
    state.mutate(s.path, {}, lambda record: state.attach_preparation(
        record, s.current_path, s.package.context['url'], s.head))
    s.different = None
    second = s.run()
    assert second['status'] == 'blocked' and second['comparison'] == first['comparison']
    assert s.calls == [0]
    with pytest.raises(ValueError):
        state.verify_review(s.path, s.current, assessment_path=s.assessment_path)
    assert len(read(s.path, {})['cycles']) == 5
    assert Path(s.reference['path']).read_bytes() == s.original_bytes
    # Ignoring location in terminal identity does not waive the actual retained
    # copy's integrity when the newly attached preparation is used.
    snapshot = Path(s.current_path).parent / 'evidence/feedback/0'
    snapshot.chmod(0o600)
    snapshot.write_text('{}')
    with pytest.raises(ValueError):
        s.run()


def test_fresh_review_supersedes_only_old_result_comparisons(tmp_path, monkeypatch):
    s = Scenario(tmp_path, monkeypatch, cycles=4)
    s.refresh(published=False)
    s.different = ('sources', 'feedback/0')
    assert s.run()['status'] == 'blocked'
    record = read(s.path, {})
    state.next_cycle(record, s.head)
    binding = state.review_session(record, s.head, s.path)
    session = review_sessions.Session(s.package.context, s.current, s.reviewers, path=binding['session_file'])
    reference = session.finish({**s.result, 'architecture_review': architecture_review(
        s.current, s.package.context, s.package.decisions)})
    state.register_review(record, {'review_artifact': reference}, s.head)
    write(s.path, record)
    assert state.verify_review(s.path, s.current)['preparation_id'] == s.current['preparation_id']
    assert Path(s.reference['path']).read_bytes() == s.original_bytes


def test_successful_correction_uses_same_evidence_and_no_extra_cycle(scenario, monkeypatch):
    s = scenario
    s.refresh()
    original = s.comparison
    monkeypatch.setattr(s, 'comparison', lambda binding: {} if len(s.calls) == 1 else original(binding))
    assert s.run()['status'] == 'reconciled'
    assert s.calls == [0, 1] and len(read(s.path, {})['cycles']) == 5


@pytest.mark.parametrize('version,missing', [(1, 'sources'), (2, 'decisions')])
def test_comparison_correction_names_all_required_categories(scenario, version, missing):
    s = scenario
    s.refresh()
    binding, _, _, _ = reconcile.snapshot(read(s.path, {}), s.head, s.assessment_path,
                                          state.reconciliation_api(), version=version)
    complete = s.comparison(binding)
    prompts = []
    def provider(prompt, checkout, *, attempt=0):
        prompts.append(prompt)
        return json.dumps(complete if attempt else {key: value for key, value in complete.items() if key != missing})
    result = executors.execute_json('Compare retained preparation evidence.', s.package.context['checkout'],
        executor=provider, validate=lambda value: reconcile.validate_comparison(value, binding))
    hint = prompts[1].split('Correction: ', 1)[1].split('\nReturn ', 1)[0]
    assert all(key in hint for key in complete)
    assert ('decisions' in hint) == (version == 2)
    assert result == complete and len(prompts) == 2


def replace_result(s, result):
    record = read(s.path, {})
    item = s.session.finish(result)
    state.register_review(record, {'review_artifact': item}, s.reviewed_head)
    write(s.path, record)


@pytest.mark.parametrize('invalid_current', ['missing', 'violation', 'stale'])
def test_original_nonempty_coverage_survives_refresh_but_current_review_stays_live(
    tmp_path, monkeypatch, invalid_current
):
    status = 'compliant'
    def judge(prompt, checkout, validate):
        packet = architecture.load(architecture._PACKET.get())
        value = {'complete': True, 'reason': '', 'coverage': [
            {'rule_id': r['id'], 'revision': r['revision'], 'status': status,
             'rationale': 'Inspected the retained transform.',
             'evidence': [{'path': 'transform.py', 'start': 1, 'end': 2}]} for r in packet['rules']]}
        validate(value)
        return value
    monkeypatch.setattr(architecture, 'analyze', judge)
    monkeypatch.setattr(sys.modules[__name__], 'architecture_review', lambda prepared, context, decisions:
        architecture.review(prepared['architecture'], context, decisions, reviewer=judge))
    rule = {'id': 'arch-transform', 'revision': 1, 'text': 'Keep the transform local.',
            'origin': 'user-created', 'sources': [], 'agents': ['worker'], 'patterns': ['transform.py']}
    s = Scenario(tmp_path, monkeypatch, architecture_rule=rule)
    # This acceptance belongs solely to the isolated test fixture.
    replace_result(s, {**s.result, 'complete': False, 'executed_reviewers': [],
        'failed_reviewers': [{'reviewer': 'example', 'status': 'failed', 'reason': 'Fixture provider unavailable'}]})
    state.mutate(s.path, {}, lambda record: state.accept_review_failures(record, s.reviewed_head,
        ['example'], 'Fixture user accepted its recorded failure', 'Retain incomplete fixture coverage'))
    reference, _ = state.current_review(read(s.path, {}), s.original)
    original_bytes = Path(reference['path']).read_bytes()
    architecture.edit(s.package.repo, {**rule, 'text': 'Keep the inspected transform independent.'},
        expected=s.original['architecture']['catalog'],
        user={'actor': 'user', 'instruction': 'Update the transform boundary.', 'source': 'fixture user'})
    run, _, _, _ = setup_precheck(s, monkeypatch, no_op=True)
    shutil.rmtree(s.original['workspace'])  # Original code citations must use the available current checkout.
    assert run()['status'] == 'prechecked'  # Real snapshot -> coverage.verify(original, checkout=...) boundary.
    receipt = s.run()
    assert receipt['status'] == 'reconciled'
    selected = reconcile.coverage_for(read(s.path, {}), s.current, state.reconciliation_api())
    checkout = prep.context_for(s.current)['checkout']
    verified = coverage.verify(reference, s.current, accepted_failures=['example'], reconciliation=selected,
                               checkout=checkout)
    assert not verified['result']['complete'] and verified['accepted_failures'] == ['example']
    current_receipt = read(Path(receipt['reconciliation']['path']), {})
    if invalid_current == 'missing':
        current_receipt.pop('architecture_review')
    elif invalid_current == 'stale':
        current_receipt['architecture_review'] = s.result['architecture_review']
    else:
        status = 'violation'
        current_receipt['architecture_review'] = architecture.review(s.current['architecture'],
            prep.context_for(s.current), s.package.decisions, reviewer=judge)
    path = s.package.write('invalid-current-architecture.json', current_receipt)
    invalid = {**selected, 'artifact': {**selected['artifact'], 'path': str(path),
                                       'sha256': coverage.digest(current_receipt)}}
    with pytest.raises(ValueError, match='architecture evidence|catalog changed|violations|does not cover'):
        coverage.verify(reference, s.current, accepted_failures=['example'], reconciliation=invalid,
                        checkout=checkout)
    architecture.edit(s.package.repo, rule, expected=s.current['architecture']['catalog'],
        identity=s.package.context['repository_identity'],
        user={'actor': 'user', 'instruction': 'Change the current boundary again.', 'source': 'fixture user'})
    with pytest.raises(ValueError, match='catalog changed'):
        coverage.verify(reference, s.current, accepted_failures=['example'], reconciliation=selected,
                        checkout=checkout)
    assert Path(reference['path']).read_bytes() == original_bytes


def test_accepted_provider_failures_stay_incomplete_after_reconciliation(scenario):
    s = scenario
    result = {**s.result, 'complete': False, 'executed_reviewers': [],
              'failed_reviewers': [{'reviewer': 'example', 'status': 'failed', 'reason': 'Provider unavailable'}]}
    replace_result(s, result)
    state.mutate(s.path, {}, lambda record: state.accept_review_failures(record, s.reviewed_head,
        ['example'], 'User explicitly accepted the recorded failure', 'Proceed with incomplete review'))
    s.refresh()
    assert s.run()['retained_reviewers'] == []
    artifact = state.verify_review(s.path, s.current)
    assert not artifact['result']['complete'] and artifact['accepted_failures'] == ['example']
    assert artifact['retained_coverage']['executed_reviewers'] == []


@pytest.mark.parametrize('kind', ['unaccepted_failure', 'unassessed', 'actionable', 'missing_reviewer'])
def test_reconciliation_never_waives_existing_result_guards(scenario, kind):
    s = scenario
    result = {**s.result}
    if kind == 'unaccepted_failure':
        result.update(complete=False, executed_reviewers=[], failed_reviewers=[{
            'reviewer': 'example', 'status': 'failed', 'reason': 'Provider unavailable'}])
    if kind == 'missing_reviewer': result['executed_reviewers'] = []
    if kind in {'unassessed', 'actionable'}:
        finding = {'path': 'transform.py', 'line': 1, 'message': 'Bug', 'reviewers': ['example']}
        result['findings'] = [finding]
        if kind == 'actionable':
            record = read(s.path, {})
            ref = 'review-finding:' + coverage.digest(finding)
            record['threads'] = {ref: {s.reviewed_head: {'disposition': 'fix'}}}
            record['assessments'] = [{'reference': ref, 'head': s.reviewed_head,
                'preparation_id': s.original['preparation_id'], 'disposition': 'within_scope'}]
            write(s.path, record)
    replace_result(s, result)
    s.refresh()
    s.run()
    with pytest.raises(ValueError): state.verify_review(s.path, s.current)


def test_native_reconciliation_command_exposes_blocked_contract(scenario, monkeypatch, capsys):
    s = scenario
    s.refresh()
    s.different = ('sources', 'feedback/0')
    destination = s.path.parent / 'pr-1.json'
    s.path.rename(destination)
    s.path = destination
    monkeypatch.setattr(state, 'root', lambda repo: destination.parent)
    monkeypatch.setattr(sys, 'argv', ['run_state.py', '--repo', 'github.com/example/fixture', '--pr', '1',
        'reconcile-review', '--head', s.head, '--series-assessment', str(s.assessment_path)])
    state.main()
    receipt = json.loads(capsys.readouterr().out)
    assert receipt['differences'][0]['field'] == 'feedback/0'
    assert classification('run-state', 0, receipt)[:2] == ('blocked', 'review_reconciliation_blocked')


def add_discussion(s):
    p = s.package
    url = p.context['url'] + '#discussion_r123'
    p.decisions['discussion_decisions'] = [{'text': 'Keep the supported local transform', 'references': [url]}]
    p.decisions['decisions'] = [{'id': hashlib.sha256(b'Keep the supported local transform').hexdigest()[:20], 'text': 'Keep the supported local transform',
        'references': [url], 'source_type': 'discussion', 'status': 'active',
        'publication_status': 'unpublished', 'superseded_by': None, 'source': None}]
    body = 'Agreed: preserve the supported local transform.'
    comment = {'reference': url, 'text': body, 'url': url}
    return [{**comment, 'conversation': [comment]}]


def restatement_refresh(s, *, published=True, missing_source=False):
    # Retain the approved reply as source evidence, then discover its Decisions.
    feedback = add_discussion(s)
    s.refresh(published=published, feedback_items=[] if missing_source else feedback)


def test_discussion_restatements_retain_fifth_cycle_coverage(scenario):
    s = scenario
    restatement_refresh(s)
    receipt = s.run()
    assert receipt['status'] == 'reconciled' and receipt['executed_reviewers'] == []
    saved = read(Path(receipt['reconciliation']['path']), {})
    assert saved['reconciliation_version'] == 2 and len(saved['comparison']['decisions']) == 1
    assert state.verify_review(s.path, s.current)['head'] == s.reviewed_head
    assert Path(s.reference['path']).read_bytes() == s.original_bytes
    assert len(read(s.path, {})['cycles']) == 5


@pytest.mark.parametrize('kind', ['inline', 'review', 'comment'])
def test_official_exporters_supply_restatement_sources(scenario, monkeypatch, kind):
    import copy
    s = scenario
    p = s.package
    add_discussion(s)
    url = p.context['url'] + {'inline': '#discussion_r123', 'review': '#pullrequestreview-123',
                              'comment': '#issuecomment-123'}[kind]
    p.decisions['discussion_decisions'][0]['references'] = [url]
    p.decisions['decisions'][0]['references'] = [url]
    body = 'Agreed: preserve the supported local transform.'
    metadata = dict(number=1, url=p.context['url'], title='Transform', body='',
                    headRefOid=s.reviewed_head)
    if kind == 'inline':
        raw = {'id': 'THREAD', 'isResolved': False, 'comments': {'nodes': [
            {'id': 'COMMENT', 'body': body, 'url': url}],
            'pageInfo': {'hasNextPage': False, 'endCursor': None}}}
        def gh(arguments, cwd):
            return copy.deepcopy(metadata if arguments[0] == 'pr' else {'data': {'repository': {
                'pullRequest': {'reviewThreads': {'nodes': [raw],
                'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}}})
        monkeypatch.setattr(inline_exporter, 'run_gh', gh)
        exported = inline_exporter.fetch_stable_snapshot(p.context['url'], p.root)
        assert exported['threads'][0]['comments'][0]['text'] == body
        assert 'body' not in exported['threads'][0]['comments'][0]
    else:
        snapshot = inline_exporter.build_output(dict(metadata, owner='example', repo='fixture', description=''), [])
        raw = {'id': 123, 'body': body, 'html_url': url}
        responses = iter([[[raw]] if kind == 'review' else [[]],
                          [[raw]] if kind == 'comment' else [[]],
                          {'headRefOid': s.reviewed_head}] * 2)
        monkeypatch.setattr(conversation_exporter, 'gh_json', lambda *a: next(responses))
        exported = conversation_exporter.fetch(snapshot, p.root)
        assert exported['items'][0]['text'] == body and 'body' not in exported['items'][0]
    s.refresh(published=False)
    p.write('official-feedback.json', exported)
    p.manifest['evidence']['feedback'] = ['official-feedback.json']
    s.prepare(analysis(s.reviewers))
    assert s.run()['status'] == 'reconciled'
    assert state.verify_review(s.path, s.current)['head'] == s.reviewed_head


@pytest.mark.parametrize('record,expected', [
    ({'url': 'https://example.test/source', 'body': 'Legacy retained body'}, True),
    ({'url': 'https://example.test/source', 'text': 'Native retained text'}, True),
    ({'url': 'https://example.test/other', 'text': 'Wrong source'}, False),
    ({'text': 'No own URL'}, False),
    ({'url': 'https://example.test/source', 'text': ' '}, False),
])
def test_discussion_source_requires_own_url_and_nonempty_content(record, expected):
    from shared import decision_restatements
    assert decision_restatements.discussion_sources(record, 'https://example.test/source') is expected


def test_restatement_without_retained_discussion_body_is_blocked(scenario):
    restatement_refresh(scenario, missing_source=True)
    assert scenario.run()['status'] == 'blocked' and scenario.calls == []


@pytest.mark.parametrize('kind', ['missing_mapping', 'unknown_mapping', 'missing_source', 'material'])
def test_restatement_mapping_must_be_complete_and_equivalent(scenario, monkeypatch, kind):
    s = scenario
    restatement_refresh(s)
    compare = s.comparison
    def altered(binding):
        value = compare(binding)
        entry = next(iter(value['decisions'].values()))
        if kind == 'missing_mapping': value['decisions'] = {}
        if kind == 'unknown_mapping': entry['maps_to'] = 'original/analysis#/questions'
        if kind == 'missing_source': entry['discussion_sources'] = []
        if kind == 'material':
            entry['verdict'] = 'different'
            value['equivalent'] = False
        return value
    monkeypatch.setattr(s, 'comparison', altered)
    if kind == 'material':
        assert s.run()['status'] == 'blocked'
    else:
        with pytest.raises(ValueError, match='one correction'):
            s.run()
    assert not read(s.path, {}).get('review_reconciliations') or kind == 'material'


@pytest.mark.parametrize('field', ['worker_instructions', 'completion_criteria'])
def test_operational_prose_is_independently_assessed(scenario, field):
    s = scenario
    s.refresh(change=lambda value: value.update({field: value[field] + ' Refresh retained evidence after cleanup.'}))
    assert s.run()['status'] == 'reconciled'
    saved = read(Path(read(s.path, {})['review_reconciliations'][-1]['path']), {})
    assert '/' + field in saved['comparison']['fields']


@pytest.mark.parametrize('restatement', [False, True])
def test_decisions_backed_choice_allows_only_validated_context(tmp_path, monkeypatch, restatement):
    s = Scenario(tmp_path, monkeypatch, decisions_choices=True)
    if restatement:
        restatement_refresh(s)
    else:
        s.refresh()
    assert s.run()['status'] == 'reconciled'
    assert state.verify_review(s.path, s.current)['head'] == s.reviewed_head


def setup_precheck(s, monkeypatch, *, no_op=False, discussion=False):
    from shared import cleanup_precheck
    p = s.package
    if discussion:
        restatement_refresh(s, published=False)
    else:
        s.refresh(published=False)
    isolated = p.root / 'isolated'
    s.git('clone', '-q', str(p.repo), str(isolated))
    p.context['checkout'] = str(isolated)
    p.manifest['evidence']['production'] = [str(isolated / 'transform.py')]
    p.write('context.json', p.context)
    s.prepare(analysis(s.reviewers))
    remote = p.root / 'remote.git'
    s.git('init', '--bare', '-q', str(remote))
    s.git('remote', 'add', 'origin', str(remote))
    branch = s.git('symbolic-ref', '--short', 'HEAD')
    s.git('push', '-qu', 'origin', branch)
    if not no_op:
        s.git('commit', '--amend', '-qm', 'Coherent cleanup')
    candidate = s.git('rev-parse', 'HEAD')
    recovery = 'refs/pr-split-recovery/' + s.reviewed_head
    s.git('update-ref', recovery, s.reviewed_head)
    guard_path = p.repo / '.git/pr-split-state.json'
    guard = {'branch': branch, 'remote': 'origin', 'remote_branch': branch,
        'remote_sha': s.reviewed_head, 'original_head': s.reviewed_head,
        'original_tree': s.git('rev-parse', 'HEAD^{tree}'), 'base': s.base,
        'pr': 1, 'pr_url': p.context['url'], 'host': 'github.com',
        'base_repo': 'example/fixture', 'head_repo': 'example/fixture',
        'author': 'fixture@example.test', 'mode': 'regroup', 'target_tip': s.base,
        'target_branch': 'main', 'recovery_ref': recovery}
    write(guard_path, guard)
    def live(*args):
        published = s.git('ls-remote', '--heads', 'origin', branch).split()[0]
        return dict(pr_url=p.context['url'], head_repo='example/fixture', final_head=published,
                    final_tree=s.git('rev-parse', published + '^{tree}'), target_tip=s.base,
                    target_branch='main', commit_count=1)
    monkeypatch.setattr(reconcile.series, 'live_snapshot', live)
    def run():
        return cleanup_precheck.precheck(s.path, s.reviewed_head, candidate, guard_path, state.reconciliation_api())
    return run, candidate, guard_path, guard


@pytest.mark.parametrize('discussion', [False, True])
def test_operational_preparation_refresh_preserves_cleanup_contract(scenario, monkeypatch, discussion):
    s = scenario
    run, _, _, _ = setup_precheck(s, monkeypatch, discussion=discussion)
    parent = s.current
    refreshed = s.prepare()
    assert refreshed['refresh']['model_call_count'] == 0
    assert s.current['analysis'] == parent['analysis']
    assert s.current['graph'] == parent['graph']
    assert s.current['parent']['preparation_id'] == parent['preparation_id']
    assert run()['status'] == 'prechecked' and s.calls == [0]
    assert len(read(s.path, {})['cycles']) == 5
    assert Path(s.reference['path']).read_bytes() == s.original_bytes


def retain_legacy_review(s, change=None):
    record = read(s.path, {})
    # The original runner registered only this reference and these v1 fields.
    artifact = {key: value for key, value in coverage.load(s.reference).items()
                if key not in {'session_id', 'session_identity'}}
    artifact.update(version=1, iteration_run_id=record['iteration_run']['id'], cycle=5)
    if change:
        change(artifact)
    path = s.package.write('legacy-review.json', artifact)
    s.reference = {'path': str(path), 'sha256': coverage.digest(artifact)}
    record['review_results'], record['review_sessions'] = [s.reference], []
    write(s.path, record)
    s.original_bytes = path.read_bytes()


@pytest.mark.parametrize('kind', ['scope', 'discussion', 'tree', 'source', 'run', 'cycle',
                                  'reviewer', 'finding', 'retained'])
def test_legacy_precheck_keeps_original_guards(scenario, monkeypatch, kind):
    s = scenario
    retain_legacy_review(s)
    run, _, _, _ = setup_precheck(s, monkeypatch, discussion=kind == 'discussion')
    if kind == 'scope':
        changed = analysis(s.reviewers)
        changed['worker_instructions'] += ' Changed operational wording.'
        s.prepare(changed)
    elif kind == 'source':
        s.package.write('user.json', {'instruction': 'Changed human choice'})
    elif kind == 'retained':
        snapshot = Path(s.original_path).parent / 'evidence/feedback/0'
        snapshot.chmod(0o600)
        snapshot.write_text('changed')
    elif kind != 'discussion':
        def change(artifact):
            if kind == 'tree': artifact['tree'] = 'a' * 40
            if kind == 'run': artifact['iteration_run_id'] = 'different-run'
            if kind == 'cycle': artifact['cycle'] = 4
            if kind == 'reviewer': artifact['result']['executed_reviewers'] = []
            if kind == 'finding':
                artifact['result']['findings'] = [{'path': 'transform.py', 'line': 1,
                                                  'message': 'Unassessed defect', 'reviewers': s.reviewers}]
        retain_legacy_review(s, change)
    if kind in {'scope', 'discussion', 'tree', 'reviewer', 'finding'}:
        result = run()
        assert result['status'] == 'blocked'
        assert 'unchanged review scope' in result['reason']
    else:
        reason = {'source': 'source changed', 'run': 'iteration run/cycle', 'cycle': 'iteration run/cycle',
                  'reviewer': 'execution is incomplete', 'finding': 'lacks validity and scope assessment',
                  'retained': 'retained preparation evidence changed'}[kind]
        with pytest.raises(ValueError, match=reason):
            run()
    assert not read(s.path, {}).get('cleanup_prechecks') and s.calls == []
    assert len(read(s.path, {})['cycles']) == 5


@pytest.mark.parametrize('missing_object', [False, True])
def test_precheck_uses_fresh_checkout_after_historical_checkout_removal(tmp_path, monkeypatch, missing_object):
    original_prepare = Package.prepare
    def prepare(package, *args, **kwargs):
        if not hasattr(package, 'historical_checkout'):
            package.historical_checkout = package.root / 'historical-review-checkout'
            coverage.git(package.repo, 'clone', '-q', str(package.repo), str(package.historical_checkout))
            package.context['checkout'] = str(package.historical_checkout)
            package.manifest['evidence']['production'] = [str(package.historical_checkout / 'transform.py')]
            package.write('manifest.json', package.manifest)
            package.write('context.json', package.context)
        return original_prepare(package, *args, **kwargs)
    monkeypatch.setattr(Package, 'prepare', prepare)
    s = Scenario(tmp_path, monkeypatch)
    run, candidate, path, _ = setup_precheck(s, monkeypatch)
    shutil.rmtree(s.package.historical_checkout)
    if missing_object:
        checkout = Path(prep.context_for(s.current)['checkout'])
        (checkout / '.git/objects' / s.reviewed_head[:2] / s.reviewed_head[2:]).unlink()
        with pytest.raises(subprocess.CalledProcessError):
            run()
        assert s.calls == []
    else:
        from shared import cleanup_precheck
        receipt = run()['precheck']
        assert cleanup_precheck.validate(receipt['path'], path, candidate,
                                        state.reconciliation_api()) == receipt
        assert run()['reused_precheck'] is True and s.calls == [0]
    assert Path(s.reference['path']).read_bytes() == s.original_bytes
    assert all(path.read_bytes() == before for path, before in s.retained_bytes.items())


@pytest.mark.parametrize('no_op', [False, True])
def test_precheck_retains_evidence_but_cannot_supply_completion(scenario, monkeypatch, no_op):
    from shared import cleanup_precheck
    s = scenario
    run, candidate, path, guard = setup_precheck(s, monkeypatch, no_op=no_op, discussion=True)
    receipt = run()
    assert receipt['status'] == 'prechecked' and receipt['completion_ready'] is False
    assert receipt['executed_reviewers'] == [] and s.calls == [0]
    assert cleanup_precheck.validate(receipt['precheck']['path'], path, candidate,
                                     state.reconciliation_api()) == receipt['precheck']
    with pytest.raises(ValueError, match='scope lacks review coverage'):
        state.verify_review(s.path, s.current)
    assert not read(s.path, {}).get('review_reconciliations')
    assert len(read(s.path, {})['cycles']) == 5
    assert Path(s.reference['path']).read_bytes() == s.original_bytes
    assert run()['status'] == 'prechecked' and s.calls == [0]


@pytest.mark.parametrize('failure', ['candidate', 'recovery', 'identity', 'guard',
    'remote', 'remote_branch', 'recovery_ref', 'remote_blank', 'missing_remote',
    'missing_remote_branch', 'missing_target_branch', 'missing_head_repo'])
def test_precheck_cli_reports_expected_errors_without_mutating_evidence(scenario, monkeypatch, capsys, failure):
    s = scenario
    _, candidate, guard_path, guard = setup_precheck(s, monkeypatch)
    if failure == 'candidate':
        candidate = 'f' * 40
    elif failure == 'recovery':
        s.git('update-ref', '-d', guard['recovery_ref'])
    elif failure == 'guard':
        guard_path.write_text('[]')
    elif failure.startswith('missing_'):
        guard.pop(failure.removeprefix('missing_'))
        write(guard_path, guard)
    elif failure in {'remote', 'remote_branch', 'recovery_ref', 'remote_blank'}:
        field, value = {'remote': ('remote', []), 'remote_branch': ('remote_branch', None),
                        'recovery_ref': ('recovery_ref', 42), 'remote_blank': ('remote', '')}[failure]
        guard[field] = value
        write(guard_path, guard)
    else:
        def unavailable(*args):
            raise RuntimeError('Live repository identity unavailable')
        monkeypatch.setattr(reconcile.series, 'live_snapshot', unavailable)
    destination = s.path.with_name('pr-1.json')
    destination.write_bytes(s.path.read_bytes())
    original = destination.read_bytes()
    guard_bytes = guard_path.read_bytes()
    monkeypatch.setattr(state, 'root', lambda repo: destination.parent)
    monkeypatch.setattr(sys, 'argv', ['run_state.py', '--repo', 'github.com/example/fixture', '--pr', '1',
        'precheck-cleanup', '--head', s.reviewed_head, '--candidate-head', candidate,
        '--split-state', str(guard_path)])
    with pytest.raises(SystemExit) as stopped:
        state.main()
    assert stopped.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == '' and captured.err.startswith('run_state: ')
    assert 'Traceback' not in captured.err
    assert destination.read_bytes() == original and s.calls == []
    assert guard_path.read_bytes() == guard_bytes
    assert all(path.read_bytes() == before for path, before in s.retained_bytes.items())


@pytest.mark.parametrize('malformed', [[], {'binding': []}, {'binding': {'assessment': []}}])
def test_malformed_precheck_containers_fail_through_real_gate_and_cli(scenario, monkeypatch, capsys, malformed):
    from shared import cleanup_precheck, ci_policy
    s = scenario
    run, candidate, guard_path, guard = setup_precheck(s, monkeypatch)
    receipt = run()['precheck']
    Path(receipt['path']).write_text(json.dumps(malformed))
    record_bytes = s.path.read_bytes()
    calls = list(s.calls)
    verification = s.verification()
    assert completion.completion_gate(s.reviewed_head, s.reviewed_head, s.reviewed_head, True, False, 'success',
        stage='review', preparation=s.current_path, pr_url=s.package.context['url'], body='',
        run_record=s.path, verification=verification, cleanup_precheck=receipt['path']) == 'review_coverage_invalid'
    for check in (lambda: cleanup_precheck.validate(receipt['path'], guard_path, candidate, state.reconciliation_api()),
                  lambda: cleanup_precheck.published_receipt({**guard, 'cleanup_precheck': receipt}, guard_path)):
        with pytest.raises(ValueError):
            check()
    ci = module('ci_wait')
    monkeypatch.setitem(sys.modules, 'ci_wait', ci)
    monkeypatch.setattr(ci, 'observe', lambda *a: {'head': s.reviewed_head, 'result': 'success'})
    monkeypatch.setattr(ci_policy, 'decide', lambda *a: {'head': s.reviewed_head, 'result': 'success'})
    monkeypatch.setattr(completion, 'live_preparation_scope', lambda *a: {'body': '', 'headRefOid': s.reviewed_head})
    monkeypatch.setattr(sys, 'argv', ['completion.py', '--cwd', str(s.package.repo), '--pr', s.package.context['url'],
        'gate', '--stage', 'review', '--start-head', s.reviewed_head, '--final-head', s.reviewed_head,
        '--review-complete', '--preparation-file', str(s.current_path), '--verification-file', str(verification),
        '--run-record', str(s.path), '--cleanup-precheck', receipt['path']])
    with pytest.raises(SystemExit) as stopped:
        completion.main()
    assert stopped.value.code == 1
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output['gate'] == 'review_coverage_invalid'
    assert not any(output[key] for key in ('review_ready', 'cleanup_ready', 'completion_ready'))
    assert captured.err == '' and s.calls == calls and s.path.read_bytes() == record_bytes
    assert all(path.read_bytes() == before for path, before in s.retained_bytes.items())


def test_nonobject_retained_comparison_has_normal_invalid_evidence_error(tmp_path):
    path = tmp_path / 'comparison.json'
    path.write_text('[]')
    item = {'path': str(path), 'sha256': coverage.digest([]), 'current_preparation_id': 'current'}
    with pytest.raises(ValueError):
        reconcile.load(item, {})
    with pytest.raises(ValueError):
        reconcile.retained_comparison({'cleanup_prechecks': [item]}, {'current_preparation_id': 'current'},
                                      collection='cleanup_prechecks')


@pytest.mark.parametrize('kind', ['guard', 'candidate', 'source', 'run', 'attempt', 'receipt', 'lease'])
def test_stale_precheck_never_authorizes_publication(scenario, monkeypatch, kind):
    from shared import cleanup_precheck
    s = scenario
    run, candidate, path, guard = setup_precheck(s, monkeypatch)
    receipt = run()['precheck']
    if kind == 'guard':
        guard['remote_branch'] = 'other'
        write(path, guard)
    if kind == 'candidate':
        s.git('commit', '--allow-empty', '-qm', 'Concurrent change')
    if kind == 'source':
        target = Path(s.current['sources']['feedback/0']['snapshot'])
        target.chmod(0o600)
        target.write_text('{}')
    if kind == 'run': state.mutate(s.path, {}, lambda r: r['iteration_run'].update(id='other'))
    if kind == 'attempt': state.mutate(s.path, {}, lambda r: r['cycle_retries'].append({'number': 5}))
    if kind == 'receipt': Path(receipt['path']).write_text('{}')
    if kind == 'lease': s.git('push', '-q', '--force', 'origin', 'HEAD:' + guard['remote_branch'])
    with pytest.raises((ValueError, KeyError)):
        cleanup_precheck.validate(receipt['path'], path, candidate, state.reconciliation_api())
    assert Path(s.reference['path']).read_bytes() == s.original_bytes


def test_precheck_disagreement_and_interruption_preserve_budget(scenario, monkeypatch):
    s = scenario
    run, _, _, _ = setup_precheck(s, monkeypatch)
    provider = executors.execute
    monkeypatch.setattr(executors, 'execute', lambda *a, **kw: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt): run()
    assert not read(s.path, {}).get('cleanup_prechecks')
    monkeypatch.setattr(executors, 'execute', provider)
    s.different = ('sources', 'feedback/0')
    assert run()['status'] == 'blocked'
    s.different = None
    assert run()['status'] == 'blocked' and s.calls == [0]
    assert len(read(s.path, {})['cycles']) == 5


@pytest.mark.parametrize('verdict', ['different', 'ambiguous', 'equivalent'])
def test_precheck_same_tree_candidate_preserves_disagreement_and_exact_approval(scenario, monkeypatch, verdict):
    from shared import cleanup_precheck
    s = scenario
    run, candidate, path, _ = setup_precheck(s, monkeypatch)
    original_comparison = s.comparison
    answer = [verdict]
    def comparison(binding):
        result = original_comparison(binding)
        if answer[0] != 'equivalent':
            result.update(equivalent=False, rationale='Retained engineering judgment: ' + answer[0])
            result['sources']['feedback/0']['verdict'] = answer[0]
        return result
    s.comparison = comparison
    expected = 'prechecked' if verdict == 'equivalent' else 'blocked'
    assert run()['status'] == expected
    original_receipt = read(s.path, {})['cleanup_prechecks'][-1]
    original_receipt_bytes = Path(original_receipt['path']).read_bytes()
    assert run()['status'] == expected and s.calls == [0]
    s.git('commit', '--amend', '-qm', 'Another same-tree cleanup candidate')
    changed = s.git('rev-parse', 'HEAD')
    assert changed != candidate
    assert s.git('rev-parse', changed + '^{tree}') == s.git('rev-parse', candidate + '^{tree}')
    answer[0] = 'equivalent'
    result = cleanup_precheck.precheck(s.path, s.reviewed_head, changed, path, state.reconciliation_api())
    if verdict == 'equivalent':
        assert result['status'] == 'prechecked' and result['reused_precheck'] is False
        assert s.calls == [0, 0]
        with pytest.raises(ValueError, match='does not cover this guard and candidate'):
            cleanup_precheck.validate(original_receipt['path'], path, changed, state.reconciliation_api())
        assert cleanup_precheck.validate(result['precheck']['path'], path, changed,
                                        state.reconciliation_api()) == result['precheck']
    else:
        assert result['status'] == 'blocked' and s.calls == [0]
        assert result['comparison'] == original_receipt
        assert read(s.path, {})['cleanup_prechecks'] == [original_receipt]
    assert Path(original_receipt['path']).read_bytes() == original_receipt_bytes
    assert Path(s.reference['path']).read_bytes() == s.original_bytes
    assert all(path.read_bytes() == before for path, before in s.retained_bytes.items())
    assert len(read(s.path, {})['cycles']) == 5


@pytest.mark.parametrize('verdict', ['different', 'ambiguous'])
def test_version_one_disagreement_survives_attestation_upgrade(scenario, verdict):
    s = scenario
    s.refresh()
    binding, _, _, _ = reconcile.snapshot(read(s.path, {}), s.head, s.assessment_path,
                                         state.reconciliation_api(), version=1)
    comparison = s.comparison(binding)
    comparison['equivalent'] = False
    comparison['rationale'] = 'Retained legacy disagreement'
    comparison['sources']['feedback/0']['verdict'] = verdict
    value = {'reconciliation_version': 1, 'binding': binding, 'comparison': comparison}
    path = s.package.write('legacy-disagreement.json', value)
    item = {'path': str(path), 'sha256': coverage.digest(value), 'binding_hash': coverage.digest(binding),
            'current_preparation_id': s.current['preparation_id']}
    reconcile.load(item, binding, require_equivalent=False)
    state.mutate(s.path, {}, lambda r: r.setdefault('review_reconciliations', []).append(item))
    assert s.run()['status'] == 'blocked' and s.calls == []

    # A later valid v2 approval for the same evidence cannot supply coverage.
    upgraded, _, _, _ = reconcile.snapshot(read(s.path, {}), s.head, s.assessment_path,
                                          state.reconciliation_api())
    approval = {'reconciliation_version': 2, 'binding': upgraded, 'comparison': s.comparison(upgraded)}
    path = s.package.write('later-approval.json', approval)
    later = {'path': str(path), 'sha256': coverage.digest(approval), 'binding_hash': coverage.digest(upgraded),
             'current_preparation_id': s.current['preparation_id']}
    reconcile.load(later, upgraded)
    state.mutate(s.path, {}, lambda r: r['review_reconciliations'].append(later))
    assert s.run()['status'] == 'blocked' and s.calls == []
    with pytest.raises(ValueError, match='not semantically equivalent'):
        state.verify_review(s.path, s.current)
    assert len(read(s.path, {})['cycles']) == 5
    assert Path(s.reference['path']).read_bytes() == s.original_bytes


def test_version_one_attestations_keep_original_restrictions(scenario):
    s = scenario
    s.refresh()
    binding, _, _, _ = reconcile.snapshot(read(s.path, {}), s.head, s.assessment_path,
                                         state.reconciliation_api(), version=1)
    # A legacy comparison format does not waive fresh architecture coverage.
    value = {'reconciliation_version': 1, 'binding': binding, 'comparison': s.comparison(binding),
             'architecture_review': architecture_review(s.current, s.package.context, s.package.decisions)}
    path = s.package.write('legacy-reconciliation.json', value)
    item = {'path': str(path), 'sha256': coverage.digest(value), 'binding_hash': coverage.digest(binding),
            'current_preparation_id': s.current['preparation_id']}
    state.mutate(s.path, {}, lambda r: r.setdefault('review_reconciliations', []).append(item))
    assert state.verify_review(s.path, s.current)['head'] == s.reviewed_head
    receipt = s.run()
    assert receipt['status'] == 'reconciled' and not receipt['reused_reconciliation']
    assert s.calls == [0]
    assert read(Path(receipt['reconciliation']['path']), {})['reconciliation_version'] == 2
    restatement_refresh(s, published=False)
    with pytest.raises(reconcile.NotEquivalent, match='Decisions'):
        reconcile.snapshot(read(s.path, {}), s.head, s.assessment_path, state.reconciliation_api(), version=1)


@pytest.mark.parametrize('no_op', [False, True])
@pytest.mark.parametrize('legacy', [False, True])
def test_reply_precheck_guarded_cleanup_and_final_completion(scenario, monkeypatch, no_op, legacy):
    from shared.review_loop import encode
    s = scenario
    if legacy:
        retain_legacy_review(s)
    run, candidate, path, data = setup_precheck(s, monkeypatch, no_op=no_op, discussion=not legacy)
    if legacy:
        # A synthetic v1 result cannot cover refreshed v3 engineering inputs.
        blocked = run()
        assert blocked['status'] == 'blocked' and 'unchanged review scope' in blocked['reason']
        assert len(read(s.path, {})['cycles']) == 5
        return
    receipt = run()['precheck']
    saved = read(Path(receipt['path']), {})
    with pytest.raises(ValueError, match='cannot supply final review coverage'):
        coverage.verify(s.reference, s.current, reconciliation={
            'artifact': receipt, 'binding': saved['binding']})
    assert completion.completion_gate(s.reviewed_head, s.reviewed_head, s.reviewed_head, True, False, 'success',
        stage='review', preparation=s.current_path, pr_url=s.package.context['url'], body='', run_record=s.path,
        verification=s.verification(), cleanup_precheck=receipt['path']) == 'clean'
    with pytest.raises(ValueError, match='cannot authorize completion'):
        completion.completion_gate(s.reviewed_head, candidate, candidate, True, False, 'success',
                                   cleanup_precheck=receipt['path'])
    spec = importlib.util.spec_from_file_location('end_to_end_guard', ROOT / 'dk-pr-split-commits/scripts/split_guard.py')
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    monkeypatch.chdir(s.package.repo)
    real_run = guard.run
    def command(*args, **kwargs):
        if args[0] == 'gh':
            return json.dumps(dict(number=1, url=s.package.context['url'], headRefName=data['remote_branch'],
                headRefOid=s.git('ls-remote', '--heads', 'origin', data['remote_branch']).split()[0],
                isCrossRepository=False, headRepository={'nameWithOwner': data['head_repo']}))
        return real_run(*args, **kwargs)
    monkeypatch.setattr(guard, 'run', command)
    token = encode('split', 1, True)
    def invoke(*args):
        monkeypatch.setattr(sys, 'argv', ['split_guard.py', *args])
        guard.main()
    reasons = s.package.write('reasons.json', ['One coherent reviewed change'])
    final_assessment = s.package.root / 'published-assessment.json'
    if not no_op:
        with pytest.raises(ValueError, match='cleanup-precheck'):
            invoke('push', '--allow-force-push', '--review-state', token)
        assert s.git('ls-remote', '--heads', 'origin', data['remote_branch']).split()[0] == s.reviewed_head
        invoke('push', '--allow-force-push', '--review-state', token, '--cleanup-precheck', receipt['path'])
    if no_op:
        with pytest.raises(ValueError, match='cleanup-precheck'):
            invoke('assess', '--review-state', token, '--reasons-file', str(reasons),
                   '--output', str(final_assessment))
        assert not final_assessment.exists()
    invoke('assess', '--review-state', token, '--cleanup-precheck', receipt['path'],
           '--reasons-file', str(reasons), '--output', str(final_assessment))
    isolated = s.package.context['checkout']
    coverage.git(isolated, 'fetch', '-q', str(s.package.repo), candidate)
    coverage.git(isolated, 'checkout', '-q', '--detach', candidate)
    # Final preparation must consume the same verified discussion, now at the
    # published candidate head. It receives a new comparison and verification.
    restatement_refresh(s, published=False)
    s.assessment_path = final_assessment
    s.assessment = reconcile.series.load(final_assessment, s.package.repo)
    assert s.run()['status'] == 'reconciled'
    assert completion.completion_gate(s.reviewed_head, candidate, candidate, True, False,
        {'head': candidate, 'result': 'success'}, preparation=s.current_path, pr_url=s.package.context['url'], body='', run_record=s.path,
        verification=s.verification(), assessment=s.assessment,
        series_snapshot=reconcile.series.live_snapshot(s.package.context['url'], isolated)) == 'clean'
    assert s.calls == [0, 0] and len(read(s.path, {})['cycles']) == 5
    assert Path(s.reference['path']).read_bytes() == s.original_bytes
    assert not path.exists() and s.git('rev-parse', data['recovery_ref']) == s.reviewed_head
    assert all(path.read_bytes() == before for path, before in s.retained_bytes.items())


def test_choice_envelope_is_not_an_exact_decisions_copy(tmp_path, monkeypatch):
    s = Scenario(tmp_path, monkeypatch, decisions_choices=True)
    p = s.package
    s.refresh()
    p.write('choice-envelope.json', {'data': p.decisions, 'unverified_extension': 'New obligation'})
    p.manifest['evidence']['choices'] = ['choice-envelope.json']
    p.write('manifest.json', p.manifest)
    s.prepare(analysis())
    assert s.run()['status'] == 'blocked' and s.calls == []
