import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/review_stats.py'
spec = importlib.util.spec_from_file_location('stats_test_api', SCRIPT)
stats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stats)
REPO = 'github.com/o/r'
SHA = 'a' * 40


@pytest.fixture(autouse=True)
def private_storage(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'private'))


def finding(message='bug', severity='Major', line=1):
    return dict(path='x.py', line=line, message=message, fix='restore behavior', severity=severity)


def run(reviewers=('a', 'b'), cycle=1, repo=REPO, head=SHA):
    return stats.begin_review(pr_url=f'https://{repo}/pull/2', cycle=cycle, head=head,
                              preparation_id='prep', reviewers=list(reviewers))


def complete(r, name, findings):
    return stats.record_review(r['repository'], r['id'], name, status='completed', findings=findings,
                               duration_seconds=1, correction_retries=0)


def assess(repo, review, canonical='defect', verdict='confirmed', severity='Minor',
           disposition='actionable', suffix=''):
    f = review['findings'][0]
    entry = dict(id=f['id'] + ':' + suffix + verdict, finding_id=f['id'], canonical_issue=canonical,
                 verdict=verdict, severity=severity, disposition=disposition,
                 evidence=['https://github.com/o/r/pull/2#discussion_r1'], reason='checked code')
    stats.record_assessments(repo, [entry])
    return entry


def test_severity_precision_and_scope_are_independent():
    r = run()
    a = complete(r, 'a', [finding()])
    b = complete(r, 'b', [finding('different', 'Nit')])
    assess(REPO, a, disposition='excluded')
    assess(REPO, b, canonical='not-a-defect', verdict='rejected', severity='Nit')
    total = stats.summary(REPO)['total']
    assert total['reported'] == dict(Major=1, Minor=0, Nit=1, total=2)
    assert total['confirmed']['Minor'] == 1
    assert total['excluded']['Minor'] == 1
    assert total['rejected']['Nit'] == 1
    assert total['precision']['total'] == dict(value=.5, sample_size=2)
    assert total['precision']['Major'] == dict(value=None, sample_size=0)
    assert stats.summary(REPO)['reviewers']['a']['severity_changes'] == {'Major->Minor': 1}


def test_shared_credit_dedup_and_repeated_cycles():
    r = run()
    a = complete(r, 'a', [finding(), finding()])
    b = complete(r, 'b', [finding('same defect, other wording')])
    assess(REPO, a)
    assess(REPO, b)
    r2 = run(cycle=2, head='b' * 40)
    a2 = complete(r2, 'a', [finding('moved bug', line=10)])
    complete(r2, 'b', [])
    assess(REPO, a2)
    result = stats.summary()
    assert result['total']['reported']['total'] == 3
    assert result['total']['confirmed_issues']['total'] == 1
    assert result['total']['shared']['total'] == 1
    assert all(v['confirmed_issues']['total'] == 1 for v in result['reviewers'].values())
    assert stats.summary(reviewer='a')['total']['shared']['total'] == 1


def test_exclusive_needs_comparable_completed_and_assessed_reviewers():
    r = run()
    a = complete(r, 'a', [finding()])
    assess(REPO, a)
    total = stats.summary()['total']
    assert total['exclusive']['total'] == 0
    assert total['exclusive_unknown']['total'] == 1
    assert stats.summary()['warnings']
    b = complete(r, 'b', [finding('uncertain')])
    assert stats.summary()['total']['exclusive_unknown']['total'] == 1
    assess(REPO, b, verdict='rejected', canonical='other')
    assert stats.summary()['total']['exclusive']['total'] == 1
    r2 = run(reviewers=('a',), cycle=2)
    a2 = complete(r2, 'a', [finding('single')])
    assess(REPO, a2, canonical='single')
    assert stats.summary()['total']['exclusive_unknown']['total'] == 1


def test_corrections_can_split_merge_and_reverse_verdict_without_losing_history():
    r = run()
    a = complete(r, 'a', [finding()])
    b = complete(r, 'b', [finding('worded differently')])
    original = assess(REPO, a)
    assess(REPO, b)
    assert stats.summary()['total']['confirmed_issues']['total'] == 1
    assess(REPO, b, canonical='second', severity='Nit', suffix='split')
    assert stats.summary()['total']['confirmed_issues']['total'] == 2
    assess(REPO, a, verdict='rejected', severity='Major', suffix='correction')
    stats.record_assessments(REPO, [original])  # Replay old ID cannot roll back correction.
    result = stats.summary()
    assert result['total']['confirmed_issues']['total'] == 1
    assert result['total']['precision']['total'] == dict(value=.5, sample_size=2)
    assert len(stats.details(REPO, r['id'])['assessments']) == 4
    assert result['reviewers']['a']['confirmed_issues']['total'] == 0


def test_unverified_is_not_rejected_and_missing_is_not_zero():
    assert stats.summary()['total'] is None
    r = run()
    a = complete(r, 'a', [finding()])
    stats.record_review(REPO, r['id'], 'b', status='exhausted', reason='usage limit')
    total = stats.summary()['total']
    assert total['precision']['total'] == dict(value=None, sample_size=0)
    assert total['unverified']['total'] == 1
    assert total['rejected']['total'] == 0
    assert total['reviews']['exhausted'] == 1
    assess(REPO, a, disposition='deferred')
    assert stats.summary()['total']['confirmed']['total'] == 1
    assert stats.summary()['total']['deferred']['total'] == 1


def test_idempotent_mutations_and_conflict_protection():
    r = run()
    a = complete(r, 'a', [finding()])
    assert run()['id'] == r['id']
    assert complete(r, 'a', [finding()]) == a
    entry = assess(REPO, a)
    assert stats.record_assessments(REPO, [entry]) == []
    with pytest.raises(ValueError, match='immutable'):
        complete(r, 'a', [])
    with pytest.raises(ValueError, match='ID conflict'):
        stats.record_assessments(REPO, [{**entry, 'severity': 'Nit'}])
    assert len(stats.ledger(REPO)['assessment_history']) == 1
    # Artifacts survive a failed ledger operation and can be replayed.
    assert list(stats.ledger_path(REPO).parent.glob('review-stats-artifacts/assessments/*.json'))


def test_concurrent_writes_cross_repo_reports_and_local_read_only_queries(tmp_path, monkeypatch):
    def write_one(n):
        r = run(reviewers=('a',), cycle=n + 1)
        a = complete(r, 'a', [finding(str(n))])
        assess(REPO, a, canonical=str(n))
        stats.render_reports()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write_one, range(12)))
    other = run(reviewers=('b',), repo='git.example/O/Other')
    complete(other, 'b', [])
    reports = stats.render_reports()
    assert stats.summary()['run_count'] == 13
    assert stats.summary()['total']['confirmed_issues']['total'] == 12
    assert stats.repositories() == ['git.example/o/other', REPO]
    user = Path(reports['user']).read_text()
    for path in reports['repositories'].values():
        assert Path(path).as_uri() in user
        assert Path(reports['user']).as_uri() in Path(path).read_text()
    report = stats.ledger_path(REPO).with_suffix('.md').read_text()
    assert 'https://github.com/o/r/pull/2' in report
    assert 'review-stats-artifacts' in report
    assert stats.ledger_path(REPO).stat().st_mode & 0o777 == 0o600
    before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in stats.stats_home().rglob('*') if p.is_file()}
    def forbidden(*args, **kwargs):
        raise AssertionError('query tried to mutate or run a process')
    monkeypatch.setattr(stats, 'mutate', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    stats.summary()
    stats.summary(REPO, reviewer='a')
    stats.details(REPO, next(iter(stats.ledger(REPO)['runs'])))
    after = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in stats.stats_home().rglob('*') if p.is_file()}
    assert before == after


def test_cli_json_queries_and_version_rejection():
    r = run(reviewers=('a',))
    complete(r, 'a', [])
    for args in (['summary'], ['summary', '--repo', REPO, '--reviewer', 'a'], ['repositories'],
                 ['details', '--repo', REPO, '--run', r['id']]):
        proc = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout)['api_version'] == 1
    proc = subprocess.run([sys.executable, str(SCRIPT), '--api-version', '99', 'summary'], capture_output=True)
    assert proc.returncode == 2


def test_existing_iteration_assessment_reuses_evidence_and_counters():
    spec = importlib.util.spec_from_file_location('stats_run_state', SCRIPT.with_name('run_state.py'))
    state = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(state)
    r = run(reviewers=('a',))
    a = complete(r, 'a', [finding()])
    record = {'cycles': [{'number': 1, 'head': SHA}], 'repairs': [], 'reruns': [],
              'assessments': [{'reference': 'ref', 'head': SHA, 'disposition': 'outside_scope',
                               'evidence': 'scope.json', 'reason': 'excluded by decision'}]}
    state.classify(record, 'ref', SHA, 'pre_existing', 'base.json', 'base has bug', 21)
    before = json.dumps(record, sort_keys=True)
    state.reviewer_assessments(record, REPO, [{'id': 'assessment', 'reference': 'ref', 'head': SHA,
        'finding_ids': [a['findings'][0]['id']], 'canonical_issue': 'defect', 'severity': 'Minor'}])
    assert before == json.dumps(record, sort_keys=True)
    total = stats.summary()['total']
    assert total['confirmed']['Minor'] == 1
    assert total['rejected']['total'] == 0
    assert total['excluded']['total'] == 1


@pytest.mark.parametrize('findings', [{}, '', [None]])
def test_malformed_data_cannot_look_like_a_clean_review(findings):
    r = run()
    with pytest.raises(ValueError):
        stats.record_review(REPO, r['id'], 'a', status='completed', findings=findings)
    assert stats.ledger(REPO)['runs'][r['id']]['reviews']['a']['status'] == 'not_run'


def test_same_defect_with_different_reported_severities_preserves_observations():
    r = run(reviewers=('a',))
    a = complete(r, 'a', [finding(), finding(severity='Minor'), finding()])
    assert len(a['findings']) == 2
    assert len({f['canonical_issue'] for f in a['findings']}) == 1
