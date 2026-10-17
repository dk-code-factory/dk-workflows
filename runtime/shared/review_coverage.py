"""Required review provenance, independent of best-effort reviewer analytics."""
import hashlib
import json
import subprocess
from pathlib import Path

from shared.private_state import read, mutate


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def git(cwd, *args):
    return subprocess.check_output(['git', *args], cwd=cwd, text=True).strip()


def scope(prepared):
    raw = read(Path(prepared['sources']['decisions']['path']), {})
    decisions = raw.get('data', raw)
    # Head changes alone are allowed for proven tree-equivalent regrouping.
    return digest({'decisions': {k: v for k, v in decisions.items() if k != 'head_sha'},
                   'analysis': prepared['analysis'], 'reviewers': prepared['reviewers'],
                   'base_sha': prepared['identity']['base_sha'],
                   'scope_sources': {key: value['sha256'] for key, value in prepared['sources'].items()
                                     if key.split('/')[0] in {'issues', 'choices', 'user_instructions', 'rules'}}})


def retain(session_path, context, prepared, result, session_id):
    """Save an immutable result with no knowledge of iteration budgets or state."""
    artifact = {'version': 2, 'session_id': session_id,
                'pr_url': context['url'], 'head': context['head_sha'],
                'tree': git(context['checkout'], 'rev-parse', f"{context['head_sha']}^{{tree}}"),
                'scope': scope(prepared), 'preparation_id': prepared['preparation_id'],
                'result': result}
    fingerprint = digest(artifact)
    path = Path(session_path).resolve().parent / 'review-results' / (fingerprint + '.json')
    def immutable(old):
        if old != artifact:
            raise ValueError('review result artifact changed')
    mutate(path, artifact, immutable)
    return {'path': str(path), 'sha256': fingerprint}


def load(item):
    artifact = read(Path(item['path']), {})
    if digest(artifact) != item['sha256'] or artifact.get('version') not in {1, 2}:
        raise ValueError('review result artifact changed or has an unsupported version')
    return artifact


def verify(item, prepared):
    """Validate current-tree coverage; callers separately assess remediation."""
    artifact = load(item)
    context = read(Path(prepared['sources']['context']['path']), {})
    tree = git(context['checkout'], 'rev-parse', f"{prepared['identity']['head_sha']}^{{tree}}")
    if (artifact['pr_url'] != prepared['identity']['pr_url']
            or artifact['tree'] != tree or artifact['scope'] != scope(prepared)):
        raise ValueError('final tree or scope lacks review coverage')
    result = artifact['result']
    completed = result['executed_reviewers'] + result['reused_reviewers']
    skipped = result['skipped_reviewers']
    if (result.get('complete') is not True or not completed
            or len(set(completed)) != len(completed)
            or any(not s.get('reason') for s in skipped)
            or set(completed) & {s['reviewer'] for s in skipped}
            or set(completed) | {s['reviewer'] for s in skipped} != set(prepared['reviewers'])):
        raise ValueError('configured-reviewer execution is incomplete')
    return artifact
