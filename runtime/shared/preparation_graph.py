"""Deterministic preparation fingerprints, conclusion graph and reassessment merge.

No language parsing or semantic irrelevance heuristics live here. Unknown evidence
is opaque; a missing watch always requires an explicit model impact decision.
"""
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import uuid

POLICY = 2
GOVERNING = {'issues', 'choices', 'user_instructions', 'rules', 'questions', 'decisions',
             'reviewers', 'reviewer-source'}
GUIDANCE = {'reviewer_addenda', 'worker_instructions', 'completion_criteria'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8', 'backslashreplace')).hexdigest()


def decode(content):
    try:
        return json.loads(content)
    except (ValueError, UnicodeError):
        return None


def entries(checkout, head):
    raw = subprocess.check_output(['git', 'ls-tree', '-rz', '--full-tree', head], cwd=checkout)
    result = {}
    for record in raw.split(b'\0'):
        if record:
            header, path = record.split(b'\t', 1)
            mode, kind, oid = header.decode('ascii').split()
            result[path.decode('utf-8', 'surrogateescape')] = [mode, kind, oid]
    return result


def relative(path, checkout):
    # Do not resolve symlinks: the Git entry, including its mode, is the identity.
    try:
        return Path(path).absolute().relative_to(Path(checkout).absolute()).as_posix()
    except ValueError:
        return None


def source_identity(key, source, checkout, content):
    if source.get('mode') == 'capture' and source.get('origin_identity'):
        return source['origin_identity']
    category = key.split('/')[0]
    if '/' not in key or category in {'prior_preparation', 'reviewer-source'}:
        return key
    repo_path = relative(source['path'], checkout)
    if repo_path is not None:
        return category + ':repo:' + repo_path
    value = decode(content)
    if category == 'reviewers' and isinstance(value, dict) and isinstance(value.get('reviewer'), str):
        return category + ':' + value['reviewer']
    if category == 'feedback' and isinstance(value, dict) and value.get('complete') is True:
        pr = value.get('pullRequest', {})
        inline = type(value.get('schemaVersion')) is int and value['schemaVersion'] == 1
        if isinstance(pr, dict) and isinstance(pr.get('url'), str) and (inline or 'schemaVersion' not in value):
            shape = 'threads' if inline else 'conversation'
            return category + ':github:' + pr['url'] + ':' + shape
    # Explicit stable identities permit relocation without conflating opaque files.
    if (isinstance(value, dict) and type(value.get('evidence_version')) is int
            and value['evidence_version'] == 1 and isinstance(value.get('source_id'), str)):
        return category + ':id:' + value['source_id']
    return category + ':path:' + str(Path(source['path']).absolute())


def operational_unknown(value):
    """Only declared counters/status/receipts are operational; new fields govern."""
    result = {}
    counters = {'cycles', 'cycle_retries', 'repairs', 'reruns'}
    statuses = {'pending', 'queued', 'in_progress', 'success', 'failure', 'neutral',
                'cancelled', 'skipped', 'timed_out'}
    receipt_fields = {'url', 'head_sha', 'sha256', 'outcome', 'timestamp', 'artifact', 'kind'}
    for key, item in value.items():
        if key in counters and type(item) is int and item >= 0:
            continue
        if key == 'publication_receipts' and isinstance(item, list) and all(
                isinstance(v, dict) and set(v).issubset(receipt_fields) and
                all(isinstance(field, str) for field in v.values()) for v in item):
            continue
        if key == 'status' and isinstance(item, str) and item in statuses:
            continue
        if key in {'head_sha', 'run_id', 'checked_at'} and isinstance(item, str):
            continue
        result[key] = item
    return result


def semantic(key, content):
    """Normalize only declared schemas/fields; unknown fields remain governing."""
    category = key.split('/')[0]
    value = decode(content)
    if key == 'manifest':
        return {'mode': value['mode'], **{k: v for k, v in value.items()
                                         if k not in {'mode', 'context', 'decisions', 'evidence',
                                                      'source_modes', 'source_origins'}}}
    if key == 'context':
        return {k: v for k, v in value.items()
                if k not in {'head_sha', 'checkout', 'repository', 'patch', 'metadata',
                             'local_head', 'worktree_clean', 'local_matches_pr', 'workflow_dir', 'context_file'}}
    if key == 'decisions':
        if 'data' in value:
            return {**value, 'data': {k: v for k, v in value['data'].items() if k != 'head_sha'}}
        return {k: v for k, v in value.items() if k != 'head_sha'}
    if key == 'metadata' and isinstance(value, dict):
        return {k: v for k, v in value.items() if k != 'headRefOid'}
    if key == 'patch':
        # Full Git entries are compared independently, including modes and objects.
        return {'derived_from_git': True}
    if category == 'prior_preparation':
        # Verified separately. Growing lineage must not invalidate itself.
        return {'lineage': True}
    if isinstance(value, dict):
        if category == 'feedback' and 'complete' in value:
            return feedback(value)
        if type(value.get('preparation_history_version')) is int and value['preparation_history_version'] == 1:
            if not isinstance(value.get('governing'), dict) or not isinstance(value.get('operational'), dict):
                raise ValueError('typed preparation history requires governing and operational objects')
            return {'fields': {k: v for k, v in value.items() if k != 'operational'},
                    'operational_unknown': operational_unknown(value.get('operational', {}))}
        if type(value.get('evidence_version')) is int and value['evidence_version'] == 1:
            # Only this explicitly typed payload has a non-governing operational section.
            if not isinstance(value.get('governing'), dict) or not isinstance(value.get('operational', {}), dict):
                raise ValueError('typed evidence requires governing and operational objects')
            return {'fields': {k: v for k, v in value.items() if k != 'operational'},
                    'operational_unknown': operational_unknown(value.get('operational', {}))}
        if category == 'reviewers':
            from shared.engineering_identity import descriptor
            return descriptor(value)
    return {'opaque_sha256': hashlib.sha256(content).hexdigest()}


def feedback(value, *, pr_url=None, head_sha=None):
    """Normalize supported GitHub collections by stable record ID, keeping all text.

    Thread resolution alone is operational. Every other record field, including unknown
    fields, authorship, supersession and ordering within comments, is significant.
    """
    if not isinstance(value, dict) or value.get('complete') is not True:
        raise ValueError('complete current feedback required')
    pr = value.get('pullRequest')
    if not isinstance(pr, dict) or any(not isinstance(pr.get(k), str) or not pr[k]
                                       for k in ('url', 'headRefOid')):
        raise ValueError('feedback PR and pinned head required')
    if (pr_url is not None and pr['url'] != pr_url or
            head_sha is not None and pr['headRefOid'] != head_sha):
        raise ValueError('feedback PR or pinned head differs')
    inline = type(value.get('schemaVersion')) is int and value['schemaVersion'] == 1
    if inline:
        required = {'threads', 'items', 'threadsSha256', 'itemsSha256'}
        collections = (('threads', 'id'), ('items', 'reference'))
        hashes = {'threads': 'threadsSha256', 'items': 'itemsSha256'}
    elif 'schemaVersion' not in value:
        required = {'reviews', 'comments', 'items', 'sha256'}
        collections = (('reviews', 'id'), ('comments', 'id'), ('items', 'reference'))
        hashes = {}
    else:
        raise ValueError('unsupported complete feedback schema')
    if not required.issubset(value):
        raise ValueError('complete feedback collections and fingerprints required')
    if 'sha256' in required and value['sha256'] != digest({k: v for k, v in value.items() if k != 'sha256'}):
        raise ValueError('feedback fingerprint mismatch: conversation')
    def original(record, numeric_id=False):
        if (not isinstance(record, dict) or not isinstance(record.get('reference'), str) or not record['reference']
                or not isinstance(record.get('text'), str)
                or numeric_id and type(record.get('id')) is not int):
            raise ValueError('original feedback record requires identity and string text')
    result = copy.deepcopy(value)
    for collection, identity in collections:
        records = result[collection]
        if not isinstance(records, list) or any(not isinstance(v, dict) or identity not in v for v in records):
            raise ValueError('invalid complete feedback collection: ' + collection)
        count = {'threads': 'threadCount', 'reviews': 'reviewCount', 'comments': 'commentCount', 'items': 'itemCount'}[collection]
        if value.get(count) != len(records):
            raise ValueError('feedback count mismatch: ' + collection)
        hash_field = hashes.get(collection)
        if hash_field and digest(records) != value[hash_field]:
            raise ValueError('feedback fingerprint mismatch: ' + collection)
        ids = [str(v[identity]) for v in records]
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate feedback identity in ' + collection)
        normalized = []
        for record in records:
            if collection == 'threads':
                if not isinstance(record['id'], str) or not record['id']:
                    raise ValueError('invalid feedback thread identity')
            else:
                original(record, numeric_id=not inline)
            if inline:
                comments = record.get('comments' if collection == 'threads' else 'conversation')
                if not isinstance(comments, list) or not comments:
                    raise ValueError('complete feedback comment records required')
                for comment in comments:
                    original(comment)
            record = dict(record)
            if collection == 'threads':
                record.pop('isResolved', None)
            normalized.append(record)
        result[collection] = sorted(normalized, key=lambda v: str(v[identity]))
    if inline:
        # items is a derived unresolved-only view. Normalize it away only after
        # proving that every item is exactly the supported projection of threads.
        expected = []
        for thread in value['threads']:
            if type(thread.get('isResolved')) is not bool or not isinstance(thread.get('comments'), list) or not thread['comments']:
                raise ValueError('complete thread resolution state and comments required')
            if thread['isResolved']:
                continue
            root = thread['comments'][0]
            expected.append({'reference': thread['id'], 'commentReference': root.get('reference'),
                'url': root.get('url'), 'path': thread.get('path'), 'line': thread.get('line'),
                'originalLine': thread.get('originalLine'), 'isOutdated': bool(thread.get('isOutdated')),
                'text': root.get('text', ''), 'conversation': thread['comments']})
        if sorted(expected, key=lambda v: v['reference']) == result['items']:
            result.pop('items')
            result.pop('itemCount', None)
    for field in hashes.values():
        result.pop(field)  # Validated hashes of the retained known collections.
    if not inline:
        result.pop('sha256')
    if isinstance(result.get('pullRequest'), dict):
        result['pullRequest'].pop('headRefOid', None)
    return result


def inputs(context, sources):
    result = {}
    for key, source in sources.items():
        content = Path(source['path']).read_bytes()
        if hashlib.sha256(content).hexdigest() != source['sha256']:
            raise ValueError('preparation source changed during collection')
        result[key] = {'identity': source_identity(key, source, context['checkout'], content),
                       'governing': digest(semantic(key, content))}
    return {'policy': POLICY, 'tree': entries(context['checkout'], context['head_sha']), 'sources': result}


def rebind(sources, current, parent):
    """Keep evidence citation keys stable when collection order changes."""
    old_by_id = {}
    for key, value in parent['inputs']['sources'].items():
        old_by_id.setdefault(value['identity'], []).append(key)
    mapping, used = {}, set()
    # Singleton identities never move. Repeated identical source paths retain slots.
    for key, value in current['sources'].items():
        candidates = old_by_id.get(value['identity'], [])
        target = next((k for k in candidates if k not in used), None)
        if target:
            mapping[key] = target
            used.add(target)
    reserved = used | set(parent['sources'])
    for key in sources:
        if key in mapping:
            continue
        target = key
        if target in reserved:
            category = key.split('/')[0]
            n = 0
            while f'{category}/{n}' in reserved:
                n += 1
            target = f'{category}/{n}'
        mapping[key] = target
        reserved.add(target)
    return ({mapping[k]: v for k, v in sources.items()},
            {**current, 'sources': {mapping[k]: v for k, v in current['sources'].items()}})


def changes(parent, current):
    old = parent['inputs']
    paths = sorted(path for path in old['tree'].keys() | current['tree'].keys()
                   if old['tree'].get(path) != current['tree'].get(path))
    sources = sorted(key for key in old['sources'].keys() | current['sources'].keys()
                     if not key.startswith('prior_preparation/') and old['sources'].get(key) != current['sources'].get(key))
    return paths, sources


def escape(value):
    return value.replace('~', '~0').replace('/', '~1')


def conclusions(analysis):
    result = {}
    for dimension, values in analysis['boundaries'].items():
        for n, value in enumerate(values):
            result[f'/boundaries/{dimension}/{n}'] = value
    for name, value in analysis['placement'].items():
        result['/placement/' + name] = value
    for field in ('test_strategy', 'questions'):
        for n, value in enumerate(analysis[field]):
            result[f'/{field}/{n}'] = value
    for name, value in analysis['reviewer_addenda'].items():
        result['/reviewer_addenda/' + escape(name)] = value
    for field in ('worker_instructions', 'completion_criteria'):
        result['/' + field] = analysis[field]
    result['/sync_required'] = {k: analysis[k] for k in ('sync_required', 'sync_reason')}
    return result


def watch_path(value):
    if not isinstance(value, dict) or set(value) != {'path', 'kind'} or value['kind'] not in {'file', 'subtree'}:
        raise ValueError('watch requires path and file/subtree kind')
    path = value['path']
    if not isinstance(path, str) or not path or '\x00' in path or '\\' in path or any(c in path for c in '*?[]'):
        raise ValueError('invalid watch path; globs are unsupported')
    try:
        canonical = path.encode('utf-8', 'surrogateescape').decode('utf-8', 'surrogateescape')
    except UnicodeError as exc:
        raise ValueError('invalid watch path encoding') from exc
    if canonical != path:
        raise ValueError('invalid watch path encoding')
    if path == '.':
        if value['kind'] != 'subtree':
            raise ValueError('repository root requires a subtree watch')
        return
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or '..' in parsed.parts or parsed.as_posix() != path:
        raise ValueError('watch paths must be normalized repository-relative paths')


def matches(watch, path):
    return watch['path'] == path or (watch['kind'] == 'subtree' and
                                   (watch['path'] == '.' or path.startswith(watch['path'] + '/')))


def decision_ids(decisions):
    return {v['id'] for v in decisions['decisions'] if isinstance(v, dict) and isinstance(v.get('id'), str)}


def mandatory(graph, sources):
    governing = [key for key, node in graph.items() if node['location'].split('/')[1] not in GUIDANCE]
    for key, node in graph.items():
        if node['location'].split('/')[1] in GUIDANCE:
            node['depends_on'] = sorted(set(node['depends_on']) | set(governing))
        else:
            node['sources'] = sorted(set(node['sources']) | {s for s in sources if s.split('/')[0] in GOVERNING})


def validate_graph(graph, analysis, sources, decisions, retired=(), *, require_mandatory=True):
    if not isinstance(graph, dict) or not graph or not isinstance(retired, list):
        raise ValueError('complete conclusion graph and retired IDs required')
    if len(set(retired)) != len(retired) or set(graph) & set(retired):
        raise ValueError('retired conclusion ID reused')
    locations = []
    for key, node in graph.items():
        if not isinstance(key, str) or not key.startswith('c-') or not isinstance(node, dict):
            raise ValueError('invalid runtime conclusion ID')
        if set(node) != {'location', 'depends_on', 'paths', 'sources', 'decisions'}:
            raise ValueError('conclusion graph node fields are incomplete')
        locations.append(node['location'])
        for field in ('depends_on', 'sources', 'decisions'):
            if not isinstance(node[field], list) or any(not isinstance(v, str) for v in node[field]) or len(set(node[field])) != len(node[field]):
                raise ValueError('invalid graph references: ' + field)
        if set(node['depends_on']) - graph.keys():
            raise ValueError('dangling conclusion dependency')
        if set(node['sources']) - sources.keys():
            raise ValueError('unknown watched evidence source')
        if set(node['decisions']) - decision_ids(decisions):
            raise ValueError('unknown governing Decision ID')
        if not isinstance(node['paths'], list):
            raise ValueError('repository watches must be an array')
        for watch in node['paths']:
            watch_path(watch)
        if not node['paths'] and not node['sources'] and not node['depends_on']:
            raise ValueError('conclusion has no supporting watches')
    if len(set(locations)) != len(locations) or set(locations) != set(conclusions(analysis)):
        raise ValueError('graph must cover every analysis conclusion exactly once')
    if require_mandatory:
        required = copy.deepcopy(graph)
        mandatory(required, sources)
        if any(set(required[k]['depends_on']) != set(v['depends_on']) or
               set(required[k]['sources']) != set(v['sources']) for k, v in graph.items()):
            raise ValueError('mandatory governing dependencies missing')


def initial(response, sources, decisions):
    if not isinstance(response, dict) or set(response) != {'analysis', 'graph'}:
        raise ValueError('initial preparation requires analysis and graph')
    analysis, proposed = response['analysis'], response['graph']
    if not isinstance(proposed, list) or any(not isinstance(v, dict) for v in proposed):
        raise ValueError('initial graph must be an array')
    ids = {v.get('location'): 'c-' + uuid.uuid4().hex for v in proposed}
    if len(ids) != len(proposed):
        raise ValueError('duplicate analysis location')
    graph = {}
    for node in proposed:
        node = copy.deepcopy(node)
        try:
            node['depends_on'] = [ids[v] for v in node['depends_on']]
            graph[ids[node['location']]] = node
        except (KeyError, TypeError) as exc:
            raise ValueError('initial graph has missing or dangling dependencies') from exc
    mandatory(graph, sources)
    validate_graph(graph, analysis, sources, decisions, [])
    return analysis, graph


def closure(graph, selected):
    visited = set(selected)
    while True:
        more = {key for key, node in graph.items() if set(node['depends_on']) & visited} - visited
        if not more:
            return visited
        visited.update(more)


def affected(graph, paths, sources):
    selected, mapped_paths, mapped_sources = set(), set(), set()
    if any(source.split('/')[0] in GOVERNING for source in sources):
        selected.update(key for key, node in graph.items() if node['location'].split('/')[1] not in GUIDANCE)
    for key, node in graph.items():
        hit_paths = {path for path in paths if any(matches(w, path) for w in node['paths'])}
        hit_sources = set(sources) & set(node['sources'])
        if hit_paths or hit_sources:
            selected.add(key)
            mapped_paths.update(hit_paths)
            mapped_sources.update(hit_sources)
    unmapped = ['path:' + p for p in paths if p not in mapped_paths]
    unmapped += ['source:' + s for s in sources if s not in mapped_sources]
    # Additions always need an impact decision, even under a broad watch.
    return closure(graph, selected), unmapped


def merge(parent, response, selected, unmapped, sources, decisions):
    if not isinstance(response, dict):
        raise ValueError('reassessment must be an object')
    if response.get('full_reassessment') is True:
        if set(response) != {'full_reassessment', 'reason'} or not isinstance(response['reason'], str) or not response['reason'].strip():
            raise ValueError('full reassessment requires an explicit reason')
        return None
    if set(response) != {'dispositions', 'additions', 'impacts'} or any(not isinstance(response[k], list) for k in response):
        raise ValueError('reassessment requires dispositions, additions and impacts arrays')
    old_graph = parent['graph']
    values = conclusions(parent['analysis'])
    dispositions = {}
    for item in response['dispositions']:
        if (not isinstance(item, dict) or item.get('id') not in old_graph or item['id'] in dispositions
                or item.get('action') not in {'retain', 'replace', 'remove'}
                or not isinstance(item.get('reason'), str) or not item['reason'].strip()
                or set(item) - {'id', 'action', 'reason', 'value', 'watches'}):
            raise ValueError('invalid or duplicate conclusion disposition')
        if item['action'] == 'replace' and 'value' not in item:
            raise ValueError('replacement requires a value')
        if item['action'] != 'replace' and 'value' in item:
            raise ValueError('retained/removed conclusions cannot supply a value')
        dispositions[item['id']] = item
    impacts = {}
    for item in response['impacts']:
        if (not isinstance(item, dict) or set(item) != {'change', 'affected', 'rationale'}
                or item['change'] not in unmapped or item['change'] in impacts
                or not isinstance(item['affected'], list) or set(item['affected']) - old_graph.keys()
                or not isinstance(item['rationale'], str) or not item['rationale'].strip()):
            raise ValueError('invalid unmapped evidence impact decision')
        impacts[item['change']] = item
    if set(impacts) != set(unmapped):
        raise ValueError('every unmapped change requires an impact decision')
    required = set(selected) | set(dispositions)
    for item in impacts.values():
        required.update(item['affected'])
    if closure(old_graph, required) - dispositions.keys():
        raise ValueError('every affected conclusion and dependent requires a disposition')
    graph, items = copy.deepcopy(old_graph), {}
    retired = list(parent['retired_ids'])
    # Serialized graph keys are random IDs. Follow original analysis locations so
    # surviving list conclusions keep their order; additions follow response order.
    by_location = {node['location']: key for key, node in graph.items()}
    for location in values:
        key = by_location[location]
        node = graph[key]
        item = dispositions.get(key, {'action': 'retain'})
        if item['action'] == 'remove':
            retired.append(key)
            continue
        value = item.get('value', values[node['location']])
        if 'watches' in item:
            if not isinstance(item['watches'], dict) or set(item['watches']) != {'depends_on', 'paths', 'sources', 'decisions'}:
                raise ValueError('replacement watches must contain all dependency fields')
            node.update(copy.deepcopy(item['watches']))
        items[key] = (node['location'], copy.deepcopy(value))
    graph = {key: node for key, node in graph.items() if key in items}
    added_ids = ['c-' + uuid.uuid4().hex for _ in response['additions']]
    if len(set(added_ids)) != len(added_ids) or set(added_ids) & (set(old_graph) | set(retired)):
        raise ValueError('runtime conclusion ID collision')
    addition_refs = {'addition:' + str(n): key for n, key in enumerate(added_ids)}
    for key, item in zip(added_ids, response['additions']):
        if not isinstance(item, dict) or set(item) != {'location', 'value', 'watches', 'reason'} or not isinstance(item['reason'], str) or not item['reason'].strip():
            raise ValueError('addition requires location, value, watches and reason')
        location = item['location']
        # Additions append to existing conclusion lists, or restore absent singleton slots.
        if not isinstance(location, str):
            raise ValueError('invalid added conclusion location')
        if location.split('/')[1] in {'boundaries', 'questions', 'test_strategy'} and not location.endswith('/-'):
            raise ValueError('new list conclusions require an append location ending in /-')
        if not isinstance(item['watches'], dict) or set(item['watches']) != {'depends_on', 'paths', 'sources', 'decisions'}:
            raise ValueError('addition watches must contain all dependency fields')
        graph[key] = {'location': location, **copy.deepcopy(item['watches'])}
        items[key] = (location, copy.deepcopy(item['value']))
    if response['additions'] and any(item['location'].split('/')[1] not in GUIDANCE for item in response['additions']):
        guidance = {key for key, node in old_graph.items() if node['location'].split('/')[1] in GUIDANCE}
        if guidance - dispositions.keys():
            raise ValueError('new governing conclusions require guidance dispositions')
    for node in graph.values():
        if isinstance(node['depends_on'], list):
            node['depends_on'] = [addition_refs.get(ref, ref) if isinstance(ref, str) else ref
                                  for ref in node['depends_on']]
    analysis = copy.deepcopy(parent['analysis'])
    for dimension in analysis['boundaries']:
        analysis['boundaries'][dimension] = []
    analysis['questions'], analysis['test_strategy'] = [], []
    analysis['placement'], analysis['reviewer_addenda'] = {}, {}
    for key, (location, value) in items.items():
        parts = location.split('/')[1:]
        if len(parts) == 3 and parts[0] == 'boundaries' and parts[1] in analysis['boundaries']:
            target = analysis['boundaries'][parts[1]]
            location = f'/boundaries/{parts[1]}/{len(target)}'
            target.append(value)
        elif len(parts) == 2 and parts[0] in {'questions', 'test_strategy'}:
            target = analysis[parts[0]]
            location = f'/{parts[0]}/{len(target)}'
            target.append(value)
        elif len(parts) == 2 and parts[0] in {'placement', 'reviewer_addenda'}:
            name = parts[1].replace('~1', '/').replace('~0', '~')
            if name in analysis[parts[0]]:
                raise ValueError('duplicate singleton conclusion')
            analysis[parts[0]][name] = value
        elif location == '/sync_required':
            if not isinstance(value, dict) or set(value) != {'sync_required', 'sync_reason'}:
                raise ValueError('synchronization conclusion requires both fields')
            analysis.update(value)
        elif location in {'/worker_instructions', '/completion_criteria'}:
            analysis[parts[0]] = value
        else:
            raise ValueError('invalid conclusion location: ' + location)
        graph[key]['location'] = location
    # Missing singleton nodes are caught by exact coverage. Removed dependencies must
    # be explicitly rewired by their dependents; never silently discard an edge.
    mandatory(graph, sources)
    validate_graph(graph, analysis, sources, decisions, retired)
    old_open = {q['id'] for q in parent['analysis']['questions'] if q['status'] == 'open'}
    for question in analysis['questions']:
        if question.get('id') in old_open and question.get('status') == 'resolved' and not any(
                ref.split('#', 1)[0].split('/')[0] in {'user_instructions', 'rules', 'choices', 'decisions'}
                for ref in question.get('evidence', [])):
            raise ValueError('resolving a question requires answer or precedence source evidence')
    return analysis, graph, retired
