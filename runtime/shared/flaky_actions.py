"""Read-only GitHub Actions evidence collection and bounded flake discovery."""
import hashlib
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

from shared.flaky_tests import digest, identity, normalize_repo, now, require_head
from shared.private_state import write, read
from shared.ci_deadline import ClassificationTimeout
from shared.test_evidence import parse_report, result
from shared.ci_exceptions import matching
from shared.github_reads import run as github_run

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_REPORT_BYTES = 10 * 1024 * 1024
ATTEMPT_READER = hashlib.sha256(Path(__file__).read_bytes() +
    Path(__file__).with_name('test_evidence.py').read_bytes()).hexdigest()


class GitHub:
    def __init__(self, repo):
        self.repo = normalize_repo(repo)
        self.host, self.name = self.repo.split('/', 1)

    def request(self, endpoint, binary=False):
        command = ['gh', 'api', '--hostname', self.host, f'repos/{self.name}/{endpoint}']
        try:
            completed = github_run(command, capture_output=True, timeout=60)
        except ClassificationTimeout:
            raise
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f'GitHub request unavailable: {endpoint}') from exc
        if completed.returncode:
            raise RuntimeError(f'GitHub request failed: {endpoint}: ' + completed.stderr.decode(errors='replace').strip())
        if binary:
            if len(completed.stdout) > MAX_ARCHIVE_BYTES:
                raise RuntimeError('log archive exceeds 100 MiB collection limit')
            return completed.stdout
        try:
            return json.loads(completed.stdout)
        except ValueError as exc:
            raise RuntimeError(f'invalid GitHub response: {endpoint}') from exc

    def issue(self, number):
        return self.request(f'issues/{number}')

    def jobs(self, run_id, attempt):
        jobs = []
        page = 1
        while True:
            batch = self.request(f'actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100&page={page}')
            jobs.extend(batch['jobs'])
            if len(jobs) >= batch['total_count']:
                break
            if not batch['jobs']:
                raise RuntimeError('incomplete attempt jobs pagination')
            page += 1
        if len({j['id'] for j in jobs}) != len(jobs):
            raise RuntimeError('duplicate jobs in attempt pagination')
        return jobs

    def open_issues(self, limit):
        # gh issue list uses the repository's Issue connection (excludes PRs).
        command = ['gh', 'issue', 'list', '--repo', self.repo, '--state', 'open',
                   '--search', 'sort:updated-desc', '--limit', str(limit),
                   '--json', 'number,url,title,body,updatedAt,state']
        completed = github_run(command, capture_output=True, text=True, timeout=60)
        if completed.returncode:
            raise RuntimeError(completed.stderr.strip() or 'open issue scan unavailable')
        issues = json.loads(completed.stdout)
        return [{'number': i['number'], 'html_url': i['url'], 'title': i['title'], 'body': i['body'],
                 'updated_at': i['updatedAt'], 'state': i['state'].lower()} for i in issues]


def evidence_file(registry, value, suffix='.json'):
    folder = registry.path.parent / 'flaky-evidence'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = digest(value) if suffix == '.json' else hashlib.sha256(value).hexdigest()
    path = folder / (key + suffix)
    if suffix == '.json':
        write(path, value)
    elif not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != key:
        # Unique content-addressed archive. Atomic publication avoids partial reads.
        import os
        import tempfile
        fd, name = tempfile.mkstemp(dir=folder)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(value)
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
    return str(path)


def archive_report(archive, job_name, step, job_names):
    """Bind one complete step log to a unique job and step number in the zip."""
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', job_name)
    if sum(re.sub(r'[<>:"/\\|?*]', '_', n) == safe_name for n in job_names) != 1:
        return result(reason='ambiguous log job directory')
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            prefix = f'{safe_name}/{step["number"]}_'
            members = [m for m in bundle.infolist() if m.filename.startswith(prefix) and m.filename.endswith('.txt')]
            if len(members) != 1 or members[0].file_size > MAX_REPORT_BYTES:
                return result(reason='missing/ambiguous step log or 10 MiB report limit')
            text = bundle.read(members[0]).decode('utf-8-sig', errors='strict')
            # Actions command preamble is not test output. Remove a single closed
            # group containing only the invocation/environment (not test results).
            lines = text.splitlines()
            from shared.test_evidence import clean
            if lines and clean(lines[0]).startswith('##[group]Run '):
                end = next((i for i, line in enumerate(lines) if clean(line) == '##[endgroup]'), None)
                if end is None:
                    return result(reason='incomplete Actions command preamble')
                invocation = '\n'.join(clean(line) for line in lines[:end + 1])
                # Compound commands can contain non-test failures hidden by a report.
                if any(token in invocation for token in ('&&', '||', ';')):
                    return result(reason='compound test step requires independent failure coverage')
                commands = []
                for line in lines[1:end]:
                    command = clean(line)
                    if command.startswith(('shell:', 'env:')):
                        break
                    if command:
                        commands.append(command)
                if (len(commands) != 1 or not re.match(
                        r'^(?:pytest\b|python[23]?(?:\.\d+)? -m pytest\b|go test\b|(?:npx )?jest\b)',
                        commands[0])):
                    return result(reason='unsupported step command (wrapper or aggregate gate): '
                                  'step command does not prove exclusive test execution')
                text = '\n'.join(lines[end + 1:])
            return parse_report(text) | {'archive_member': members[0].filename}
    except ClassificationTimeout:
        raise
    except (zipfile.BadZipFile, UnicodeError, KeyError, RuntimeError):
        return result(reason='unavailable or invalid attempt logs')


def collect_attempt(api, registry, run, attempt, pipeline_exceptions=(), *, reuse=False, targets=None):
    run_id = run['id']
    metadata = api.request(f'actions/runs/{run_id}/attempts/{attempt}')
    if (metadata.get('id') != run_id or metadata.get('run_attempt') != attempt
            or metadata.get('head_sha') != run['head_sha'] or metadata.get('path') != run.get('path')):
        raise RuntimeError('run attempt provenance mismatch')
    require_head(metadata['head_sha'])
    jobs_scope = sorted({t['job'] for t in targets if t['workflow'] == run.get('path')}) if targets else None
    cache_key = digest({'repo': registry.repo, 'head': run['head_sha'], 'run_id': run_id,
                        'attempt': attempt, 'workflow': run.get('path'),
                        'jobs': jobs_scope, 'reader': ATTEMPT_READER})
    cached = registry.load().get('attempt_evidence', {}).get(cache_key) if reuse else None
    if cached and metadata.get('status') == 'completed' and not pipeline_exceptions:
        try:
            value = read(Path(cached), None)
            if (value and digest(value) == Path(cached).stem and not value.get('missing')
                    and all(value.get(k) == v for k, v in {'repo': registry.repo,
                        'head': run['head_sha'], 'run_id': run_id, 'attempt': attempt,
                        'workflow': run.get('path')}.items())
                    and digest(read(Path(value['source_file']), None)) == Path(value['source_file']).stem
                    and hashlib.sha256(Path(value['archive_file']).read_bytes()).hexdigest()
                        == Path(value['archive_file']).stem):
                return value | {'cached': True}
        except (ValueError, OSError, KeyError, TypeError):
            pass  # Missing or corrupt retained evidence requires fresh collection.
    jobs = api.jobs(run_id, attempt)
    evidence = {'repo': registry.repo, 'head': metadata['head_sha'], 'run_id': run_id,
                'attempt': attempt, 'workflow': metadata['path'], 'jobs': [], 'missing': []}
    evidence['source_file'] = evidence_file(registry, {'run': metadata, 'jobs': jobs})
    exempt = {job['id'] for job in jobs if matching(pipeline_exceptions, 'pipeline',
                                                  {'workflow': metadata['path'], 'job': job['name']})}
    try:
        archive = b''
        if any(job['id'] not in exempt and (not targets or any(
                t['workflow'] == metadata['path'] and t['job'] == job['name'] for t in targets)) for job in jobs):
            archive = api.request(f'actions/runs/{run_id}/attempts/{attempt}/logs', binary=True)
            evidence['archive_file'] = evidence_file(registry, archive, '.zip')
    except ClassificationTimeout:
        raise
    except RuntimeError as exc:
        archive = b''
        evidence['missing'].append(str(exc))
    observations = []
    for job in jobs:
        if targets and not any(t['workflow'] == metadata['path'] and t['job'] == job['name'] for t in targets):
            continue
        if (job.get('run_id') != run_id or job.get('head_sha') != metadata['head_sha']
                or job.get('run_attempt', attempt) != attempt):
            raise RuntimeError('job attempt provenance mismatch')
        item = {'job_id': job['id'], 'job': job['name'], 'conclusion': job.get('conclusion'),
                'status': job.get('status'), 'url': job['html_url'], 'steps': []}
        if job['id'] in exempt:
            evidence['jobs'].append(item)
            continue
        for step in job.get('steps', []):
            if step.get('conclusion') not in {'failure', 'success'}:
                item['steps'].append(step | {'report': result(reason='step is not success/failure')})
                continue
            report = archive_report(archive, job['name'], step, [j['name'] for j in jobs])
            item['steps'].append(step | {'report': report})
            if not report['complete']:
                if step.get('conclusion') == 'failure':
                    evidence['missing'].append(f'{job["id"]}/{step["number"]}: {report["reason"]}')
                continue
            for test in report['tests']:
                observations.append(test | {'repo': registry.repo, 'head': metadata['head_sha'],
                    'workflow': metadata['path'], 'job': job['name'], 'run_id': run_id, 'attempt': attempt,
                    'job_id': job['id'], 'step': step['number'], 'url': job['html_url'], 'complete': True,
                    'completed_at': step.get('completed_at') or job.get('completed_at'),
                    'archive_file': evidence.get('archive_file'), 'archive_member': report.get('archive_member'),
                    'source_file': evidence['source_file']})
        evidence['jobs'].append(item)
    registry.save_observations(observations)
    if (not pipeline_exceptions and not evidence['missing'] and metadata.get('status') == 'completed'
            and all(job.get('status') == 'completed' for job in jobs) and evidence.get('archive_file')):
        path = evidence_file(registry, evidence)
        registry.update(lambda data: data.setdefault('attempt_evidence', {}).__setitem__(cache_key, path))
    return evidence


def discover(api, registry, days=30, max_runs=100, max_issues=500, *, targets=None):
    if not (1 <= days <= 365 and 1 <= max_runs <= 100 and 1 <= max_issues <= 500):
        raise ValueError('discovery limits: days 1..365, runs 1..100, issues 1..500')
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
    report = {'scanned_at': now(), 'limits': {'days': days, 'runs': max_runs, 'open_issues': max_issues,
              'archive_bytes': MAX_ARCHIVE_BYTES, 'report_bytes': MAX_REPORT_BYTES},
              'runs': 0, 'attempts': 0, 'cached_attempts': 0, 'open_issues': 0,
              'targets': targets, 'missing': [], 'limits_reached': []}
    try:
        issues = api.open_issues(max_issues)
        report['open_issues'] = len(issues)
        if len(issues) == max_issues:
            report['limits_reached'].append('open_issues')
        evidence_file(registry, {'open_issues': issues})
    except ClassificationTimeout:
        raise
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        issues = []
        report['missing'].append(str(exc))
    if targets:
        verification = registry.synchronize(issues, api.issue)
        active = {tuple(identity(entry)) for entry in verification['entries'] if entry.get('active')}
        targets = [target for target in targets if tuple(identity(target)) not in active]
        if not targets:
            report['verification'] = verification
            report['evidence_file'] = evidence_file(registry, report)
            return report
    try:
        query = urlencode({'status': 'completed', 'created': '>=' + since, 'per_page': max_runs})
        response = api.request('actions/runs?' + query)
        runs = response['workflow_runs'][:max_runs]
        if response.get('total_count', len(runs)) > len(runs) or len(runs) == max_runs:
            report['limits_reached'].append('runs')
        report['runs'] = len(runs)
        evidence_file(registry, response)
        for run in runs:
            if targets and not any(target['workflow'] == run.get('path') for target in targets):
                continue
            for attempt in range(1, run['run_attempt'] + 1):
                try:
                    evidence = collect_attempt(api, registry, run, attempt, reuse=True, targets=targets)
                    report['cached_attempts'] += int(evidence.get('cached', False))
                    report['attempts'] += 1
                    report['missing'].extend(evidence['missing'])
                    evidence_file(registry, evidence)
                except ClassificationTimeout:
                    raise
                except (RuntimeError, ValueError, OSError, KeyError) as exc:
                    report['missing'].append(f'run {run["id"]} attempt {attempt}: {exc}')
    except ClassificationTimeout:
        raise
    except (RuntimeError, ValueError, KeyError) as exc:
        report['missing'].append(str(exc))
    # Linked issues are always refreshed, even when the bounded open issue scan fails.
    report['verification'] = registry.synchronize(issues, api.issue)
    report['evidence_file'] = evidence_file(registry, report)
    return report


def check_job(repo, link):
    parsed = urlparse(link or '')
    host, owner, name = normalize_repo(repo).split('/')
    prefix = f'/{owner}/{name}'
    match = re.fullmatch(re.escape(prefix) + r'/actions/runs/([0-9]+)/job/([0-9]+)', parsed.path.lower())
    if parsed.scheme != 'https' or parsed.hostname != host or not match:
        raise ValueError('unsupported provider or missing exact Actions job link')
    return tuple(map(int, match.groups()))


def collect_snapshot(api, registry, snapshot, pipeline_exceptions=()):
    """Collect terminal snapshot failures, pinning the latest attempt and head."""
    evidence = {'repo': registry.repo, 'head': snapshot['head'], 'checks': []}
    attempts = {}
    for check in snapshot.get('checks', []):
        if check.get('bucket') not in {'fail', 'cancel'}:
            continue
        item = {'link': check.get('link'), 'check_name': check.get('name'),
                'check_workflow': check.get('workflow'), 'error': None}
        try:
            run_id, job_id = check_job(registry.repo, check.get('link'))
            run = api.request(f'actions/runs/{run_id}')
            if run.get('head_sha') != snapshot['head'] or run.get('status') != 'completed':
                raise ValueError('head changed, pending run, or stale check')
            key = (run_id, run['run_attempt'])
            if key not in attempts:
                attempts[key] = collect_attempt(api, registry, run, run['run_attempt'], pipeline_exceptions)
            attempt = attempts[key]
            job = next((j for j in attempt['jobs'] if j['job_id'] == job_id), None)
            if not job:
                raise ValueError('check job does not belong to latest attempt')
            item.update(run_id=run_id, attempt=run['run_attempt'], workflow=attempt['workflow'], **job)
        except ClassificationTimeout:
            raise
        except (ValueError, RuntimeError, OSError, KeyError) as exc:
            item['error'] = str(exc)
        evidence['checks'].append(item)
    evidence['evidence_file'] = evidence_file(registry, evidence)
    return evidence


def pr_repository(pr, repo=None):
    """Use the PR's base repository, never inherit a fork's registry entries."""
    if not pr.startswith('https://'):
        command = ['gh', 'pr', 'view', pr, '--json', 'url']
        if repo:
            command += ['--repo', repo]
        completed = github_run(command, capture_output=True, text=True, timeout=30)
        if completed.returncode:
            raise ValueError('cannot resolve PR base repository')
        pr = json.loads(completed.stdout)['url']
    parsed = urlparse(pr)
    parts = parsed.path.strip('/').split('/')
    if parsed.scheme != 'https' or not parsed.hostname or len(parts) != 4 or parts[2] != 'pull' or not parts[3].isdigit():
        raise ValueError('invalid PR URL')
    base = normalize_repo('/'.join([parsed.hostname, *parts[:2]]))
    if repo and normalize_repo(repo if repo.count('/') == 2 else parsed.hostname + '/' + repo) != base:
        raise ValueError('--repo disagrees with PR base repository')
    return base
