#!/usr/bin/env python3
"""Version 1 private reviewer observations, assessments, and deterministic reports.

All query operations are local and read-only. Mutation payloads are retained before
ledger writes so failed collection can be replayed with the same identifiers.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import argparse
import copy
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
from shared.private_state import root, read, mutate

API_VERSION = 1
SEVERITIES = ('Major', 'Minor', 'Nit')
STATUSES = {'not_run', 'running', 'completed', 'failed', 'incomplete', 'exhausted'}
DISPOSITIONS = {'actionable', 'excluded', 'deferred', 'fixed', 'unknown'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode('utf-8', 'backslashreplace')).hexdigest()[:24]


def normalize_repo(repo):
    repo = repo.lower()
    root(repo)  # Validate through the existing private storage helper.
    return repo


def pr_identity(url):
    parsed = urlparse(url)
    parts = parsed.path.strip('/').split('/')
    if (parsed.scheme != 'https' or not parsed.hostname or len(parts) != 4
            or parts[2] != 'pull' or not parts[3].isdigit() or int(parts[3]) < 1):
        raise ValueError('expected a base-repository PR URL')
    return normalize_repo('/'.join([parsed.hostname, *parts[:2]])), int(parts[3])


def stats_home():
    return root('stats/local/user').parents[2]


def ledger_path(repo):
    return root(normalize_repo(repo)) / 'review-stats.json'


def empty(repo):
    return {'api_version': API_VERSION, 'repository': normalize_repo(repo),
            'runs': {}, 'assessment_history': []}


def ledger(repo):
    value = read(ledger_path(repo), empty(repo))
    if value.get('api_version') != API_VERSION:
        raise ValueError('unsupported review stats version')
    return value


def retain(repo, category, value):
    """Content addressed artifacts never replace different evidence."""
    path = ledger_path(repo).parent / 'review-stats-artifacts' / category / (digest(value) + '.json')
    mutate(path, value, lambda old: None)
    return str(path)


def finding_key(finding):
    return digest({key: finding[key] for key in ('path', 'line', 'message', 'fix')})


def observation_id(run_id, reviewer, finding):
    return 'f-' + digest([run_id, reviewer, finding])


def begin_review(*, pr_url, cycle, head, preparation_id, reviewers, attempt='1', run_id=None):
    repo, pr = pr_identity(pr_url)
    if type(cycle) is not int or cycle < 1 or not re.fullmatch(r'[0-9a-f]{40,64}', head):
        raise ValueError('positive cycle and pinned head required')
    if (not preparation_id or not reviewers or len(set(reviewers)) != len(reviewers)
            or any(not isinstance(name, str) or not name.strip() for name in reviewers)):
        raise ValueError('preparation and distinct configured reviewers required')
    identity = dict(repository=repo, pr=pr, pr_url=pr_url, cycle=cycle, head=head,
                    preparation_id=preparation_id, attempt=str(attempt), reviewers=reviewers)
    run_id = run_id or 'r-' + digest(identity)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', run_id):
        raise ValueError('invalid run ID')
    item = {**identity, 'id': run_id, 'reviews': {
        name: {'id': 'a-' + digest([run_id, name]), 'status': 'not_run',
               'findings': [], 'duration_seconds': None, 'correction_retries': None,
               'provider': None, 'model': None, 'usage': None, 'artifacts': []}
        for name in reviewers}}
    def apply(data):
        old = data['runs'].get(run_id)
        if old and any(old[key] != item[key] for key in identity):
            raise ValueError('run identity conflict')
        if not old:
            data['runs'][run_id] = item
        return copy.deepcopy(data['runs'][run_id])
    retain(repo, 'runs', item)
    return mutate(ledger_path(repo), empty(repo), apply)


def record_review(repo, run_id, reviewer, *, status, findings=None, **metadata):
    """Record one configured parent's result; retries/resume reuse its attempt ID."""
    repo = normalize_repo(repo)
    if status not in STATUSES:
        raise ValueError('invalid reviewer status')
    findings = [] if findings is None else findings
    if not isinstance(findings, list):
        raise ValueError('findings must be an explicit list')
    if status != 'completed' and findings:
        raise ValueError('operational failures cannot supply assessed findings')
    unique = {}
    for finding in findings:
        if (not isinstance(finding, dict) or set(finding) != {'severity', 'path', 'line', 'message', 'fix'}
                or finding['severity'] not in SEVERITIES or type(finding['line']) is not int
                or finding['line'] < 1 or any(not isinstance(finding[k], str) or not finding[k].strip()
                                           for k in ('path', 'message', 'fix'))):
            raise ValueError('invalid structured finding')
        fid = observation_id(run_id, reviewer, finding)
        unique[fid] = {'id': fid, 'reported': finding,
                       'canonical_issue': 'i-' + finding_key(finding)}
    if set(metadata) - {'duration_seconds', 'prompt_fingerprint', 'correction_retries',
                        'provider', 'model', 'usage', 'artifacts', 'reason'}:
        raise ValueError('unknown review metadata')
    for key in ('duration_seconds', 'correction_retries'):
        if metadata.get(key) is not None and (type(metadata[key]) not in (int, float)
                or not math.isfinite(metadata[key]) or metadata[key] < 0
                or (key == 'correction_retries' and type(metadata[key]) is not int)):
            raise ValueError('negative or invalid duration/retry count')
    payload = dict(run_id=run_id, reviewer=reviewer, status=status, findings=findings, **metadata)
    replay = retain(repo, 'reviews', payload)
    def apply(data):
        review = data['runs'][run_id]['reviews'][reviewer]
        new = {**review, **metadata, 'status': status, 'findings': list(unique.values()), 'replay': replay}
        if review['status'] not in {'not_run', 'running'} and review != new:
            raise ValueError('terminal review is immutable; use a new attempt')
        review.update(new)
        return copy.deepcopy(review)
    return mutate(ledger_path(repo), empty(repo), apply)


def record_assessments(repo, assessments):
    """Append coordinator verdicts. Latest verdict wins; history is never erased.

    Each observation has an independently correctable canonical link. Reusing an
    assessment ID is idempotent, but changing its payload is an error.
    """
    repo = normalize_repo(repo)
    retain(repo, 'assessments', assessments)
    def apply(data):
        observations = {f['id']: (run, f) for run in data['runs'].values()
                        for rev in run['reviews'].values() for f in rev['findings']}
        added = []
        for entry in assessments:
            if set(entry) != {'id', 'finding_id', 'canonical_issue', 'verdict', 'severity',
                              'disposition', 'evidence', 'reason'}:
                raise ValueError('invalid assessment fields')
            if (not all(isinstance(entry[k], str) and entry[k].strip()
                        for k in ('id', 'finding_id', 'canonical_issue', 'reason'))
                    or entry['verdict'] not in {'confirmed', 'rejected', 'unverified'}
                    or entry['disposition'] not in DISPOSITIONS
                    or (entry['severity'] not in SEVERITIES and
                        not (entry['verdict'] == 'unverified' and entry['severity'] is None))
                    or not isinstance(entry['evidence'], list) or not entry['evidence']
                    or any(not isinstance(ref, str) or not ref.strip() for ref in entry['evidence'])):
                raise ValueError('assessment needs verdict, validated severity and evidence references')
            if entry['finding_id'] not in observations:
                raise ValueError('unknown finding ID')
            old = next((x for x in data['assessment_history'] if x['id'] == entry['id']), None)
            if old:
                if {k: v for k, v in old.items() if k != 'sequence'} != entry:
                    raise ValueError('assessment ID conflict')
                continue
            item = {**entry, 'sequence': len(data['assessment_history']) + 1}
            data['assessment_history'].append(item)
            added.append(item)
        return added
    return mutate(ledger_path(repo), empty(repo), apply)


def repositories():
    return sorted('/'.join(path.parts[-4:-1]) for path in stats_home().glob('*/*/*/review-stats.json'))


def details(repo, run_id):
    data = ledger(repo)
    run = data['runs'][run_id]
    ids = {f['id'] for r in run['reviews'].values() for f in r['findings']}
    return {'api_version': API_VERSION, 'run': run,
            'assessments': [a for a in data['assessment_history'] if a['finding_id'] in ids]}


def buckets():
    return dict.fromkeys((*SEVERITIES, 'total'), 0)


def row():
    return {**{key: buckets() for key in ('reported', 'confirmed', 'rejected', 'unverified',
            'confirmed_issues', 'shared', 'exclusive', 'exclusive_unknown',
            'actionable', 'excluded', 'deferred', 'fixed', 'scope_unknown')},
            'reviews': dict.fromkeys(('completed', 'clean', 'failures', 'exhausted', 'not_run',
                                     'running', 'correction_retries', 'correction_unknown'), 0),
            'duration_seconds': 0, 'duration_unknown': 0, 'severity_changes': {}}


def count(target, severity):
    target[severity] += 1
    target['total'] += 1


def summary(repo=None, reviewer=None):
    """Query v1. Counts observations separately from PR-scoped unique credits."""
    repos = [normalize_repo(repo)] if repo else repositories()
    reviewers, total, issues = {}, row(), {}
    warnings = {}
    def warn(label, n=1):
        warnings[label] = warnings.get(label, 0) + n
    run_count = 0
    for repository in repos:
        data = ledger(repository)
        latest = {a['finding_id']: a for a in data['assessment_history']}
        for run_id, run in sorted(data['runs'].items()):
            run_count += 1
            comparable = (len(run['reviewers']) > 1 and
                          all(r['status'] == 'completed' for r in run['reviews'].values()) and
                          all(latest.get(f['id'], {}).get('verdict', 'unverified') != 'unverified'
                              for r in run['reviews'].values() for f in r['findings']))
            for name, review in run['reviews'].items():
                r = reviewers.setdefault(name, row())
                for target in (r, total):
                    status = review['status']
                    field = {'completed': 'completed', 'failed': 'failures', 'incomplete': 'failures',
                             'exhausted': 'exhausted', 'not_run': 'not_run', 'running': 'running'}[status]
                    target['reviews'][field] += 1
                    if status == 'completed' and not review['findings']:
                        target['reviews']['clean'] += 1
                    retries = review.get('correction_retries')
                    target['reviews']['correction_unknown' if retries is None else 'correction_retries'] += 1 if retries is None else retries
                    duration = review.get('duration_seconds')
                    target['duration_unknown'] += duration is None
                    target['duration_seconds'] += duration or 0
                if review['status'] != 'completed':
                    warn(f'{repository} {name} reviews {review["status"]}')
                unverified = sum(latest.get(f['id'], {}).get('verdict', 'unverified') == 'unverified'
                                 for f in review['findings'])
                if unverified:
                    warn(f'{repository} {name} unverified findings', unverified)
                for f in review['findings']:
                    a = latest.get(f['id'])
                    verdict = a['verdict'] if a else 'unverified'
                    severity = a['severity'] if a and a['severity'] else f['reported']['severity']
                    disposition = a['disposition'] if a else 'unknown'
                    for target in (r, total):
                        count(target['reported'], f['reported']['severity'])
                        count(target[verdict], severity)
                        count(target['scope_unknown' if disposition == 'unknown' else disposition], severity)
                    if a and severity != f['reported']['severity']:
                        change = f'{f["reported"]["severity"]}->{severity}'
                        for target in (r, total):
                            target['severity_changes'][change] = target['severity_changes'].get(change, 0) + 1
                    if verdict == 'confirmed':
                        key = (repository, run['pr'], a['canonical_issue'])
                        issue = issues.setdefault(key, {'reviewers': set(), 'eligible': set(),
                                                       'severity': severity, 'sequence': 0})
                        issue['reviewers'].add(name)
                        if comparable:
                            issue['eligible'].add(name)
                        if a['sequence'] >= issue['sequence']:
                            issue.update(severity=severity, sequence=a['sequence'])
    for issue in issues.values():
        names, severity = issue['reviewers'], issue['severity']
        if reviewer and reviewer not in names:
            continue
        count(total['confirmed_issues'], severity)
        category = 'shared' if len(names) > 1 else ('exclusive' if issue['eligible'] else 'exclusive_unknown')
        count(total[category], severity)
        for name in names:
            count(reviewers[name]['confirmed_issues'], severity)
            count(reviewers[name][category], severity)
    for target in [total, *reviewers.values()]:
        target['precision'] = {}
        for severity in (*SEVERITIES, 'total'):
            n = target['confirmed'][severity] + target['rejected'][severity]
            target['precision'][severity] = {'value': target['confirmed'][severity] / n if n else None,
                                             'sample_size': n}
    if reviewer:
        # Overall issue counts above keep global overlap context; observations use the selected reviewer.
        if reviewer in reviewers:
            selected = copy.deepcopy(reviewers[reviewer])
            for key in ('confirmed_issues', 'shared', 'exclusive', 'exclusive_unknown'):
                selected[key] = total[key]
            total = selected
        else:
            total = None
        reviewers = {reviewer: reviewers[reviewer]} if reviewer in reviewers else {}
    return {'api_version': API_VERSION, 'repositories': repos, 'run_count': run_count,
            'reviewers': reviewers, 'total': total if run_count and reviewers else None,
            'warnings': [f'{label}: {n}' for label, n in sorted(warnings.items())], 'reports': {
                'user': str(stats_home() / 'review-stats.md'),
                'repositories': {r: str(ledger_path(r).with_suffix('.md')) for r in repos}}}


def link(path):
    return Path(path).resolve().as_uri()


def markdown(value, title):
    lines = [f'# {title}', '', 'Local future iteration runs only. Unknown precision has no adjudicated sample.',
             'Reported, verdict and scope rows count observations. confirmed_issues counts unique PR defects once overall; reviewer credits can overlap.',
             'Precision = confirmed / (confirmed + rejected); unverified findings are excluded.',
             'Reported uses reported severity; verdicts use validated severity. Unique issues use the latest confirmed severity.',
             'Exclusive credit requires completed comparable reviewers on the same pinned run and fully assessed findings.',
             'No recall or overall quality score is measured.', '',
             f'[Local user report]({link(value["reports"]["user"])})', '']
    for repo, path in value['reports']['repositories'].items():
        lines.append(f'[{repo}]({link(path)})')
    lines.append('')
    for name, item in [('Overall', value['total']), *sorted(value['reviewers'].items())]:
        if item is None:
            continue
        lines += [f'## {name}', '', '| Metric | Major | Minor | Nit | Total |',
                  '| --- | ---: | ---: | ---: | ---: |']
        for metric in ('reported', 'confirmed', 'rejected', 'unverified', 'confirmed_issues',
                       'shared', 'exclusive', 'exclusive_unknown', 'actionable', 'excluded',
                       'deferred', 'fixed', 'scope_unknown'):
            lines.append('| ' + metric + ' | ' + ' | '.join(str(item[metric][s]) for s in (*SEVERITIES, 'total')) + ' |')
        lines += ['| precision (sample) | ' + ' | '.join(
            f'{item["precision"][s]["value"]:.1%} (n={item["precision"][s]["sample_size"]})'
            if item['precision'][s]['value'] is not None else 'unknown (n=0)'
            for s in (*SEVERITIES, 'total')) + ' |', '',
            ', '.join(f'{key}: {n}' for key, n in item['reviews'].items()), '',
            f'Duration: {item["duration_seconds"]:.3f}s known; {item["duration_unknown"]} unknown. '
            'Provider/model/token usage is retained when exposed; null means unknown.', '']
        if item['severity_changes']:
            lines += ['Reported → validated severity: ' + ', '.join(
                f'{transition}: {n}' for transition, n in sorted(item['severity_changes'].items())), '']
    if value['total'] is None:
        lines += ['No recorded reviews.', '']
    if value['warnings']:
        lines += ['## Missing data', '', *['- ' + w for w in value['warnings']], '']
    return '\n'.join(lines)


def render_reports():
    """Serialize all report writers; queries never call this function."""
    def render(_):
        user = summary()
        for repo in user['repositories']:
            value = summary(repo)
            text = markdown(value, f'Reviewer statistics: {repo}')
            text += f'\n[Ledger and assessment history]({link(ledger_path(repo))})\n'
            data = ledger(repo)
            latest = {a['finding_id']: a for a in data['assessment_history']}
            text += '\n## Retained reviews and findings\n\n'
            for run_id, run in sorted(data['runs'].items()):
                text += f'### [{run_id}]({run["pr_url"]}) — cycle {run["cycle"]}, {run["head"]}\n\n'
                for name, review in run['reviews'].items():
                    text += f'- {name}: {review["status"]}; attempt {review["id"]}'
                    for path in review.get('artifacts', []) + ([review['replay']] if review.get('replay') else []):
                        text += f' [evidence]({link(path)})'
                    text += '\n'
                    for f in review['findings']:
                        a = latest.get(f['id'])
                        text += f'  - [{f["id"]}]({run["pr_url"]}): {a["verdict"] if a else "unverified"}; '
                        text += (a['canonical_issue'] if a else f['canonical_issue']) + '\n'
                        reported = f['reported']
                        validated = a['severity'] if a and a['severity'] else 'unknown'
                        text += (f'    {reported["path"]}:{reported["line"]}; '
                                 f'reported {reported["severity"]}, validated {validated}: '
                                 + reported['message'].replace('\n', ' ') + '\n')
                        if a:
                            for ref in a['evidence']:
                                url = ref if urlparse(ref).scheme in ('https', 'http', 'file') else link(ref)
                                text += f'    - [assessment evidence]({url})\n'
                text += '\n'
            _write_report(ledger_path(repo).with_suffix('.md'), text)
        _write_report(stats_home() / 'review-stats.md', markdown(user, 'Reviewer statistics: local user'))
        return user['reports']
    return mutate(stats_home() / 'review-stats-reports.json', {}, render)


def _write_report(path, text):
    # Use the shared atomic/permission behavior for text as well as JSON.
    import os
    import tempfile
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.report-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', errors='backslashreplace') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-version', type=int, choices=[API_VERSION], default=API_VERSION)
    sub = parser.add_subparsers(dest='command', required=True)
    q = sub.add_parser('summary')
    q.add_argument('--repo'); q.add_argument('--reviewer')
    sub.add_parser('repositories')
    d = sub.add_parser('details'); d.add_argument('--repo', required=True); d.add_argument('--run', required=True)
    for command in ('begin', 'record-review', 'assess'):
        p = sub.add_parser(command); p.add_argument('--file', required=True)
        if command != 'begin':
            p.add_argument('--repo', required=True)
    sub.add_parser('render')
    args = parser.parse_args()
    try:
        if args.command == 'summary':
            result = summary(args.repo, args.reviewer)
        elif args.command == 'repositories':
            result = {'api_version': API_VERSION, 'repositories': repositories()}
        elif args.command == 'details':
            result = details(args.repo, args.run)
        elif args.command == 'render':
            result = render_reports()
        else:
            payload = json.loads(Path(args.file).read_text())
            if args.command == 'begin':
                result = begin_review(**payload)
            elif args.command == 'record-review':
                result = record_review(args.repo, **payload)
            else:
                result = record_assessments(args.repo, payload)
            render_reports()
        print(json.dumps({'api_version': API_VERSION, 'data': result}, sort_keys=True))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f'review_stats: {exc}\n')


if __name__ == '__main__':
    main()
