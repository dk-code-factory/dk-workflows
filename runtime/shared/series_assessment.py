"""Evidence for a tree-preserving assessment of the full published PR series."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import quote, urlparse

SHA = re.compile(r"^[0-9a-f]{40}$")


def command(cwd, *args):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "command failed")
    return result.stdout.strip()


def pr_key(url):
    if not isinstance(url, str):
        raise ValueError("assessment requires a PR URL")
    parsed = urlparse(url)
    parts = parsed.path.strip('/').split('/')
    if (parsed.scheme != 'https' or not parsed.hostname or len(parts) != 4
            or not all(parts) or parts[2] != 'pull' or not parts[3].isdigit()
            or int(parts[3]) < 1 or parsed.query or parsed.fragment):
        raise ValueError("assessment requires a PR URL")
    return parsed.hostname.lower(), '/'.join(parts[:2]).lower(), int(parts[3])


def validate(data):
    if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        raise ValueError("invalid series assessment schema")
    pr_key(data.get('pr_url'))
    if (not isinstance(data.get('head_repo'), str) or
            len(data['head_repo'].split('/')) != 2 or not all(data['head_repo'].split('/'))
            or not isinstance(data.get('target_branch'), str) or not data['target_branch'].strip()):
        raise ValueError("invalid series assessment repository or target branch")
    for field in ('reviewed_head', 'reviewed_tree', 'target_tip', 'final_head'):
        if not isinstance(data.get(field), str) or not SHA.fullmatch(data[field]):
            raise ValueError(f"invalid series assessment {field}")
    if type(data.get('commit_count')) is not int or data['commit_count'] < 1:
        raise ValueError("invalid series assessment commit count")
    reasons = data.get('reasons')
    if not isinstance(reasons, list) or not reasons or any(
            not isinstance(reason, str) or not reason.strip() for reason in reasons):
        raise ValueError("series assessment requires boundary reasons")
    if not isinstance(data.get('outcome'), str) or data['outcome'] not in {'no_op', 'published'}:
        raise ValueError("series assessment is incomplete")
    unchanged = data['reviewed_head'] == data['final_head']
    if (data['outcome'] == 'no_op') != unchanged:
        raise ValueError("series assessment outcome disagrees with heads")
    return data


def outside_repository(path, cwd):
    path = Path(path).resolve()
    for root in (command(cwd, 'git', 'rev-parse', '--show-toplevel'),
                 command(cwd, 'git', 'rev-parse', '--git-common-dir')):
        root = (Path(cwd) / root).resolve()
        if path.is_relative_to(root):
            raise ValueError("series assessment must live outside the repository")
    return path


def load(path, cwd):
    return validate(json.loads(outside_repository(path, cwd).read_text(encoding='utf-8')))


def save(path, data, cwd):
    validate(data)
    path = outside_repository(path, cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.series-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        # Keep old evidence immutable; a reassessment gets a new path.
        os.link(name, path)
    finally:
        os.unlink(name)
    return path


def live_snapshot(pr, cwd):
    def gh(*args):
        return json.loads(command(cwd, 'gh', *args))

    fields = 'url,number,headRefOid,headRepository,baseRefName'
    meta = gh('pr', 'view', pr, '--json', fields)
    host, base_repo, number = pr_key(meta['url'])
    if meta['number'] != number:
        raise ValueError("live PR identity is inconsistent")
    head_repo = (meta.get('headRepository') or {}).get('nameWithOwner')
    if not head_repo:
        raise ValueError("live PR head repository is missing")
    ref = f"repos/{base_repo}/git/ref/heads/{quote(meta['baseRefName'], safe='')}"
    tip = gh('api', '--hostname', host, ref)['object']['sha']
    head = meta['headRefOid']
    if any(not isinstance(value, str) or not SHA.fullmatch(value) for value in (tip, head)):
        raise ValueError('live PR head or target tip is malformed')
    tree = gh('api', '--hostname', host, f'repos/{head_repo}/git/commits/{head}')['tree']['sha']
    latest = gh('pr', 'view', pr, '--json', fields)
    if latest != meta or gh('api', '--hostname', host, ref)['object']['sha'] != tip:
        raise RuntimeError("PR head or target tip changed during assessment")
    # Local objects must match remote evidence; never fetch or mutate in the gate.
    if command(cwd, 'git', 'rev-parse', f'{head}^{{tree}}') != tree:
        raise RuntimeError("local and published trees disagree")
    base = command(cwd, 'git', 'merge-base', tip, head)
    if base != tip:
        raise RuntimeError('published head is not based on current target tip')
    count = int(command(cwd, 'git', 'rev-list', '--count', f'{base}..{head}'))
    return dict(pr_url=meta['url'], head_repo=head_repo.lower(), final_head=head,
                final_tree=tree, target_tip=tip, target_branch=meta['baseRefName'],
                commit_count=count)


def check(data, live, final_head):
    if data is None:
        return 'series_missing'
    try:
        validate(data)
    except ValueError:
        return 'series_invalid'
    if not isinstance(live, dict):
        return 'series_unverified'
    if (pr_key(data['pr_url']) != pr_key(live['pr_url'])
            or data['head_repo'].lower() != live['head_repo'].lower()):
        return 'series_pr_mismatch'
    if data['final_head'] != final_head or live['final_head'] != final_head:
        return 'series_head_changed'
    if data['reviewed_tree'] != live['final_tree']:
        return 'series_tree_changed'
    if data['target_tip'] != live['target_tip'] or data['target_branch'] != live['target_branch']:
        return 'series_target_changed'
    if data['commit_count'] != live['commit_count']:
        return 'series_count_changed'
    return 'clean'
