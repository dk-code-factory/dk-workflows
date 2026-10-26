"""Required review provenance, independent of best-effort reviewer analytics."""
import hashlib
import json
import subprocess
from pathlib import Path

from shared.private_state import read, mutate

PROTECTED_SOURCE_CATEGORIES = frozenset({'issues', 'choices', 'user_instructions', 'rules'})


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8', 'backslashreplace')).hexdigest()


def git(cwd, *args):
    return subprocess.check_output(['git', *args], cwd=cwd, text=True).strip()


def scope(prepared, *, decisions=None):
    raw = read(Path(prepared['sources']['decisions']['path']), {}) if decisions is None else decisions
    decisions = raw.get('data', raw)
    # Head changes alone are allowed for proven tree-equivalent regrouping.
    return digest({'decisions': {k: v for k, v in decisions.items() if k != 'head_sha'},
                   'analysis': prepared['analysis'], 'reviewers': prepared['reviewers'],
                   'base_sha': prepared['identity']['base_sha'],
                   'scope_sources': {key: value['sha256'] for key, value in prepared['sources'].items()
                                     if key.split('/')[0] in PROTECTED_SOURCE_CATEGORIES}})


def outside(path, context):
    path = Path(path).resolve()
    for key in ('repository', 'checkout'):
        if path.is_relative_to(Path(context[key]).resolve()):
            raise ValueError('private review state must stay outside the repository and checkout')
    return path


def storage_path(path, context):
    """Check JSON, temporary-file directory and the adjacent private_state lock."""
    path = Path(path)
    outside(path.parent, context)
    outside(path, context)
    outside(path.parent / (path.name + '.lock'), context)
    return path


def retain(session_path, context, prepared, result, session_id):
    """Save an immutable result with no knowledge of iteration budgets or state."""
    session_path = storage_path(session_path, context)
    directory = outside(session_path.resolve().parent / 'review-results', context)
    session = read(session_path, {})
    if session.get('id') != session_id or session.get('session_version') != 1:
        raise ValueError('review result requires its originating session')
    artifact = {'version': 2, 'session_id': session_id,
                'session_identity': session.get('identity'),
                'pr_url': context['url'], 'head': context['head_sha'],
                'tree': git(context['checkout'], 'rev-parse', f"{context['head_sha']}^{{tree}}"),
                'scope': scope(prepared), 'preparation_id': prepared['preparation_id'],
                'result': result}
    validate_session_identity(artifact)
    fingerprint = digest(artifact)
    path = storage_path(directory / (fingerprint + '.json'), context)
    def immutable(old):
        if old != artifact:
            raise ValueError('review result artifact changed')
    mutate(path, artifact, immutable)
    return {'path': str(path), 'sha256': fingerprint}


def validate_session_identity(artifact):
    identity = artifact.get('session_identity')
    fields = {'pr_url', 'head', 'base', 'preparation_id', 'tree', 'scope',
              'scope_text', 'rules_text', 'reviewers'}
    if (not isinstance(identity, dict) or set(identity) != fields
            or any(not isinstance(identity[key], str) or not identity[key]
                   for key in fields - {'reviewers'})
            or not isinstance(identity['reviewers'], list)
            or not identity['reviewers']
            or any(not isinstance(reviewer, str) or not reviewer for reviewer in identity['reviewers'])
            or len(set(identity['reviewers'])) != len(identity['reviewers'])
            or any(identity[key] != artifact[key]
                   for key in ('pr_url', 'head', 'preparation_id', 'tree', 'scope'))):
        raise ValueError('review result session identity is missing or inconsistent')


def load(item):
    artifact = read(Path(item['path']), {})
    if digest(artifact) != item['sha256'] or artifact.get('version') not in {1, 2}:
        raise ValueError('review result artifact changed or has an unsupported version')
    if artifact['version'] == 2:
        validate_session_identity(artifact)
    return artifact


def failures(result):
    """Only recorded provider failures can be accepted; guard failures stay blocking."""
    if result.get('guard_failure'):
        raise ValueError('review evidence guard failed')
    failed = result.get('failed_reviewers', [])
    if (not isinstance(failed, list) or any(not isinstance(f, dict)
            or not isinstance(f.get('reviewer'), str) or not f['reviewer']
            or f.get('status') not in {'failed', 'incomplete'}
            or not isinstance(f.get('reason'), str) or not f['reason'].strip() for f in failed)):
        raise ValueError('invalid reviewer failure evidence')
    return failed


def verify(item, prepared, *, accepted_failures=(), reconciliation=None, checkout=None):
    """Validate current-tree coverage; callers separately assess remediation."""
    if reconciliation and reconciliation['binding'].get('purpose') == 'cleanup_precheck':
        raise ValueError('cleanup prechecks cannot supply final review coverage')
    artifact = load(item)
    context = read(Path(prepared['sources']['context']['path']), {})
    tree = git(checkout or context['checkout'], 'rev-parse', f"{prepared['identity']['head_sha']}^{{tree}}")
    current_scope = scope(prepared)
    if artifact['scope'] != current_scope and reconciliation is not None:
        from shared import review_reconciliation
        binding = reconciliation['binding']
        review_reconciliation.load(reconciliation['artifact'], binding)
        if (artifact['version'] != 2 or binding['review_artifact'] != {k: item[k] for k in ('path', 'sha256')}
                or binding['original_scope'] != artifact['scope']
                or binding['original_preparation_id'] != artifact['preparation_id']
                or binding['current_preparation_id'] != prepared['preparation_id']
                or binding['current_scope'] != current_scope or binding['tree'] != tree
                or binding['final_head'] != prepared['identity']['head_sha']):
            raise ValueError('reconciliation does not cover this review and preparation')
    if (artifact['pr_url'] != prepared['identity']['pr_url']
            or artifact['tree'] != tree or (artifact['scope'] != current_scope and reconciliation is None)):
        raise ValueError('final tree or scope lacks review coverage')
    result = artifact['result']
    completed = result['executed_reviewers'] + result['reused_reviewers']
    skipped = result['skipped_reviewers']
    failed = failures(result)
    failed_names = [f['reviewer'] for f in failed]
    skipped_names = [s['reviewer'] for s in skipped]
    required = set(failed_names) | (set(skipped_names) if not completed else set())
    accepted = set(accepted_failures)
    if (type(result.get('complete')) is not bool
            or (failed and result['complete'])
            or (result['complete'] and not completed)
            or (not result['complete'] and not required)
            or required - accepted
            or accepted - (set(failed_names) | set(skipped_names))
            or len(set(completed)) != len(completed)
            or len(set(failed_names)) != len(failed_names)
            or len(set(skipped_names)) != len(skipped_names)
            or any(not s.get('reason') for s in skipped)
            or set(completed) & (set(skipped_names) | set(failed_names))
            or set(skipped_names) & set(failed_names)
            or set(completed) | set(skipped_names) | set(failed_names) != set(prepared['reviewers'])):
        raise ValueError('configured-reviewer execution is incomplete')
    # Preserve the immutable result's complete:false and diagnostics.
    return {**artifact, **({'accepted_failures': sorted(required)} if required else {})}
