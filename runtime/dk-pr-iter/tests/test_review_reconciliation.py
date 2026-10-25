"""Cleanup coverage with real Git, retained preparations and bounded sessions."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from shared import executors, review_coverage as coverage, review_reconciliation as reconcile, review_sessions
from shared.command_results import classification
from shared.private_state import read, write

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'dk-pr-decisions/tests'))
from preparation_fixtures import Package, analysis, descriptor, prep


def module(name):
    spec = importlib.util.spec_from_file_location('reconciliation_' + name,
        ROOT / 'dk-pr-iter/scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


state, completion = module('run_state'), module('completion')


class Scenario:
    def __init__(self, tmp_path, monkeypatch, reviewers=('example',), cycles=5):
        self.package = p = Package(tmp_path)
        self.reviewers = list(reviewers)
        if len(reviewers) > 1:
            p.manifest['evidence']['reviewers'] = [str(p.write(name + '-reviewer.json',
                descriptor(name, p.repo / 'transform.py'))) for name in reviewers]
        self.git = lambda *args: coverage.git(p.repo, *args)
        self.base = p.head
        (p.repo / 'change.txt').write_text('Reviewed change\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'Reviewed commit')
        self.reviewed_head = self.git('rev-parse', 'HEAD')
        p.context['head_sha'] = p.decisions['head_sha'] = self.reviewed_head
        p.write('context.json', p.context)
        p.write('decisions.json', p.decisions)
        p.manifest['mode'] = 'iteration'
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
                       'skipped_reviewers': [], 'findings': []}
        self.reference = self.session.finish(self.result)
        state.register_review(record, {'review_artifact': self.reference}, self.reviewed_head)
        write(self.path, record)
        self.original_bytes = Path(self.reference['path']).read_bytes()
        self.calls = []
        self.different = None
        self.provider_hook = None
        def provider(prompt, checkout, *, attempt=0):
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
                'sources': {key: verdict('sources', key) for key in binding['changed_sources']}}

    def refresh(self, *, published=True, change=None):
        p = self.package
        if published:
            self.git('commit', '--amend', '-qm', 'Clean series boundary')
        self.head = self.git('rev-parse', 'HEAD')
        p.context['head_sha'] = p.decisions['head_sha'] = self.head
        p.write('context.json', p.context)
        p.write('decisions.json', p.decisions)
        p.write('feedback.json', {'status': 'Fresh feedback after cleanup'})
        p.manifest['evidence']['feedback'] = ['feedback.json']
        p.manifest['evidence']['prior_preparation'] = [self.original_path]
        p.write('manifest.json', p.manifest)
        result = analysis(self.reviewers)
        result['sync_reason'] = 'Published scope and Decisions are synchronized after cleanup.'
        if change:
            change(result)
        self.current_path = p.prepare(result)['artifact']
        self.current = prep.read(self.current_path)
        state.mutate(self.path, {}, lambda record: state.attach_preparation(
            record, self.current_path, p.context['url'], self.head))
        self.assess_series(published=published)

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
            'verification_version': 1, 'preparation_id': prepared['preparation_id'],
            'head_sha': prepared['identity']['head_sha'], 'pr_url': prepared['identity']['pr_url'],
            'checks': {name: {'satisfied': True, 'rationale': 'Current transform and tests inspected',
                             'evidence': [prepared['sources']['production/0']]}
                       for name in ('behavior', 'placement', 'test_sufficiency', 'compatibility', 'complexity')}})


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    return Scenario(tmp_path, monkeypatch)


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
        p.write('new-rules.json', {'rule': 'Additional obligations'})
        p.manifest['evidence']['rules'] = ['new-rules.json']
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
        target = Path(s.current['sources']['feedback/0']['path'])
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
        else: target.write_text('{}')
    with pytest.raises((ValueError, OSError, KeyError)):
        state.verify_review(s.path, s.current)


@pytest.mark.parametrize('kind', ['head', 'source', 'assessment', 'run', 'target'])
def test_movement_during_comparison_registers_nothing(scenario, kind):
    s = scenario
    s.refresh()
    def move():
        if kind == 'head': s.git('commit', '--allow-empty', '-qm', 'Concurrent movement')
        if kind == 'source': s.package.write('feedback.json', {'new': 'obligation'})
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
    def provider(prompt, checkout, *, attempt=0):
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
    assert coverage.scope(s.original) == coverage.scope(s.current)
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
    assert coverage.scope(s.original) == coverage.scope(s.current)
    assert s.run()['status'] == 'blocked'
    with pytest.raises(ValueError):
        state.verify_review(s.path, s.current)
    assert s.calls == []


@pytest.mark.parametrize('negative_first', [True, False])
def test_concurrent_opposite_verdicts_remain_terminally_blocked(scenario, monkeypatch, negative_first):
    s = scenario
    s.refresh()
    calls = []
    def provider(prompt, checkout, *, attempt=0):
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
    again = s.package.prepare(s.current['analysis'])
    s.current_path = again['artifact']
    assert before != Path(s.current_path)
    assert before.read_bytes() == Path(s.current_path).read_bytes()
    assert again['preparation_id'] == s.current['preparation_id']
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
    (Path(s.current_path).parent / 'evidence/feedback/0').write_text('{}')
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
    reference = session.finish(s.result)
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


def replace_result(s, result):
    record = read(s.path, {})
    item = s.session.finish(result)
    state.register_review(record, {'review_artifact': item}, s.reviewed_head)
    write(s.path, record)


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
