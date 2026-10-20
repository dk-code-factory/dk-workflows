"""Issue-backed flake registry. Legacy observations never authorize red CI."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from shared.repository_scope import inside_checkout
from shared.private_state import root, read, mutate

SHA = re.compile(r"^[0-9a-f]{40}$")


def require_head(head):
    if not isinstance(head, str) or not SHA.fullmatch(head):
        raise ValueError("head must be a full lowercase SHA")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def normalize_repo(repo):
    repo = repo.removeprefix('remote:')
    root(repo)  # validate before using in paths or API requests
    return repo.lower()


def identity(item):
    return [item.get(k) for k in ('workflow', 'job', 'test', 'signature')]


def issue_number(repo, url):
    parsed = urlparse(url)
    host, owner, name = normalize_repo(repo).split('/')
    match = re.fullmatch(rf'/{re.escape(owner)}/{re.escape(name)}/issues/([1-9][0-9]*)', parsed.path.lower())
    if parsed.scheme != 'https' or parsed.hostname != host or not match or parsed.query or parsed.fragment:
        raise ValueError('issue URL must belong to the exact repository')
    return int(match[1])


def issue_match(issue, item):
    """Return explicit/matching/none; ambiguous prose is evidence only.

    Structured declarations or exact backtick-quoted context avoid fuzzy identity
    matches. No issue text is executed or treated as agent instructions.
    """
    text = (issue.get('title') or '') + '\n' + (issue.get('body') or '')
    for block in re.findall(r'```dk-flaky\s*\n(.*?)\n```', text, re.S):
        try:
            value = json.loads(block)
        except ValueError:
            continue
        if isinstance(value, dict) and identity(value) == identity(item):
            return 'explicit' if value.get('flaky') is True else 'matching'
    if not all(isinstance(v, str) and v and f'`{v}`' in text for v in identity(item)):
        # Retain plausible links for diagnosis without treating partial context
        # as a verified match, even when historical flakiness exists.
        return 'candidate' if all(isinstance(item.get(k), str) and item[k] and item[k] in text
                                  for k in ('test', 'signature')) else None
    # Only an affirmative declaration with all exact context is automatic proof.
    if re.search(r'(?i)\b(?:not|never|no|non)[ -]?(?:a )?(?:flaky|intermittent)', text):
        return None
    if re.search(r'(?i)\b(?:maybe|possibly|suspect|could|might|whether|is it)\b|\?', text):
        return 'matching'
    return 'explicit' if re.search(r'(?im)^\s*(?:confirmed flaky test|known flaky test|this test is flaky)[.: ]', text) else 'matching'


def historical_proof(observations, item):
    same = [o for o in observations if o.get('complete') is True and
            all(o.get(k) == item.get(k) for k in ('workflow', 'job', 'test'))]
    for fail in same:
        if fail.get('outcome') != 'fail' or fail.get('signature') != item['signature']:
            continue
        for passed in same:
            if (passed.get('outcome') == 'pass' and passed.get('head') == fail.get('head')
                    and passed.get('completed_at', '') > fail.get('completed_at', '~')
                    and (passed.get('run_id'), passed.get('attempt'), passed.get('job_id')) !=
                        (fail.get('run_id'), fail.get('attempt'), fail.get('job_id'))):
                return {'kind': 'same_commit_fail_pass', 'failure': fail, 'pass': passed}
    return None


class Registry:
    def __init__(self, repo, cwd=None):
        self.repo = normalize_repo(repo)
        self.path = root(self.repo) / 'flaky-tests.json'
        if cwd and inside_checkout(self.path, cwd):
            raise ValueError('flake tracking must be outside the repository')
        # Preserve pre-normalization legacy history without enabling it.
        folders = [root(self.repo).parents[2]]
        for part in self.repo.split('/'):
            folders = [child for folder in folders if folder.exists() for child in folder.iterdir()
                       if child.is_dir() and child.name.lower() == part]
        for folder in folders:
            legacy = folder / 'flaky-tests.json'
            if legacy == self.path or not legacy.exists():
                continue
            old = read(legacy, {})
            def merge(data):
                for key, events in old.get('tests', {}).items():
                    target = data.setdefault('tests', {}).setdefault(key, [])
                    for event in events:
                        if event not in target:
                            target.append(event)
            self.update(merge)

    def load(self):
        return read(self.path, {'tests': {}, 'entries': {}, 'observations': [], 'scans': {}})

    def update(self, operation):
        return mutate(self.path, {'tests': {}, 'entries': {}, 'observations': [], 'scans': {}}, operation)

    def save_observations(self, items):
        def save(data):
            stored = data.setdefault('observations', [])
            seen = {digest(o) for o in stored}
            for item in items:
                require_head(item['head'])
                if item.get('repo') != self.repo or not all(item.get(k) for k in
                        ('workflow', 'job', 'test', 'run_id', 'attempt', 'job_id', 'url', 'completed_at')):
                    raise ValueError('observation lacks exact repository/attempt/context evidence')
                key = digest(item)
                if key not in seen:
                    stored.append(item)
                    seen.add(key)
        self.update(save)

    def synchronize(self, issues, fetch_issue):
        """Refresh every linked issue directly, then verify against current evidence."""
        saved = self.load()
        urls = {e['issue_url'] for e in saved.get('entries', {}).values()}
        failures = [o for o in saved.get('observations', []) if o.get('outcome') == 'fail']
        urls.update(i['html_url'] for i in issues if i.get('html_url') and not i.get('pull_request')
                    and any(issue_match(i, o) for o in failures))
        live = {}
        for url in sorted(urls):
            try:
                number = issue_number(self.repo, url)
                issue = fetch_issue(number)
                if issue.get('html_url', '').lower() != url.lower() or issue.get('pull_request'):
                    raise ValueError('issue identity mismatch')
                live[url] = issue
            except (RuntimeError, OSError, ValueError):
                live[url] = None
        checked = now()

        def sync(data):
            entries = data.setdefault('entries', {})
            sources = data.setdefault('issue_snapshots', {})
            for issue in live.values():
                if issue:
                    sources.setdefault(digest(issue), issue)
            observations = data.get('observations', [])
            failures = {digest(identity(o)): o for o in observations if o.get('outcome') == 'fail'}
            for url, issue in live.items():
                if not issue:
                    continue
                for item in failures.values():
                    match = issue_match(issue, item)
                    if not match:
                        continue
                    key = digest([identity(item), url])
                    entries.setdefault(key, dict(zip(('workflow', 'job', 'test', 'signature'), identity(item))))
                    entries[key].update(issue_url=url)
            for entry in entries.values():
                issue = live.get(entry['issue_url'])
                state = issue.get('state') if issue else 'unavailable'
                match = issue_match(issue, entry) if issue else None
                proof = ({'kind': 'explicit_issue', 'url': entry['issue_url']} if match == 'explicit'
                         else historical_proof(observations, entry) if match == 'matching' else None)
                active = state == 'open' and proof is not None
                status = 'active' if active else 'closed' if state == 'closed' else 'unavailable' if issue is None else 'candidate'
                event = {'status': status, 'issue_state': state, 'issue_updated_at': issue.get('updated_at') if issue else None,
                         'proof': proof, 'issue_evidence': digest(issue) if issue else None}
                history = entry.setdefault('history', [])
                if not history or any(history[-1].get(k) != v for k, v in event.items()):
                    history.append(event | {'at': checked})
                entry.update(event, active=active, checked_at=checked)
            return {'checked_at': checked, 'entries': list(entries.values()),
                    'unavailable_issues': [url for url, issue in live.items() if issue is None]}
        return self.update(sync)


def record_flaky(data, test, signature, head, outcome, attempt_id, url):
    require_head(head)
    if not all((test, signature, attempt_id, url)) or outcome not in {"fail", "pass"} or not url.startswith("https://"):
        raise ValueError("flaky evidence needs name, signature, outcome, attempt, and HTTPS link")
    key = json.dumps([test, signature, head])
    events = data.setdefault("tests", {}).setdefault(key, [])
    if any(x["attempt"] == attempt_id for x in events):
        raise ValueError("duplicate test attempt")
    events.append({"outcome": outcome, "attempt": attempt_id, "url": url})
    fails = [x for x in events if x["outcome"] == "fail"]
    passes_after = [x for x in events if x["outcome"] == "pass" and any(events.index(f) < events.index(x) for f in fails)]
    return {"flaky": bool(passes_after), "test": test, "signature": signature, "head": head,
            "failures": fails, "passes": passes_after}
