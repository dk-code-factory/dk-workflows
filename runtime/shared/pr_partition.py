"""Evidence-bound PR partition candidates and recoverable publication.

The worker supplies semantic units, scope classifications and reviewed descriptions.
This module owns arithmetic, DAG order, construction, preservation and remote writes.
"""
from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid
from urllib.parse import quote

from shared import pr_size, pr_dependencies, publication, workflow_storage, pr_partition_policy
from shared.github_reads import run as github_run
from shared.pr_identity import resolve
from shared.private_state import write, atomic_text
from shared.review_loop import require_complete
from shared.repository_scope import normalize_remote

BEHAVIORS = ('evacuate_unrelated', 'split_related')


def run(cwd, *args):
    result = github_run(list(args), cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'command failed')
    return result.stdout.strip()


def git(cwd, *args):
    return run(cwd, 'git', *args)


def api(cwd, host, endpoint, *args):
    return json.loads(run(cwd, 'gh', 'api', '--hostname', host, endpoint, *args))


def metadata(cwd, pr):
    return json.loads(run(cwd, 'gh', 'pr', 'view', pr, '--json',
        'url,number,title,body,headRefOid,headRefName,headRepository,isCrossRepository,baseRefName,baseRefOid,state'))


def sources(evidence):
    if not isinstance(evidence, dict) or set(evidence) != {'scope', 'decisions', 'rules', 'dependencies'}:
        raise ValueError('evidence requires scope, decisions, rules and dependencies files')
    result = {}
    for key, filename in evidence.items():
        target = Path(filename).resolve()
        content = target.read_bytes()
        # Decisions snapshots may carry a head solely as capture provenance.
        if key == 'decisions':
            try:
                value = json.loads(content)
                if isinstance(value, dict):
                    value = {k: v for k, v in value.items() if k not in ('head', 'head_sha', 'headRefOid')}
                    content = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
            except (ValueError, UnicodeError):
                pass
        result[key] = {'path': str(target), 'sha256': hashlib.sha256(content).hexdigest()}
    return result


def snapshot(cwd, pr, evidence, *, engineering_id=None):
    meta = metadata(cwd, pr)
    host, base_repo, head_repo = resolve(meta, cwd)
    if meta['state'] != 'OPEN':
        raise ValueError('partition requires an open original PR')
    repo = api(cwd, host, 'repos/' + base_repo)
    default = repo['default_branch']
    tip = api(cwd, host, 'repos/' + base_repo + '/git/ref/heads/' + quote(default, safe=''))['object']['sha']
    # Objects must already have been fetched during preparation; checks never fetch.
    head = git(cwd, 'rev-parse', meta['headRefOid'] + '^{commit}')
    base = git(cwd, 'merge-base', meta['baseRefOid'], head)
    settings = pr_size.load(['remote:' + host + '/' + name for name in dict.fromkeys((head_repo, base_repo))])
    binding = {'pr': pr, 'tree': git(cwd, 'rev-parse', head + '^{tree}'), 'base': base,
               'engineering_id': engineering_id,
               'default_tip': tip, 'default_branch': default, 'host': host,
               'head_branch': meta['headRefName'], 'target_branch': meta['baseRefName'],
               'target_tip': meta['baseRefOid'],
               'base_repo': base_repo, 'head_repo': head_repo,
               'scope': {'title': meta['title'], 'body': meta['body']},
               'evidence': {key: item['sha256'] for key, item in sources(evidence).items()},
               'partition_policy': pr_partition_policy.binding(pr),
               'settings': settings}
    return {'key': publication.digest(binding), 'binding': binding, 'head': head, 'metadata': meta,
            'added_lines': pr_size.additions(cwd, base, head)}


def cache_path(cwd, pr):
    # Shared Git directory lets all linked worktrees use the same assessment.
    folder = Path(git(cwd, 'rev-parse', '--path-format=absolute', '--git-common-dir')) / 'pr-partitions'
    return folder / (publication.digest(pr) + '.json')


def remember(cwd, snap, state):
    target = cache_path(cwd, snap['binding']['pr'])
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = json.loads(target.read_text()) if target.exists() else {}
        value[snap['key']] = str(Path(state).resolve())
        write(target, value)


def preparation(snap, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('partition_preparation',
        Path(__file__).resolve().parents[1] / 'dk-pr-decisions/scripts/preparation.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prepared = module.gate(path, pr_url=snap['binding']['pr'], head_sha=snap['head'])
    from shared.engineering_identity import engineering_id
    return engineering_id(prepared)


def pending_states(cwd, pr):
    target = cache_path(cwd, pr)
    if not target.exists():
        return []
    return [state for state in set(json.loads(target.read_text()).values())
            if (data := load(state))['operations'] and data['status'] not in ('published', 'cancelled')]


def check(cwd, pr, evidence, *, preparation_file=None):
    policy = pr_partition_policy.load(pr)
    pending = pending_states(cwd, pr)
    if pending:
        return {'outcome': 'partition_pending', 'state': pending[0], 'partition_policy': policy,
                'behaviors': []}
    if not policy['enabled']:
        return {'outcome': 'pr_disabled', 'state': None, 'partition_policy': policy, 'behaviors': []}
    if evidence is None:
        raise ValueError('enabled PR partition checks require --evidence-file')
    snap = snapshot(cwd, pr, evidence)
    if preparation_file:
        snap['binding']['engineering_id'] = preparation(snap, preparation_file)
        snap['key'] = publication.digest(snap['binding'])
    settings = snap['binding']['settings']['pr_size']
    enabled = [key for key in BEHAVIORS if settings[key] != 'disabled']
    path = cache_path(cwd, pr)
    history = json.loads(path.read_text()) if path.exists() else {}
    cached = history.get(snap['key'])
    for retained_path in set(history.values()):
        retained = load(retained_path)
        if not cached and retained['status'] == 'cancelled':
            previous = copy.deepcopy(retained['snapshot']['binding'])
            previous['partition_policy'] = snap['binding']['partition_policy']
            previous['engineering_id'] = snap['binding']['engineering_id']
            if previous == snap['binding']:
                return {**snap, 'outcome': 'resume_required', 'state': retained_path,
                        'behaviors': enabled, 'partition_policy': policy}
    outcome = 'below_threshold' if not policy['instructions'] and snap['added_lines'] < settings['added_lines_threshold'] else (
        'disabled' if not enabled else 'assessment_required')
    if cached:
        retained = load(cached)
        if retained['snapshot']['key'] != snap['key']:
            raise ValueError('partition assessment cache mismatch')
        if retained['status'] in ('no_op', 'declined', 'published'):
            outcome = retained['status']
        else:
            outcome = 'partition_pending'
    return {**snap, 'outcome': outcome, 'state': cached, 'behaviors': enabled, 'partition_policy': policy}


def order(pieces):
    by_id = {piece['id']: piece for piece in pieces}
    if len(by_id) != len(pieces) or 'original' not in by_id:
        raise ValueError('pieces require unique IDs including original')
    done, visiting, result = set(), set(), []
    def visit(name):
        if name in visiting:
            raise ValueError('cyclic prerequisite graph')
        if name not in by_id:
            raise ValueError('unknown prerequisite: ' + name)
        if name in done:
            return
        visiting.add(name)
        dependencies = by_id[name]['depends_on']
        if not isinstance(dependencies, list) or len(set(dependencies)) != len(dependencies):
            raise ValueError('invalid prerequisite list')
        for dependency in dependencies:
            visit(dependency)
        visiting.remove(name)
        done.add(name)
        result.append(name)
    for piece in pieces:
        visit(piece['id'])
    return result


def prerequisites(pieces, name):
    by_id = {piece['id']: piece for piece in pieces}
    needed = set()
    def visit(key):
        for dependency in by_id[key]['depends_on']:
            visit(dependency)
            needed.add(dependency)
    visit(name)
    return [key for key in order(pieces) if key in needed]


def prerequisite_units(pieces, name, dependency):
    piece = next(piece for piece in pieces if piece['id'] == name)
    source = next(piece for piece in pieces if piece['id'] == dependency)
    selected = piece.get('prerequisite_units', {}).get(dependency, source['units'])
    return [unit for unit in source['units'] if unit in selected]


@contextmanager
def worktree(cwd, directory, base):
    target = directory / 'checkout'
    git(cwd, 'worktree', 'add', '--detach', str(target), base)
    try:
        yield target
    finally:
        # On conflict/failure keep the checkout for recovery; never delete work.
        if not git(target, 'status', '--porcelain=v1'):
            git(cwd, 'worktree', 'remove', str(target))


def replay(cwd, commits):
    copied = []
    for commit in commits:
        git(cwd, 'cherry-pick', '-x', commit)
        copied.append(git(cwd, 'rev-parse', 'HEAD'))
    return copied


def text(value, name):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError(name + ' must be nonempty text')
    return value


def validate_policy_assessment(snap, assessment, *, decision):
    policy = snap['binding']['partition_policy']
    if (not isinstance(assessment, dict) or set(assessment) != {
            'decision', 'policy_fingerprint', 'reason', 'evidence'}
            or assessment['decision'] != decision or assessment['policy_fingerprint'] != policy['fingerprint']):
        raise ValueError('agent assessment must bind the current PR partition instructions and decision')
    text(assessment['reason'], 'agent partition decision rationale')
    evidence = assessment['evidence']
    allowed = set(snap['binding']['evidence']) | {'partition_policy', 'diff'}
    if (not isinstance(evidence, list) or any(not isinstance(item, str) for item in evidence)
            or set(evidence) - allowed or len(evidence) != len(set(evidence))
            or 'partition_policy' not in evidence or len(evidence) < 2):
        raise ValueError('agent partition decision must cite the instructions and current scope/diff evidence')
    return assessment


def assess_policy(cwd, pr, evidence, assessment, *, preparation_file=None):
    """Retain a conditional agent decision without constructing or publishing candidates."""
    snap = snapshot(cwd, pr, evidence)
    policy = snap['binding']['partition_policy']
    if not policy['enabled'] or not policy['instructions']:
        raise ValueError('conditional assessment requires enabled PR-specific partition instructions')
    if all(snap['binding']['settings']['pr_size'][key] == 'disabled' for key in BEHAVIORS):
        raise ValueError('both configured partition behaviors are disabled')
    validate_policy_assessment(snap, assessment, decision='retain')
    if pending_states(cwd, pr):
        raise ValueError('reconcile pending partition publication before recording a new assessment')
    if preparation_file:
        snap['binding']['engineering_id'] = preparation(snap, preparation_file)
        snap['key'] = publication.digest(snap['binding'])
    index = cache_path(cwd, pr)
    cached = json.loads(index.read_text()).get(snap['key']) if index.exists() else None
    if cached:
        previous = load(cached)
        if previous.get('kind') == 'policy_assessment' and previous['assessment'] == assessment:
            verify(cached)
            return {'state': cached, 'outcome': 'no_op', 'assessment': previous['assessment'], 'reused': True}
    directory = workflow_storage.allocate(repository=cwd)
    target = directory / 'partition-assessment.json'
    data = {'schema_version': 1, 'kind': 'policy_assessment', 'cwd': str(Path(cwd).resolve()),
            'snapshot': snap, 'evidence': sources(evidence), 'assessment': assessment,
            'assessment_id': publication.digest({'snapshot': snap['key'], 'assessment': assessment}),
            'engineering_id': snap['binding']['engineering_id'], 'status': 'no_op', 'operations': {}}
    if preparation_file:
        data['preparation_file'] = str(Path(preparation_file).resolve())
    write(target, data)
    remember(cwd, snap, target)
    return {'state': str(target), 'outcome': 'no_op', 'assessment': assessment}


def validate_plan(cwd, snap, plan):
    settings = snap['binding']['settings']['pr_size']
    if all(settings[key] == 'disabled' for key in BEHAVIORS):
        raise ValueError('both configured partition behaviors are disabled')
    policy = snap['binding']['partition_policy']
    if not policy['enabled']:
        raise ValueError('partitioning is disabled for this PR by a recorded user decision')
    if policy['instructions']:
        validate_policy_assessment(snap, plan.get('policy_assessment'), decision='partition')
    if not policy['instructions'] and snap['added_lines'] < settings['added_lines_threshold']:
        raise ValueError('PR is below the configured threshold')
    units = plan.get('units')
    pieces = plan.get('pieces')
    if not isinstance(units, list) or not units or not isinstance(pieces, list):
        raise ValueError('plan requires units and pieces')
    if len({unit['id'] for unit in units}) != len(units):
        raise ValueError('duplicate unit ID')
    commits = []
    for unit in units:
        text(unit['id'], 'unit ID')
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', unit['id']):
            raise ValueError('unit IDs must be simple stable identifiers')
        text(unit['reason'], 'scope evidence')
        if unit['scope'] not in ('related', 'unrelated', 'retained'):
            raise ValueError('unit scope must be related or unrelated; ambiguous scope requires a decision')
        if unit['scope'] == 'retained' and settings['evacuate_unrelated'] != 'disabled':
            raise ValueError('unassessed retained units require disabled evacuation')
        sha = git(cwd, 'rev-parse', unit['commit'] + '^{commit}')
        if sha != unit['commit'] or len(git(cwd, 'rev-list', '--parents', '-n', '1', sha).split()) != 2:
            raise ValueError('units must be pinned non-merge commits')
        commits.append(sha)
    if len(set(commits)) != len(commits):
        raise ValueError('duplicate unit commit')
    order(pieces)
    assigned = []
    by_unit = {unit['id']: unit for unit in units}
    for piece in pieces:
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', piece['id']):
            raise ValueError('piece IDs must be simple stable identifiers')
        text(piece['reason'], 'coherent purpose')
        text(piece['title'], 'title')
        text(piece['body'], 'description')
        if '\n' in piece['title'] or '\r' in piece['title']:
            raise ValueError('title must be a single line')
        if not piece['units'] or set(piece['units']) - by_unit.keys():
            raise ValueError('piece must contain known units')
        assigned.extend(piece['units'])
        scopes = {by_unit[key]['scope'] for key in piece['units']}
        if piece['id'] == 'original':
            if 'related' not in scopes:
                raise ValueError('no in-scope primary deliverable remains; request a decision')
        else:
            if len(scopes) != 1 or 'retained' in scopes:
                raise ValueError('draft cannot mix related and unrelated units')
            behavior = 'evacuate_unrelated' if scopes == {'unrelated'} else 'split_related'
            if settings[behavior] == 'disabled':
                raise ValueError('disabled behavior cannot move its changes')
            if settings['evacuate_unrelated'] == 'disabled':
                for dependency in prerequisites(pieces, piece['id']):
                    if any(by_unit[key]['scope'] != 'related'
                           for key in prerequisite_units(pieces, piece['id'], dependency)):
                        raise ValueError('related splitting cannot copy retained unassessed/unrelated work')
            if re.search(r'\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+(?:(?:https://\S+/issues/)|(?:[\w.-]+/[\w.-]+)?#)\d+',
                         piece['body'], flags=re.I):
                raise ValueError('automatic issue-closing references belong on the original PR')
        if '{{related}}' not in piece['body']:
            raise ValueError('each description requires {{related}} cross-links')
        if '{{dependencies}}' not in piece['body']:
            raise ValueError('each description requires {{dependencies}} for generated provenance')
        if piece.get('checks'):
            if any(not isinstance(argv, list) or not argv or any(not isinstance(arg, str) for arg in argv)
                   for argv in piece['checks']):
                raise ValueError('verification checks must be argument vectors')
        else:
            text(piece.get('verification_reason'), 'local verification rationale')
        selectors = piece.get('prerequisite_units', {})
        if not isinstance(selectors, dict) or set(selectors) - set(prerequisites(pieces, piece['id'])):
            raise ValueError('prerequisite unit selectors must name declared prerequisite pieces')
        for dependency, selected in selectors.items():
            source = next(item for item in pieces if item['id'] == dependency)
            if (not isinstance(selected, list) or not selected or len(set(selected)) != len(selected)
                    or set(selected) - set(source['units'])):
                raise ValueError('prerequisite unit selector must name unique source units')
    if len(assigned) != len(set(assigned)) or set(assigned) != by_unit.keys():
        raise ValueError('every unit must belong to exactly one piece')
    assessments = plan.get('assessments', {})
    for behavior in BEHAVIORS:
        if settings[behavior] != 'disabled':
            text(assessments.get(behavior, {}).get('reason'), behavior + ' assessment evidence')
    # Ownership is explicit: own work can be split into new semantic units; a
    # foreign author's commits must remain intact, including their author date.
    originals = git(cwd, 'rev-list', snap['binding']['base'] + '..' + snap['head']).splitlines()
    author = git(cwd, 'config', 'user.email').lower()
    foreign = [sha for sha in originals if git(cwd, 'show', '-s', '--format=%ae', sha).lower() != author]
    if set(foreign) - set(commits):
        raise ValueError('foreign-author source commits require intact units; request an ownership decision')
    authors = set(git(cwd, 'show', '-s', '--format=%an <%ae> %aI', sha) for sha in originals)
    if any(git(cwd, 'show', '-s', '--format=%an <%ae> %aI', sha) not in authors for sha in commits):
        raise ValueError('unit authorship is not preserved; request an ownership decision')
    return plan


def selected_pieces(data):
    pieces = copy.deepcopy(data['plan']['pieces'])
    units = {unit['id']: unit for unit in data['plan']['units']}
    original = next(piece for piece in pieces if piece['id'] == 'original')
    removed = []
    for piece in pieces:
        if piece['id'] == 'original':
            continue
        behavior = 'evacuate_unrelated' if units[piece['units'][0]]['scope'] == 'unrelated' else 'split_related'
        if data['decisions'].get(behavior) == 'declined':
            removed.append(piece['id'])
            original['units'].extend(piece['units'])
            for check in piece.get('checks', []):
                if check not in original.setdefault('checks', []):
                    original['checks'].append(check)
    if removed:
        # Folding a declined piece back into original also folds its tests and
        # external prerequisites. Internal dependency edges disappear; dependencies
        # of still-published drafts cannot pull declined work back out implicitly.
        needed = {}
        for source in data['plan']['pieces']:
            if source['id'] not in {'original', *removed}:
                continue
            for dependency in prerequisites(data['plan']['pieces'], source['id']):
                if dependency in {'original', *removed}:
                    continue
                needed.setdefault(dependency, set()).update(prerequisite_units(
                    data['plan']['pieces'], source['id'], dependency))
        original['depends_on'] = [key for key in order(data['plan']['pieces']) if key in needed]
        original['prerequisite_units'] = {key: [unit['id'] for unit in data['plan']['units']
            if unit['id'] in selected] for key, selected in needed.items()}
    pieces = [piece for piece in pieces if piece['id'] not in removed]
    if any(set(piece['depends_on']) & set(removed) for piece in pieces):
        raise ValueError('declined work is a prerequisite of another action; revise the concrete plan')
    order(pieces)  # Folding dependent pieces can expose an unsafe original↔draft cycle.
    declined_units = set(original['units']) - set(next(
        piece['units'] for piece in data['plan']['pieces'] if piece['id'] == 'original'))
    if declined_units:
        original['oversized_reason'] = 'Retained after explicit decline of a partition action.'
        for piece in pieces:
            if (piece['id'] != 'original' and 'original' in prerequisites(pieces, piece['id'])
                    and declined_units & set(prerequisite_units(pieces, piece['id'], 'original'))):
                raise ValueError('another action would copy declined work from original; revise the plan')
    for piece in pieces:
        own = set(piece['units'])
        piece['units'] = [unit['id'] for unit in data['plan']['units'] if unit['id'] in own]
    return pieces


def build(cwd, directory, snap, plan, pieces):
    units = {unit['id']: unit['commit'] for unit in plan['units']}
    by_id = {piece['id']: piece for piece in pieces}
    base, tip = snap['binding']['base'], snap['binding']['default_tip']
    directory.mkdir(parents=True, exist_ok=False)
    built = {}
    with worktree(cwd, directory, base) as checkout:
        replay(checkout, [unit['commit'] for unit in plan['units']])
        if git(checkout, 'rev-parse', 'HEAD^{tree}') != snap['binding']['tree']:
            raise ValueError('recomposing unique units does not reproduce the original tree')
        # Confirm the same units compose after rebasing to the pinned default tip.
        git(checkout, 'reset', '--hard', tip)
        replay(checkout, [unit['commit'] for unit in plan['units']])
        recomposed_tree = git(checkout, 'rev-parse', 'HEAD^{tree}')
        for name in order(pieces):
            piece = by_id[name]
            git(checkout, 'reset', '--hard', tip)
            rows, copied_units = [], set()
            for dependency in prerequisites(pieces, name):
                source = built[dependency]
                pairs = []
                for unit_id in prerequisite_units(pieces, name, dependency):
                    sha = source['own_commits'][unit_id]
                    if unit_id in copied_units:
                        continue
                    copied = replay(checkout, [sha])[0]
                    copied_units.add(unit_id)
                    pairs.append({'source': sha, 'copied': copied})
                if pairs:
                    rows.append({'piece': dependency, 'head': source['head'], 'commits': pairs})
            own_commits = {}
            for unit_id in piece['units']:
                if unit_id in copied_units:
                    raise ValueError('own change is also a prerequisite')
                own_commits[unit_id] = replay(checkout, [units[unit_id]])[0]
            head = git(checkout, 'rev-parse', 'HEAD')
            tree = git(checkout, 'rev-parse', 'HEAD^{tree}')
            added = pr_size.additions(checkout, tip, head)
            if added >= snap['binding']['settings']['pr_size']['added_lines_threshold']:
                text(piece.get('oversized_reason'), 'no safe smaller partition rationale')
            checks = []
            for index, argv in enumerate(piece.get('checks', [])):
                result = subprocess.run(argv, cwd=checkout, capture_output=True, text=True)
                log = directory / (name + '-check-' + str(index) + '.json')
                write(log, {'argv': argv, 'head': head, 'returncode': result.returncode,
                            'stdout': result.stdout, 'stderr': result.stderr})
                if result.returncode:
                    raise ValueError('candidate verification failed; inspect ' + str(log))
                checks.append({'path': str(log), 'sha256': hashlib.sha256(log.read_bytes()).hexdigest()})
            if git(checkout, 'status', '--porcelain=v1'):
                raise ValueError('verification changed the candidate checkout; retain it for recovery')
            built[name] = {'head': head, 'tree': tree, 'own_commits': own_commits,
                           'prerequisites': rows, 'added_lines': added, 'checks': checks}
            git(cwd, 'update-ref', 'refs/pr-partition/' + directory.parent.name + '/' + name, head)
    return {'pieces': pieces, 'candidates': built, 'recomposed_tree': recomposed_tree}


def prepare(cwd, pr, evidence, plan, remote, *, preparation_file=None):
    snap = snapshot(cwd, pr, evidence)
    if preparation_file:
        snap['binding']['engineering_id'] = preparation(snap, preparation_file)
        snap['key'] = publication.digest(snap['binding'])
    validate_plan(cwd, snap, plan)
    if git(cwd, 'status', '--porcelain=v1'):
        raise ValueError('partition requires a clean checkout')
    urls = git(cwd, 'remote', 'get-url', '--push', '--all', remote).splitlines()
    identity = 'remote:' + snap['binding']['host'] + '/' + snap['binding']['head_repo']
    if len(urls) != 1 or any(normalize_remote(url) != identity for url in (
            urls[0], git(cwd, 'remote', 'get-url', remote))):
        raise ValueError('remote does not identify the owned PR head repository')
    branch = snap['metadata']['headRefName']
    if remote_head(cwd, urls[0], branch) != snap['head']:
        raise ValueError('owned remote branch does not match PR head')
    previous = check(cwd, pr, evidence, preparation_file=preparation_file)
    superseded = None
    if previous['state']:
        # Resume, including prepared/manual work, without rebuilding or prompting.
        previous_path = Path(previous['state'])
        with previous_path.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            retained = load(previous_path)
            if (previous['outcome'] == 'resume_required' and retained['status'] == 'cancelled'
                    and retained['plan_id'] != publication.digest(plan)):
                superseded = {'state': str(previous_path), 'retained_drafts': retained['drafts']}
            if retained['snapshot']['head'] != snap['head'] and not retained['operations']:
                # Same semantic binding and candidates, newly observed exact lease.
                # Foreign-author remapping was already rejected by validate_plan.
                retained.setdefault('equivalent_heads', []).append(retained['snapshot']['head'])
                retained['snapshot'] = snap
                if preparation_file:
                    retained['preparation_file'] = str(Path(preparation_file).resolve())
                git(cwd, 'update-ref', 'refs/pr-partition-recovery/' + snap['head'], snap['head'])
                write(previous_path, retained)
        if superseded is None:
            return {'state': previous['state'], 'reused': True, 'outcome': previous['outcome']}
    directory = workflow_storage.allocate(repository=cwd)
    path = directory / 'partition.json'
    git(cwd, 'update-ref', 'refs/pr-partition-recovery/' + snap['head'], snap['head'])
    for unit in plan['units']:
        git(cwd, 'update-ref', 'refs/pr-partition-units/' + publication.digest(plan) + '/' + unit['id'], unit['commit'])
    data = {'schema_version': 1, 'cwd': str(Path(cwd).resolve()), 'snapshot': snap,
            'evidence': sources(evidence), 'plan': plan, 'plan_id': publication.digest(plan),
            'remote_url': urls[0], 'status': 'prepared', 'decisions': {}, 'operations': {}, 'drafts': {}}
    branch_id = publication.digest({'snapshot': snap['key'], 'plan': data['plan_id']})[:12]
    data['branch_names'] = {piece['id']: 'dk-partition/' + str(snap['metadata']['number']) + '-' + branch_id + '-' + piece['id']
                            for piece in plan['pieces'] if piece['id'] != 'original'}
    if superseded:
        data['superseded_partition'] = superseded
    if preparation_file:
        data['engineering_id'] = snap['binding']['engineering_id']
        data['preparation_file'] = str(Path(preparation_file).resolve())
    settings = snap['binding']['settings']['pr_size']
    data['action_ids'] = {key: publication.digest({'snapshot': snap['key'], 'plan': data['plan_id'], 'action': key})
                          for key in BEHAVIORS if settings[key] != 'disabled'}
    # Persist recovery information before any candidate construction.
    write(path, data)
    built = build(cwd, directory / 'initial', snap, plan, plan['pieces'])
    data.update(built)
    # Related splitting is assessed after removing only the unrelated units.
    related = {unit['id'] for unit in plan['units'] if unit['scope'] == 'related'}
    unrelated_drafts = {key for piece in plan['pieces'] if piece['id'] != 'original'
                        for key in piece['units'] if key not in related}
    retained_ids = {unit['id'] for unit in plan['units'] if unit['id'] not in unrelated_drafts}
    for piece in plan['pieces']:
        if piece['id'] == 'original' or set(piece['units']) <= related:
            for dependency in prerequisites(plan['pieces'], piece['id']):
                retained_ids.update(prerequisite_units(plan['pieces'], piece['id'], dependency))
    retained = [unit['commit'] for unit in plan['units'] if unit['id'] in retained_ids]
    with worktree(cwd, directory, snap['binding']['default_tip']) as checkout:
        replay(checkout, retained)
        data['after_evacuation_added_lines'] = pr_size.additions(checkout, snap['binding']['default_tip'], 'HEAD')
    if (not snap['binding']['partition_policy']['instructions']
            and data['after_evacuation_added_lines'] < settings['added_lines_threshold'] and any(
            piece['id'] != 'original' and set(piece['units']) <= related for piece in plan['pieces'])):
        raise ValueError('retained scope is below threshold after evacuation; related splitting is not triggered')
    if len(plan['pieces']) == 1 and snap['metadata']['baseRefName'] == snap['binding']['default_branch']:
        data['status'] = 'no_op'
    data['candidate_hash'] = publication.digest(built)
    write(path, data)
    remember(cwd, snap, path)
    return {'state': str(path), 'action_ids': data['action_ids'], 'outcome': data['status']}


def load(path):
    path = workflow_storage.owned(path)
    data = json.loads(path.read_text())
    if data.get('kind') == 'policy_assessment':
        snap = data['snapshot']
        if (data.get('schema_version') != 1 or data.get('status') != 'no_op' or data.get('operations') != {}
                or snap['key'] != publication.digest(snap['binding'])
                or data.get('engineering_id') != snap['binding']['engineering_id']
                or data.get('assessment_id') != publication.digest({'snapshot': snap['key'], 'assessment': data.get('assessment')})):
            raise ValueError('conditional partition assessment changed')
        validate_policy_assessment(snap, data['assessment'], decision='retain')
        return data
    if (data.get('schema_version') != 1 or data.get('plan_id') != publication.digest(data.get('plan'))
            or data['snapshot']['key'] != publication.digest(data['snapshot']['binding'])
            or data.get('engineering_id') != data['snapshot']['binding'].get('engineering_id')):
        raise ValueError('partition state or plan changed')
    return data


def remote_head(cwd, url, branch):
    value = run(cwd, 'git', 'ls-remote', '--heads', url, 'refs/heads/' + branch)
    return value.split()[0] if value else None


def branch_name(data, name):
    if name in data.get('branch_names', {}):
        return data['branch_names'][name]
    return 'dk-partition/' + str(data['snapshot']['metadata']['number']) + '-' + data['plan_id'][:12] + '-' + name


def verify(path, *, live=True, refresh_policy=False):
    data = load(path)
    cwd, snap = data['cwd'], data['snapshot']
    if data.get('kind') == 'policy_assessment':
        if sources({key: item['path'] for key, item in data['evidence'].items()}) != data['evidence']:
            raise ValueError('conditional partition assessment evidence changed')
        if data.get('preparation_file') and preparation(snap, data['preparation_file']) != data['engineering_id']:
            raise ValueError('conditional partition assessment preparation changed')
        if live:
            current = snapshot(cwd, snap['binding']['pr'], {key: item['path'] for key, item in data['evidence'].items()},
                               engineering_id=snap['binding']['engineering_id'])
            if current['key'] != snap['key']:
                raise ValueError('conditional partition assessment governing inputs changed')
        return data
    built = {key: data[key] for key in ('pieces', 'candidates', 'recomposed_tree')}
    if data.get('candidate_hash') != publication.digest(built):
        raise ValueError('partition candidates changed or preparation is incomplete')
    for name, candidate in data['candidates'].items():
        if git(cwd, 'rev-parse', candidate['head'] + '^{tree}') != candidate['tree']:
            raise ValueError('candidate tree changed')
        commits = git(cwd, 'rev-list', '--reverse', snap['binding']['default_tip'] + '..' + candidate['head']).splitlines()
        prefix = [pair['copied'] for row in candidate['prerequisites'] for pair in row['commits']]
        piece = next(piece for piece in data['pieces'] if piece['id'] == name)
        if commits != prefix + [candidate['own_commits'][key] for key in piece['units']]:
            raise ValueError('candidate is not based on the pinned default tip with a frozen prefix')
        for item in candidate['checks']:
            if hashlib.sha256(Path(item['path']).read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('local verification evidence changed')
        rows = [{**row, 'url': snap['binding']['pr']} for row in candidate['prerequisites']]
        pr_dependencies.validate_prefix(cwd, snap['binding']['default_tip'], candidate['head'], rows)
    if sources({key: value['path'] for key, value in data['evidence'].items()}) != data['evidence']:
        raise ValueError('scope, Decisions, rules or dependency evidence changed')
    if data.get('preparation_file') and preparation(snap, data['preparation_file']) != data['engineering_id']:
        raise ValueError('engineering preparation changed')
    if live:
        current = snapshot(cwd, snap['binding']['pr'], {key: item['path'] for key, item in data['evidence'].items()},
                           engineering_id=snap['binding']['engineering_id'])
        if data['status'] in ('no_op', 'declined'):
            if current['key'] != snap['key']:
                raise ValueError('cached assessment governing inputs changed')
            return data  # Tree-equivalent history does not stale a semantic assessment.
        # After our own verified writes, normalize only the fields those writes own.
        normalized = copy.deepcopy(current['binding'])
        if refresh_policy:
            normalized['partition_policy'] = snap['binding']['partition_policy']
        if current['head'] == data['candidates']['original']['head']:
            normalized['tree'] = snap['binding']['tree']
            normalized['base'] = snap['binding']['base']
        elif current['head'] != snap['head']:
            raise ValueError('original PR head changed; exact lease is stale')
        original_op = data['operations'].get('edit-original')
        if original_op:
            op = publication.load(original_op, 'partition-edit')
            desired = op['request']['approved']['fields']
            if current['binding']['scope'] == {key: desired[key] for key in ('title', 'body')}:
                normalized['scope'] = snap['binding']['scope']
                if current['binding']['target_branch'] == desired['base']:
                    normalized['target_branch'] = snap['binding']['target_branch']
                    normalized['target_tip'] = snap['binding']['target_tip']
        if normalized != snap['binding']:
            raise ValueError('partition evidence, default base, ownership, settings or scope is stale')
        lease = remote_head(cwd, data['remote_url'], snap['metadata']['headRefName'])
        if lease not in (snap['head'], data['candidates']['original']['head']):
            raise ValueError('source branch lease changed')
    return data


def cancel_policy(path):
    """Honor a whole-PR opt-out without deleting drafts or discarding source work."""
    path = workflow_storage.owned(path)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load(path)
        if data.get('kind') == 'policy_assessment' or data['status'] == 'published':
            raise ValueError('only an unfinished partition can be cancelled')
        pr = data['snapshot']['binding']['pr']
        policy = pr_partition_policy.load(pr)
        if policy['enabled']:
            raise ValueError('cancellation requires a recorded whole-PR disable decision')
        if any(publication.load(value)['status'] != 'verified' for value in data['operations'].values()):
            raise ValueError('reconcile all recorded remote operations before cancelling')
        meta = metadata(data['cwd'], pr)
        host, base_repo, head_repo = resolve(meta, data['cwd'])
        snap = data['snapshot']
        if ((host, base_repo, head_repo, meta['headRefName'], meta['headRefOid']) != (
                snap['binding']['host'], snap['binding']['base_repo'], snap['binding']['head_repo'],
                snap['metadata']['headRefName'], snap['head'])
                or remote_head(data['cwd'], data['remote_url'], meta['headRefName']) != snap['head']):
            raise ValueError('original head changed; cannot cancel after source removal or a branch race')
        data['status'] = 'cancelled'
        if pr_partition_policy.binding(pr) != {key: policy[key] for key in ('enabled', 'instructions', 'fingerprint')}:
            raise ValueError('PR partition decision changed during cancellation')
        cancellation = {'policy': policy['fingerprint'], 'decision': policy['decision'], 'original_head': snap['head']}
        if cancellation not in data.setdefault('cancellations', []):
            data['cancellations'].append(cancellation)
        write(path, data)
        return {'state': str(path), 'outcome': 'cancelled', 'original_head': snap['head'],
                'retained_drafts': data['drafts'], 'partition_policy': policy}


def resume_policy(path, *, candidate_hash, assessment=None, decisions=None):
    """Rebind unchanged candidates to a new enabled preference after live reconciliation."""
    path = workflow_storage.owned(path)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load(path)
        if data.get('kind') == 'policy_assessment' or data['status'] in ('published', 'no_op', 'declined'):
            raise ValueError('resume requires an unfinished or cancelled candidate partition')
        policy = pr_partition_policy.load(data['snapshot']['binding']['pr'])
        if not policy['enabled']:
            raise ValueError('explicitly enable this PR before resuming partition publication')
        if any(publication.load(value)['status'] != 'verified' for value in data['operations'].values()):
            raise ValueError('reconcile all recorded remote operations before resuming')
        verify(path, refresh_policy=True)
        if candidate_hash != data['candidate_hash']:
            raise ValueError('resume must bind the exact verified candidate_hash')
        previous_key = data['snapshot']['key']
        snap = copy.deepcopy(data['snapshot'])
        snap['binding']['partition_policy'] = {key: policy[key] for key in ('enabled', 'instructions', 'fingerprint')}
        snap['key'] = publication.digest(snap['binding'])
        if snap['key'] == previous_key:
            raise ValueError('policy is unchanged; continue the existing verified partition instead')
        if policy['instructions']:
            validate_policy_assessment(snap, assessment, decision='partition')
        elif assessment is not None:
            validate_policy_assessment(snap, assessment, decision='partition')
        action_ids = {key: publication.digest({'snapshot': snap['key'], 'plan': data['plan_id'], 'action': key})
                      for key in data['action_ids']}
        by_unit = {unit['id']: unit for unit in data['plan']['units']}
        actual = {('evacuate_unrelated' if by_unit[piece['units'][0]]['scope'] == 'unrelated' else 'split_related')
                  for piece in data['pieces'] if piece['id'] != 'original'}
        manual = {key for key in actual if snap['binding']['settings']['pr_size'][key] == 'manual'}
        supplied = decisions or {}
        if set(supplied) - manual:
            raise ValueError('resume decisions must name current manual publication actions')
        for key, entry in supplied.items():
            if (not isinstance(entry, dict) or not {'action_id', 'decision'} <= set(entry)
                    or set(entry) - {'action_id', 'decision', 'reason', 'source'}
                    or entry['action_id'] != action_ids[key] or entry['decision'] != 'approved'):
                raise ValueError('resume requires approval of the current action; cancel if continuation is declined')
            for field in ('reason', 'source'):
                if field in entry:
                    text(entry[field], 'decision ' + field)
        missing = manual - supplied.keys()
        if missing:
            return {'state': str(path), 'outcome': 'awaiting_approval',
                    'action_ids': {key: action_ids[key] for key in sorted(missing)}}
        data.setdefault('policy_resumes', []).append({'previous_key': previous_key, 'key': snap['key'],
            'policy': policy['fingerprint'], 'assessment': assessment, 'decisions': supplied})
        data.update(snapshot=snap, action_ids=action_ids, status='prepared')
        for key in manual:
            data['decisions'][key] = 'approved'
            data.setdefault('decision_details', {}).pop(key, None)
            details = {field: supplied[key][field] for field in ('reason', 'source') if field in supplied[key]}
            if details:
                data['decision_details'][key] = details
        data.pop('reviews', None)  # Fresh self-review binds the current instructions and selected candidates.
        if pr_partition_policy.binding(snap['binding']['pr']) != snap['binding']['partition_policy']:
            raise ValueError('PR partition decision changed during resume')
        write(path, data)
        remember(data['cwd'], snap, path)
        return {'state': str(path), 'outcome': 'selection_ready', 'candidate_hash': data['candidate_hash'],
                'next': 'Review the retained candidates and descriptions, then publish using the current policy.'}


def decide(path, decisions):
    data = load(path)
    if data['operations']:
        for key, item in (decisions or {}).items():
            if (not isinstance(item, dict) or set(item) - {'action_id', 'decision', 'reason', 'source'}
                    or {field: item.get(field) for field in ('action_id', 'decision')} != {
                        'action_id': data['action_ids'].get(key), 'decision': data['decisions'].get(key)}
                    or any(item[field] != data.get('decision_details', {}).get(key, {}).get(field)
                           for field in ('reason', 'source') if field in item)):
                raise ValueError('publication already started; decisions are frozen')
        return data
    settings = data['snapshot']['binding']['settings']['pr_size']
    by_unit = {unit['id']: unit for unit in data['plan']['units']}
    actions = {('evacuate_unrelated' if by_unit[piece['units'][0]]['scope'] == 'unrelated' else 'split_related')
               for piece in data['plan']['pieces'] if piece['id'] != 'original'}
    supplied = decisions or {}
    if set(supplied) - actions:
        raise ValueError('decision names an action absent from this plan')
    for key in actions:
        entry = supplied.get(key)
        if entry:
            if (not isinstance(entry, dict) or not {'action_id', 'decision'} <= set(entry)
                    or set(entry) - {'action_id', 'decision', 'reason', 'source'}
                    or entry['action_id'] != data['action_ids'][key]
                    or entry['decision'] not in ('approved', 'declined')):
                raise ValueError('approval/decline must bind the exact proposed action ID')
            details = {field: text(entry[field], 'decision ' + field) for field in ('reason', 'source') if field in entry}
            previous = data['decisions'].get(key)
            if previous and previous != entry['decision']:
                raise ValueError('retained action decision changed; prepare a new reviewed plan')
            data['decisions'][key] = entry['decision']
            if details:
                data.setdefault('decision_details', {})[key] = details
        elif settings[key] == 'automatic':
            data['decisions'].setdefault(key, 'approved')
    # Save each decline immediately: retries cannot turn it into another action.
    write(Path(path), data)
    for key, decision in data['decisions'].items():
        if decision == 'declined':
            details = data.get('decision_details', {}).get(key, {})
            pr_partition_policy.decline(data['snapshot']['binding']['pr'], key, data['action_ids'][key],
                reason=details.get('reason', 'Declined the proposed ' + key + ' action.'),
                source=details.get('source', json.dumps({'action_id': data['action_ids'][key], 'decision': decision}, sort_keys=True)),
                context={'state': str(Path(path).resolve()), 'snapshot_key': data['snapshot']['key'],
                         'head': data['snapshot']['head'], 'tree': data['snapshot']['binding']['tree']})
    if any(key not in data['decisions'] for key in actions):
        return {**data, 'awaiting_approval': [key for key in actions if key not in data['decisions']]}
    pieces = selected_pieces(data)
    if pieces != data['pieces']:
        built = build(data['cwd'], Path(path).parent / ('selected-' + uuid.uuid4().hex),
                      data['snapshot'], data['plan'], pieces)
        data.update(built)
        data['candidate_hash'] = publication.digest(built)
    if len(pieces) == 1 and 'declined' in data['decisions'].values():
        data['status'] = 'declined'
    write(Path(path), data)
    return data


def find_draft(data, name):
    binding = data['snapshot']['binding']
    branch = branch_name(data, name)
    owner = binding['head_repo'].split('/')[0]
    endpoint = ('repos/' + binding['base_repo'] + '/pulls?state=all&per_page=100&head=' +
                quote(owner + ':' + branch, safe='') + '&base=' + quote(binding['default_branch'], safe=''))
    pages = api(data['cwd'], binding['host'], endpoint, '--paginate', '--slurp')
    values = [pr for page in pages for pr in page]
    values = [pr for pr in values if pr['head']['ref'] == branch and
              (pr['head'].get('repo') or {}).get('full_name', '').lower() == binding['head_repo']]
    if len(values) != 1:
        raise ValueError('draft creation is absent or ambiguous; reconcile without repeating creation')
    pr = values[0]
    if (pr['state'] != 'open' or not pr['draft'] or pr['head']['sha'] != data['candidates'][name]['head']
            or pr['base']['ref'] != binding['default_branch']
            or pr['base']['repo']['full_name'].lower() != binding['base_repo']):
        raise ValueError('partition draft ownership, head or base changed')
    return pr


def body(data, name, *, complete):
    piece = next(piece for piece in data['pieces'] if piece['id'] == name)
    urls = {'original': data['snapshot']['binding']['pr'], **data['drafts']}
    related = 'Related PRs: ' + ', '.join(urls[key] for key in order(data['pieces']) if key != name and key in urls)
    rows = [{**row, 'url': urls[row['piece']]} for row in data['candidates'][name]['prerequisites']]
    output = piece['body'].replace('{{dependencies}}', pr_dependencies.table(rows)).replace('{{related}}', related)
    if '{{' in output or '}}' in output:
        raise ValueError('unresolved description placeholders')
    if name != 'original' and data['snapshot']['binding']['pr'] not in output:
        raise ValueError('draft must link to the original PR')
    pr_dependencies.validate_prefix(data['cwd'], data['snapshot']['binding']['default_tip'],
                                    data['candidates'][name]['head'], pr_dependencies.parse(output))
    if complete and len(urls) != len(data['pieces']):
        raise ValueError('all draft URLs must be verified before final description edits')
    return output


def operation(path, key, kind, expected, approved, check_ready, send, observe, *, reconcile=False):
    data = load(path)
    if key not in data['operations']:
        if reconcile:
            return None  # Reconciliation is strictly read-only, including the journal.
        value = publication.prepare(kind, data['snapshot']['binding']['pr'], expected, approved,
            [sys.executable, Path(__file__).resolve().parents[1] / 'dk-pr-split-commits/scripts/partition_prs.py',
             '--cwd', data['cwd'], 'reconcile', '--state', str(path)], directory=Path(path).parent)
        data['operations'][key] = value['operation']
        write(Path(path), data)
    op_path = data['operations'][key]
    def readiness(value, verified):
        verify(path)
        check_ready(value, verified)
    return publication.execute(op_path, kind, check=readiness, send=send, observe=observe, reconcile=reconcile)


def request_json(path, key, host, endpoint, method, fields):
    payload = Path(path).parent / (key + '-payload.json')
    write(payload, fields)
    return api(load(path)['cwd'], host, endpoint, '--method', method, '--input', str(payload))


def reconcile_operations(path, selected=None):
    """Observe dispatched writes even when new governing evidence blocks continuation."""
    data = load(path)
    if selected and selected not in data['operations'].values():
        raise ValueError('reconciliation operation is not in the retained partition journal')
    pending = [(key, operation_path) for key, operation_path in data['operations'].items()
               if (operation_path == selected if selected else
                   publication.load(operation_path)['status'] == 'pending')]
    results, failures = [], []
    for key, operation_path in pending:
        kind = publication.load(operation_path)['request']['kind']
        def observe(value):
            approved = value['request']['approved']
            current = load(path)
            if kind == 'partition-branch':
                actual = remote_head(current['cwd'], current['remote_url'], approved['branch'])
                if actual != approved['head']:
                    raise ValueError('branch publication remains uncertain; never repeat the push')
                return {'branch': approved['branch'], 'head': actual}
            if kind == 'partition-create':
                name = key.removeprefix('create-')
                pr = find_draft(current, name)
                current['drafts'][name] = pr['html_url']
                write(Path(path), current)
                return {'url': pr['html_url'], 'head': pr['head']['sha']}
            if kind != 'partition-edit':
                raise ValueError('unknown partition journal operation')
            name = key.removeprefix('edit-')
            url = current['snapshot']['binding']['pr'] if name == 'original' else current['drafts'][name]
            fields = approved['fields']
            wanted = {'title': fields['title'], 'body': fields['body'], 'baseRefName': fields['base'],
                      'headRefOid': current['candidates'][name]['head']}
            live = metadata(current['cwd'], url)
            if {field: live[field] for field in wanted} != wanted:
                raise ValueError('description publication remains uncertain')
            return {'url': url, **wanted}
        try:
            results.append(publication.execute(operation_path, kind, check=lambda *_: verify(path),
                send=lambda *_: (_ for _ in ()).throw(ValueError('reconciliation cannot dispatch')),
                observe=observe, reconcile=True))
        except Exception as exc:
            failures.append(exc)
    if failures:
        raise failures[0]
    return {'state': str(path), 'outcome': 'reconciled', 'operations': results,
            'next': 'Verify current readiness, then publish to continue the retained sequence.'}


def publish(path, decisions=None, review_state=None, description_review_state=None, *, candidate_hash=None,
            reconcile=False, operation_path=None):
    path = workflow_storage.owned(path)
    # One owner serializes candidate selection and journal extension. The durable
    # per-operation intent additionally protects interruptions inside a write.
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if reconcile:
            return reconcile_operations(path, str(Path(operation_path).resolve()) if operation_path else None)
        data = verify(path)
        if data['status'] in ('no_op', 'declined'):
            return {'state': str(path), 'outcome': data['status']}
        if not reconcile:
            previous_candidates = data['candidate_hash']
            data = decide(path, decisions)
            if data.get('awaiting_approval'):
                return {'state': str(path), 'outcome': 'awaiting_approval',
                        'action_ids': {key: data['action_ids'][key] for key in data['awaiting_approval']}}
            if data['status'] == 'declined':
                return {'state': str(path), 'outcome': 'declined'}
            if previous_candidates != data['candidate_hash']:
                return {'state': str(path), 'outcome': 'selection_ready',
                        'candidate_hash': data['candidate_hash'], 'next': 'Review the selected candidates and descriptions before publication.'}
            if 'reviews' not in data:
                if candidate_hash != data['candidate_hash']:
                    raise ValueError('review approval must bind the current candidate_hash')
                require_complete(review_state, 'split')
                require_complete(description_review_state, 'dk-pr-description')
                data['reviews'] = {'split': review_state, 'description': description_review_state,
                                   'candidate_hash': data['candidate_hash']}
                write(path, data)
        if 'reviews' not in data:
            return {'state': str(path), 'outcome': 'prepared'}
        require_complete(data['reviews']['split'], 'split')
        require_complete(data['reviews']['description'], 'dk-pr-description')
        if data['reviews']['candidate_hash'] != data['candidate_hash']:
            raise ValueError('reviewed candidates changed')
        cwd, snap = data['cwd'], data['snapshot']
        binding = snap['binding']
        def push_piece(name):
            desired = data['candidates'][name]['head']
            branch = snap['metadata']['headRefName'] if name == 'original' else branch_name(data, name)
            expected = snap['head'] if name == 'original' else None
            def ready(value, verified):
                actual = remote_head(cwd, data['remote_url'], branch)
                if actual != (desired if verified else expected):
                    raise ValueError('branch changed; refusing to refresh its exact lease')
            def send(value):
                return run(cwd, 'git', 'push', '--force-with-lease=refs/heads/' + branch + ':' + (expected or ''),
                           data['remote_url'], desired + ':refs/heads/' + branch)
            def observe(value):
                actual = remote_head(cwd, data['remote_url'], branch)
                if actual != desired:
                    raise ValueError('branch publication not observed; keep journal and recovery refs')
                return {'branch': branch, 'head': actual}
            return operation(path, 'push-' + name, 'partition-branch', {'head': expected},
                             {'head': desired, 'branch': branch}, ready, send, observe, reconcile=reconcile)
        # Publish and observe every branch before creating drafts, and verify all
        # drafts/descriptions before removing anything from the original branch.
        for name in order(data['pieces']):
            if name != 'original':
                push_piece(name)
        for name in order(data['pieces']):
            if name == 'original':
                continue
            key = 'create-' + name
            fields = {'title': next(p['title'] for p in data['pieces'] if p['id'] == name),
                      'body': body(data, name, complete=False), 'draft': True,
                      'head': binding['head_repo'].split('/')[0] + ':' + branch_name(data, name),
                      'base': binding['default_branch']}
            if binding['head_repo'] != binding['base_repo']:
                # Required by GitHub when a fork and its base share an organization.
                fields['head_repo'] = binding['head_repo'].split('/')[1]
            def ready(value, verified, name=name):
                if remote_head(cwd, data['remote_url'], branch_name(data, name)) != data['candidates'][name]['head']:
                    raise ValueError('draft branch changed')
                if verified:
                    find_draft(load(path), name)
                else:
                    # Any existing PR prevents an unjournaled duplicate creation.
                    branch = branch_name(data, name)
                    pages = api(cwd, binding['host'], 'repos/' + binding['base_repo'] + '/pulls?state=all&head=' +
                                quote(binding['head_repo'].split('/')[0] + ':' + branch, safe=''), '--paginate', '--slurp')
                    if any(page for page in pages):
                        raise ValueError('branch already has a PR; reconcile the retained creation')
            def send(value, key=key):
                return request_json(path, key, binding['host'], 'repos/' + binding['base_repo'] + '/pulls',
                                    'POST', value['request']['approved']['fields'])
            def observe(value, name=name):
                pr = find_draft(load(path), name)
                return {'url': pr['html_url'], 'head': pr['head']['sha']}
            result = operation(path, key, 'partition-create', {}, {'fields': fields}, ready, send, observe,
                               reconcile=reconcile)
            if result:
                data = load(path)
                data['drafts'][name] = result['receipt']['url']
                write(path, data)
            elif reconcile:
                return {'state': str(path), 'outcome': 'prepared', 'next': key}
        # Base/title/body updates use the shared durable publication guard and the
        # same completed description review contract as dk-pr-description.
        def edit_piece(name):
            url = binding['pr'] if name == 'original' else data['drafts'][name]
            piece = next(p for p in data['pieces'] if p['id'] == name)
            fields = {'title': piece['title'], 'body': body(data, name, complete=True),
                      'base': binding['default_branch']}
            key = 'edit-' + name
            expected = metadata(cwd, url)
            expected = {key: expected[key] for key in ('title', 'body', 'headRefOid', 'baseRefName')}
            desired_head = data['candidates'][name]['head']
            def ready(value, verified):
                require_complete(load(path)['reviews']['description'], 'dk-pr-description')
                live = metadata(cwd, url)
                wanted = ({'title': fields['title'], 'body': fields['body'], 'headRefOid': desired_head,
                           'baseRefName': fields['base']} if verified else value['request']['expected'])
                if {key: live[key] for key in wanted} != wanted:
                    raise ValueError('description head, base, title or body changed')
            def send(value):
                endpoint = 'repos/' + binding['base_repo'] + '/pulls/' + url.rsplit('/', 1)[1]
                return request_json(path, key, binding['host'], endpoint, 'PATCH', value['request']['approved']['fields'])
            def observe(value):
                live = metadata(cwd, url)
                wanted = {'title': fields['title'], 'body': fields['body'], 'headRefOid': desired_head,
                          'baseRefName': fields['base']}
                if {key: live[key] for key in wanted} != wanted:
                    raise ValueError('description publication was not observed')
                return {'url': url, **wanted}
            return operation(path, key, 'partition-edit', expected, {'fields': fields}, ready, send, observe,
                             reconcile=reconcile)
        for name in order(data['pieces']):
            if name != 'original':
                edit_piece(name)
        # Freshly observe all draft branches and PRs immediately before source removal.
        for name in data['drafts']:
            draft = find_draft(load(path), name)
            piece = next(piece for piece in data['pieces'] if piece['id'] == name)
            if draft['title'] != piece['title'] or draft['body'] != body(data, name, complete=True):
                raise ValueError('draft description changed before source removal')
        push_piece('original')
        edit_piece('original')
        data = load(path)
        required = ['push-original', 'edit-original'] + [prefix + p['id'] for p in data['pieces']
                    if p['id'] != 'original' for prefix in ('push-', 'create-', 'edit-')]
        if any(key not in data['operations'] or publication.load(data['operations'][key])['status'] != 'verified'
               for key in required):
            return {'state': str(path), 'outcome': 'partition_pending'}
        verify(path)
        data['status'] = 'published'
        write(path, data)
        # Governing scope changed: callers must refresh preparation and run this
        # check again on the retained head, within their existing review budget.
        return {'state': str(path), 'outcome': 'published', 'original_head': data['candidates']['original']['head'],
                'drafts': data['drafts'], 'refresh_preparation': True}
