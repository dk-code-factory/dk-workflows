"""Required review provenance, independent of best-effort reviewer analytics."""
import hashlib
import json
import subprocess
from pathlib import Path

from shared.private_state import read, mutate
from shared.engineering_identity import engineering_id

PROTECTED_SOURCE_CATEGORIES = frozenset({'issues', 'choices', 'user_instructions', 'rules'})


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8', 'backslashreplace')).hexdigest()


def git(cwd, *args):
    return subprocess.check_output(['git', *args], cwd=cwd, text=True).strip()


def scope(prepared, *, decisions=None):
    if prepared.get('preparation_version', 1) >= 3:
        return engineering_id(prepared)
    raw = read(Path(prepared['sources']['decisions']['path']), {}) if decisions is None else decisions
    decisions = raw.get('data', raw)
    # Head changes alone are allowed for proven tree-equivalent regrouping.
    return digest({'decisions': {k: v for k, v in decisions.items() if k != 'head_sha'},
                   'analysis': prepared['analysis'], 'reviewers': prepared['reviewers'],
                   'base_sha': prepared['identity']['base_sha'],
                   'scope_sources': {key: value['sha256'] for key, value in prepared['sources'].items()
                                     if key.split('/')[0] in PROTECTED_SOURCE_CATEGORIES}})


def preparation_api():
    import importlib.util
    path = Path(__file__).resolve().parents[1] / 'dk-pr-decisions/scripts/preparation.py'
    spec = importlib.util.spec_from_file_location('coverage_preparation', path)
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    return api


def validate_current(prepared):
    api = preparation_api()
    context = api.context_for(prepared)
    raw = api.read(api.source_path(prepared, 'decisions'))
    api.validate(prepared, context, raw.get('data', raw))
    if (git(context['checkout'], 'rev-parse', 'HEAD') != context['head_sha']
            or git(context['checkout'], 'status', '--porcelain=v1')):
        raise ValueError('prepared isolated checkout changed')
    return context


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
    version = 3 if prepared.get('preparation_version', 1) >= 3 else 2
    if session.get('id') != session_id or session.get('session_version') != version - 1:
        raise ValueError('review result requires its originating session')
    artifact = {'version': version, 'session_id': session_id,
                'session_identity': session.get('identity'),
                'pr_url': context['url'], 'head': context['head_sha'],
                'tree': git(context['checkout'], 'rev-parse', f"{context['head_sha']}^{{tree}}"),
                'scope': scope(prepared), 'preparation_id': prepared['preparation_id'],
                'result': result}
    if version == 3:
        artifact['engineering_id'] = engineering_id(prepared)
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
    reference = 'engineering_id' if artifact.get('version') == 3 else 'preparation_id'
    fields = (fields - {'preparation_id'}) | {reference}
    if (not isinstance(identity, dict) or set(identity) != fields
            or any(not isinstance(identity[key], str) or not identity[key]
                   for key in fields - {'reviewers'})
            or not isinstance(identity['reviewers'], list)
            or not identity['reviewers']
            or any(not isinstance(reviewer, str) or not reviewer for reviewer in identity['reviewers'])
            or len(set(identity['reviewers'])) != len(identity['reviewers'])
            or any(identity[key] != artifact[key]
                   for key in ('pr_url', 'head', reference, 'tree', 'scope'))):
        raise ValueError('review result session identity is missing or inconsistent')


def load(item):
    artifact = read(Path(item['path']), {})
    if digest(artifact) != item['sha256'] or artifact.get('version') not in {1, 2, 3}:
        raise ValueError('review result artifact changed or has an unsupported version')
    if artifact['version'] >= 2:
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
    historical = checkout is not None and reconciliation is None
    if not historical and prepared.get('preparation_version', 1) < 3:
        raise ValueError('historical preparation cannot authorize current review coverage; refresh preparation')
    if prepared.get('preparation_version', 1) >= 3:
        if not historical:
            context = validate_current(prepared)
        else:
            # Cleanup checks original coverage from its immutable captures while
            # separately validating current preparation and the candidate tree.
            api = preparation_api()
            context = api.context_for(prepared)
            context['checkout'] = str(checkout)
            raw = api.read(api.source_path(prepared, 'decisions'))
            api.validate(prepared, context, raw.get('data', raw), check_sources=False)
    else:
        context = read(Path(prepared['sources']['context']['path']), {})
    tree = git(checkout or context['checkout'], 'rev-parse', f"{prepared['identity']['head_sha']}^{{tree}}")
    current_scope = scope(prepared)
    changed_head = artifact.get('version') == 3 and artifact['head'] != prepared['identity']['head_sha']
    if (artifact['scope'] != current_scope or changed_head) and reconciliation is not None:
        from shared import review_reconciliation
        binding = reconciliation['binding']
        review_reconciliation.load(reconciliation['artifact'], binding)
        if (artifact['version'] not in {2, 3} or binding['review_artifact'] != {k: item[k] for k in ('path', 'sha256')}
                or binding['original_scope'] != artifact['scope']
                or binding['original_preparation_id'] != artifact['preparation_id']
                or binding['current_preparation_id'] != prepared['preparation_id']
                or binding['current_scope'] != current_scope or binding['tree'] != tree
                or binding['final_head'] != prepared['identity']['head_sha']):
            raise ValueError('reconciliation does not cover this review and preparation')
    if (artifact['pr_url'] != prepared['identity']['pr_url']
            or artifact['tree'] != tree or ((artifact['scope'] != current_scope or changed_head) and reconciliation is None)):
        raise ValueError('final tree or scope lacks review coverage')
    result = artifact['result']
    if prepared.get('preparation_version', 1) >= 3:
        from shared import architecture
        api = preparation_api()
        raw_decisions = api.read(api.source_path(prepared, 'decisions'))
        architecture_result = result.get('architecture_review', {})
        if reconciliation is not None:
            architecture_result = read(Path(reconciliation['artifact']['path']), {}).get('architecture_review', {})
        architecture.verify_review(architecture_result, prepared.get('architecture'),
                                   context, raw_decisions.get('data', raw_decisions), live=not historical)
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
