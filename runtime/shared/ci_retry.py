"""Bind native infrastructure diagnoses to current PR changes and failed attempts.

This helper never decides semantic unrelatedness or sends a retry. The native
agent inspects the referenced logs and PR diff; publication retains its decision.
"""
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

from shared.ci_policy import fingerprint
from shared.flaky_actions import MAX_REPORT_BYTES, check_job
from shared.flaky_tests import digest


def retained(path, binary=False):
    path = Path(path)
    data = path.read_bytes()
    value = data if binary else json.loads(data)
    actual = hashlib.sha256(data).hexdigest() if binary else digest(value)
    if actual != path.stem:
        raise ValueError('CI evidence is missing or corrupt; collect current failure evidence again')
    return value


def changes(api, pr, head):
    """Capture complete PR changes without retaining another copy."""
    number = pr.rstrip('/').rsplit('/', 1)[-1]
    pull = api.request('pulls/' + number)
    if pull.get('head', {}).get('sha') != head:
        raise ValueError('PR head changed before retry diagnosis')
    files = []
    page = 1
    while True:
        batch = api.request(f'pulls/{number}/files?per_page=100&page={page}')
        if not isinstance(batch, list):
            raise ValueError('PR changes unavailable')
        files.extend(batch)
        if len(batch) < 100:
            break
        if len(files) >= 3000:
            raise ValueError('PR changes exceed complete files API coverage; diagnose manually')
        page += 1
    if len(files) != pull.get('changed_files') or len({f['filename'] for f in files}) != len(files):
        raise ValueError('PR changes pagination incomplete or changed')
    return pull, files


def revisions(pull):
    """Keep source/target identity without unrelated repository activity."""
    result = {}
    for name in ('head', 'base'):
        ref = pull[name]
        result[name] = {key: ref[key] for key in ('sha', 'ref') if key in ref}
        repo = {key: ref['repo'][key] for key in ('id', 'full_name')
                if ref.get('repo') and key in ref['repo']}
        if repo:
            result[name]['repo'] = repo
    return result


def run_inputs(run):
    """Bind retained source contents without unrelated GitHub profile metadata."""
    source = retained(run['source_file'])
    metadata = dict(source['run'])
    for name in ('repository', 'head_repository', 'actor', 'triggering_actor'):
        value = metadata.get(name)
        if value:
            keys = ('id', 'full_name') if name.endswith('repository') else ('id', 'login')
            metadata[name] = {key: value[key] for key in keys if key in value}
    return {key: value for key, value in run.items() if key not in {'capture', 'source_file'}} | {
        'source': source | {'run': metadata}}


def run_capture(api, pr, head, pull, files, snapshot, run):
    checks = []
    for check in snapshot['checks']:
        try:
            run_id, _ = check_job(api.repo, check.get('link'))
        except ValueError:
            continue
        if run_id == run['run_id']:
            checks.append(check)
    return digest({'pr': pr, 'head': head, 'revisions': revisions(pull), 'changes': files,
                   'checks': fingerprint({'head': head, 'checks': checks}),
                   'run': run_inputs(run)})


def capture(api, pr, head, decision, snapshot):
    """Read current boundaries, returning a transient native diagnosis handoff."""
    pr = pr.rstrip('/').lower()
    if (decision.get('result') != 'failure' or decision.get('head') != head
            or snapshot.get('result') != 'failure' or snapshot.get('head') != head
            or fingerprint(decision) != fingerprint(snapshot)):
        raise ValueError('CI failure snapshot changed; observe current CI before diagnosis')
    evidence = retained(decision['evidence_file'])
    if evidence.get('head') != head or evidence.get('repo') != api.repo:
        raise ValueError('CI evidence repository or head differs')
    pull, files = changes(api, pr, head)
    number = pr.rstrip('/').rsplit('/', 1)[-1]
    failures = [c for c in snapshot['checks'] if c.get('bucket') in {'fail', 'cancel'}]
    runs = {}
    blockers = []
    for check in failures:
        matching = [v for v in evidence['checks'] if v.get('link') == check.get('link')]
        if len(matching) != 1 or matching[0].get('error'):
            blockers.append({'link': check.get('link'), 'reason': 'Failed check lacks complete attempt provenance'})
            continue
        item = matching[0]
        try:
            run_id, job_id = check_job(api.repo, check.get('link'))
            for field, label in (('source_file', 'source evidence is'), ('archive_file', 'logs are')):
                if not isinstance(item.get(field), str) or not item[field]:
                    raise ValueError(f'Failed workflow {label} unavailable; collect current failure evidence again')
            source = retained(item['source_file'])
            archive = retained(item['archive_file'], binary=True)
            run = source['run']
            latest = api.request(f'actions/runs/{run_id}')
            if (run['id'] != run_id or run['run_attempt'] != item['attempt']
                    or run['head_sha'] != head or latest['head_sha'] != head
                    or latest['run_attempt'] != item['attempt'] or latest['status'] != 'completed'
                    or latest['conclusion'] not in {'failure', 'timed_out'}
                    or not any(j['id'] == job_id for j in source['jobs'])):
                raise ValueError('Failed workflow attempt changed')
            failed_jobs = [j for j in source['jobs'] if j.get('conclusion') in {'failure', 'timed_out'}]
            if not failed_jobs or any(j.get('status') != 'completed' for j in source['jobs']):
                raise ValueError('Failed workflow jobs are incomplete')
            if len({j['id'] for j in source['jobs']}) != len(source['jobs']):
                raise ValueError('Failed workflow job identities are ambiguous')
            directories = [re.sub(r'[<>:"/\\|?*]', '_', j['name']) for j in source['jobs']]
            if any(directories.count(re.sub(r'[<>:"/\\|?*]', '_', j['name'])) != 1 for j in failed_jobs):
                raise ValueError('Failed workflow log job directories are ambiguous')
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                members = bundle.namelist()
            captured_run = {'run_id': run_id, 'attempt': item['attempt'],
                'workflow': run.get('path'), 'source_file': item['source_file'],
                'archive_file': item['archive_file'], 'log_members': members,
                'jobs': [{'job_id': j['id'], 'job': j['name'],
                    'failed_steps': [s['name'] for s in j.get('steps', [])
                                     if s.get('conclusion') in {'failure', 'timed_out'}]}
                    for j in failed_jobs]}
            captured_run['capture'] = run_capture(api, pr, head, pull, files, snapshot, captured_run)
            runs[run_id] = captured_run
        except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile) as exc:
            blockers.append({'link': check.get('link'), 'reason': str(exc)})
    latest_pull = api.request('pulls/' + number)
    if (revisions(latest_pull) != revisions(pull)
            or latest_pull.get('changed_files') != pull.get('changed_files')):
        raise ValueError('PR changes moved during retry diagnosis')
    result = {'pr': pr, 'head': head, 'base': pull['base']['sha'],
        'changes': [{k: f[k] for k in ('filename', 'status', 'additions', 'deletions') if k in f}
                    for f in files], 'diff_command': ['gh', 'pr', 'diff', pr],
        'runs': sorted(runs.values(), key=lambda r: r['run_id']), 'blockers': blockers}
    result['capture'] = digest({'pr': pr, 'head': head, 'revisions': revisions(pull),
        'changes': files, 'checks': fingerprint(snapshot),
        'runs': [run['capture'] for run in result['runs']], 'blockers': blockers})
    return result


def validate(request, api, snapshot):
    """Revalidate this workflow's diagnosis immediately before an unsent retry."""
    diagnosis = request['diagnosis']
    source = retained(diagnosis['source_file'])
    retained(diagnosis['archive_file'], binary=True)
    run_id, attempt = request['run_id'], request['attempt']
    jobs = source['jobs']
    run = {'run_id': run_id, 'attempt': attempt, 'workflow': source['run'].get('path'),
           'source_file': diagnosis['source_file'], 'archive_file': diagnosis['archive_file'],
           'jobs': [{'job_id': j['id'], 'job': j['name'],
                     'failed_steps': [s['name'] for s in j.get('steps', [])
                                      if s.get('conclusion') in {'failure', 'timed_out'}]}
                    for j in jobs if j.get('conclusion') in {'failure', 'timed_out'}]}
    with zipfile.ZipFile(diagnosis['archive_file']) as bundle:
        run['log_members'] = bundle.namelist()
    pull, files = changes(api, request['pr'], request['head'])
    if (snapshot.get('head') != request['head']
            or run_capture(api, request['pr'], request['head'], pull, files, snapshot, run)
               != diagnosis['capture']):
        raise ValueError('Retry diagnosis inputs changed; inspect current changes and logs')
    latest = api.request('pulls/' + request['pr'].rstrip('/').rsplit('/', 1)[-1])
    if (revisions(latest) != revisions(pull)
            or latest.get('changed_files') != pull.get('changed_files')):
        raise ValueError('PR changes moved during retry diagnosis validation')


def requests(handoff, diagnosis, *, automatic_limit=3):
    """Validate native decisions; ambiguous and PR-caused failures stay blocked."""
    if diagnosis.get('capture') != handoff['capture']:
        raise ValueError('Retry diagnosis inputs changed; inspect current changes and logs')
    supplied = diagnosis.get('runs', [])
    if not isinstance(supplied, list) or len({r['run_id'] for r in supplied}) != len(supplied):
        raise ValueError('Retry diagnoses must identify unique workflows')
    expected = {r['run_id']: r for r in handoff['runs']}
    if set(r['run_id'] for r in supplied) != set(expected):
        raise ValueError('Diagnose every failed workflow in the handoff')
    prepared, blockers = [], list(handoff['blockers'])
    for assessment in supplied:
        run = expected[assessment['run_id']]
        if assessment.get('attempt') != run['attempt']:
            raise ValueError('Retry diagnosis attempt changed')
        jobs = assessment.get('jobs', [])
        if (not isinstance(jobs, list) or len({j['job_id'] for j in jobs}) != len(jobs)
                or {j['job_id'] for j in jobs} != {j['job_id'] for j in run['jobs']}):
            raise ValueError('Diagnose every failed job, including jobs without a displayed check')
        eligible = True
        with zipfile.ZipFile(run['archive_file']) as bundle:
            for job in jobs:
                source = next(j for j in run['jobs'] if j['job_id'] == job['job_id'])
                if (job.get('verdict') not in {'unrelated', 'caused_by_pr', 'ambiguous'}
                        or not isinstance(job.get('reason'), str) or not job['reason'].strip()
                        or not isinstance(job.get('change_comparison'), str) or not job['change_comparison'].strip()):
                    raise ValueError('Native diagnosis requires a verdict, failure reason and PR change comparison')
                if job['verdict'] != 'unrelated' or job.get('retryable') is not True:
                    eligible = False
                    blockers.append({'run_id': run['run_id'], 'job_id': job['job_id'],
                                     'reason': job['reason'], 'verdict': job['verdict']})
                    continue
                refs = job.get('log_evidence', [])
                if not isinstance(refs, list) or not refs:
                    raise ValueError('Unrelated failure requires exact failed-job log evidence')
                safe = re.sub(r'[<>:"/\\|?*]', '_', source['job']) + '/'
                for ref in refs:
                    member, excerpt = ref.get('member'), ref.get('excerpt')
                    if (not isinstance(member, str) or not member.startswith(safe)
                            or not isinstance(excerpt, str) or not excerpt.strip()):
                        raise ValueError('Log citation must identify this failed job and a nonempty excerpt')
                    info = bundle.getinfo(member)
                    if info.file_size > MAX_REPORT_BYTES or excerpt not in bundle.read(info).decode('utf-8-sig'):
                        raise ValueError('Native log citation does not match retained failed-job evidence')
        if eligible:
            prepared.append({'kind': 'ci-rerun', 'pr': handoff['pr'], 'head': handoff['head'],
                'run_id': run['run_id'], 'attempt': run['attempt'],
                'reason': '; '.join(j['reason'] for j in jobs), 'automatic_limit': automatic_limit,
                'diagnosis': {'capture': run['capture'], 'jobs': jobs,
                              'source_file': run['source_file'], 'archive_file': run['archive_file']}})
    return {'requests': prepared, 'blockers': blockers}
