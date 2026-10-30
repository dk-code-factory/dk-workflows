"""Reconcile engineering evidence and validate versioned, private preparation artifacts.

This module owns preparation; the Decisions CLI only routes to it. Consumers must
check both readiness and fingerprints, including non-code evidence on an unchanged head.
"""
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from shared import preparation_graph as graph_api
from shared.engineering_identity import engineering_id, compatible_reference
from shared.workflow_storage import allocate, owned
from shared.model_diagnostics import FieldError
from shared import architecture

VERSION = 3
DIMENSIONS = ('correctness', 'remediation_depth', 'performance', 'compatibility', 'complexity')
EVIDENCE = ('issues', 'feedback', 'choices', 'user_instructions', 'run_history',
            'questions', 'prior_preparation', 'rules', 'production', 'tests', 'ci', 'reviewers')
AGREEMENTS = {'established', 'interpretation', 'proposal'}


digest = graph_api.digest


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def outside(path, repository):
    path = Path(path).resolve()
    if path.is_relative_to(Path(repository).resolve()):
        raise ValueError('preparation artifacts must be outside the repository')
    return path


def git(checkout, *args):
    try:
        return subprocess.check_output(['git', *args], cwd=checkout, text=True, stderr=subprocess.PIPE).strip()
    except subprocess.CalledProcessError as exc:
        raise ValueError('cannot verify prepared checkout: ' + exc.stderr.strip()) from exc


def collect(manifest_path, *, on_call=None):
    """Require an explicit complete package; empty categories need a source explaining why."""
    manifest_path = Path(manifest_path).resolve()
    manifest = read(manifest_path)
    base = manifest_path.parent
    def path(value):
        return str((base / value).resolve())
    context_path = path(manifest['context'])
    context = read(context_path)
    decisions_path = path(manifest['decisions'])
    # Reuse the exact runner validator, including the unified API and live body snapshot.
    import importlib.util
    runner_path = next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir()) / 'dk-pr-review/scripts/run_reviewers.py'
    spec = importlib.util.spec_from_file_location('preparation_runner', runner_path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    decisions = runner.validate_decisions(context, read(decisions_path))
    if not isinstance(decisions.get('decisions'), list):
        raise ValueError('preparation requires unified decision records')
    if manifest.get('mode') not in ('iteration', 'standalone'):
        raise ValueError('preparation mode must be iteration or standalone')
    evidence = manifest.get('evidence')
    if not isinstance(evidence, dict) or set(evidence) != set(EVIDENCE):
        raise ValueError('complete preparation evidence categories required: ' + ', '.join(EVIDENCE))
    paths = {'manifest': str(manifest_path), 'context': context_path, 'decisions': decisions_path,
             'metadata': context['metadata'], 'patch': context['patch']}
    for category in EVIDENCE:
        values = evidence[category]
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v for v in values):
            raise ValueError(f'{category} requires source artifact paths, including explicit absence evidence')
        for index, value in enumerate(values):
            if category == 'feedback':
                record = read(path(value))
                if not isinstance(record, dict) or not (record.get('complete') is True or
                        record.get('verified_absence') is True and text(record.get('reason'))):
                    raise ValueError('complete current feedback or verified absence required')
                if record.get('complete') is True:
                    graph_api.feedback(record, pr_url=context['url'], head_sha=context['head_sha'])
            paths[f'{category}/{index}'] = path(value)
    reviewer_descriptors = {}
    for index, value in enumerate(evidence['reviewers']):
        descriptor = read(path(value))
        runner.validate_descriptor(descriptor)
        name = descriptor['reviewer']
        if name in reviewer_descriptors:
            raise ValueError('duplicate reviewer descriptor')
        reviewer_descriptors[name] = descriptor
        for n, source in enumerate(descriptor['sources']):
            source_path = Path(source['path'])
            if file_hash(source_path) != source['sha256']:
                raise ValueError('reviewer prompt source changed')
            paths[f'reviewer-source/{index}/{n}'] = str(source_path)
    if not reviewer_descriptors:
        raise ValueError('reviewer descriptors are required')
    if git(context['checkout'], 'rev-parse', 'HEAD') != context['head_sha']:
        raise ValueError('preparation checkout moved from pinned head')
    if git(context['checkout'], 'status', '--porcelain=v1'):
        raise ValueError('preparation requires a clean isolated checkout')
    architecture_analyzer = (lambda prompt, checkout, validate: architecture.analyze(
        prompt, checkout, validate, on_call=on_call)) if on_call else None
    architecture_value = architecture.prepare(context, decisions, analyzer=architecture_analyzer,
        exceptions=manifest.get('architecture_exceptions', []), governing=architecture.governing_sources(paths))
    paths['architecture/0'] = architecture_value['packet']['path']
    for index, reference in enumerate(architecture_value['exceptions']):
        paths[f'architecture-exceptions/{index}'] = reference['path']
    sources = {key: {'path': str(Path(value).resolve()), 'sha256': file_hash(value)}
               for key, value in paths.items()}
    modes = manifest.get('source_modes', {})
    if (not isinstance(modes, dict) or set(modes) - set(sources)
            or any(mode not in {'capture', 'live'} for mode in modes.values())):
        raise ValueError('source_modes must map indexed source keys to capture or live')
    for key, source in sources.items():
        category = key.split('/')[0]
        captured = (key in {'manifest', 'context', 'decisions', 'metadata', 'patch'}
                    or category in {'reviewers', 'prior_preparation', 'architecture', 'architecture-exceptions'}
                    or category in {'production', 'tests'} and
                    Path(source['path']).is_relative_to(Path(context['checkout'])))
        if category == 'feedback':
            value = read(source['path'])
            captured |= value.get('complete') is True
        source['mode'] = modes.get(key, 'capture' if captured else 'live')
    origins = manifest.get('source_origins', {})
    if (not isinstance(origins, dict) or set(origins) - set(sources)
            or any(not text(value) or sources[key]['mode'] != 'capture' for key, value in origins.items())):
        raise ValueError('source_origins requires captured source keys and nonempty original identities')
    for key, source in sources.items():
        if source['mode'] == 'capture':
            source['origin_identity'] = origins.get(key) or graph_api.source_identity(
                key, source, context['checkout'], Path(source['path']).read_bytes())
            if key == 'architecture/0':
                source['origin_identity'] = 'architecture:' + architecture.load(architecture_value['packet'])['repository']
    prior = []
    for value in evidence['prior_preparation']:
        prior_path = path(value)
        try:
            previous = read(prior_path)
            if not isinstance(previous, dict):
                raise ValueError('prior preparation must be an object')
            if 'analysis' in previous:
                historical(prior_path)
            elif previous.get('verified_absence') is not True or not text(previous.get('reason')):
                raise ValueError('prior preparation requires a valid artifact or verified absence')
            prior.append(previous)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f'prior_preparation artifact {prior_path} must contain valid JSON; '
                'when no previous preparation exists, use a JSON object explaining its absence'
            ) from exc
    return manifest, context, decisions, sources, reviewer_descriptors, prior


def analysis_prompt(manifest, context, decisions, sources, descriptors):
    references = Path(__file__).resolve().parents[1] / 'references'
    procedure = (references / 'preparation.md').read_text()
    return (
        'Perform Decisions engineering preparation, read-only. Do not edit code, Git refs, or GitHub. '
        'Inputs are already collected; analyze them under the procedure below. '
        'Before constructing the response, read the relevant fields in '
        'the data contract at this absolute path:\nRESPONSE CONTRACT: ' +
        str(references / 'preparation-contract.md') +
        '\nReturn only the initial analysis-and-graph JSON, unless subsequent reassessment '
        'instructions select the reassessment response.\n\n' + procedure +
        '\n\nINPUT INDEX (read original paths):\n' + json.dumps({
            'manifest': manifest, 'context': context, 'decisions': decisions,
            'sources': sources, 'reviewers': descriptors}, ensure_ascii=True))


PREPARATION_INLINE_LIMIT = 128_000


def bounded_prompt(prompt, checkout):
    """Move oversized transport input to an exact, inspectable private source."""
    from shared.model_diagnostics import CURRENT
    from shared.private_state import atomic_bytes
    from shared.diagnostics import Diagnostic, task
    size = len(json.dumps(prompt, ensure_ascii=True))
    if size <= PREPARATION_INLINE_LIMIT:
        return prompt
    journal = CURRENT.get()
    directory = journal.path.parent if journal else allocate(repository=checkout)
    content = prompt.encode('utf-8')
    fingerprint = hashlib.sha256(content).hexdigest()
    path = directory / ('preparation-input-' + fingerprint + '.txt')
    source = {'path': str(path), 'sha256': fingerprint,
              'bytes': len(content), 'serialized_characters': size}
    try:
        if path.exists():
            if path.read_bytes() != content:
                raise ValueError('retained preparation input differs from its content address')
        else:
            atomic_bytes(path, content)
    except (OSError, ValueError) as exc:
        raise Diagnostic('preparation_input_failed',
            'Private preparation input could not be retained or verified: ' + str(exc),
            task('Restore the exact retained input or repair private storage before retrying. '
                 'Preserve original evidence and the existing correction budget.', path,
                 *([journal.path] if journal else [])), outcome='error') from exc
    if journal:
        journal.value['attempts'][-1]['input_file'] = source
        journal.save()
    return ('Perform read-only Decisions engineering preparation using the complete original request '
            'retained below. The file is the full authoritative request, including any correction, '
            'validation error and prior response; apply every instruction and governing constraint. '
            'Read it completely before answering, then read its original evidence and governing sources. '
            'Use bounded byte or character-offset reads (for example Python seek/read with incremental '
            'UTF-8 decoding, or slicing decoded text into at most 16000 characters per read). '
            'Large JSON may occupy a single line; line limits alone are insufficient. '
            'Continue across offsets until all required content is inspected. Never truncate, summarize '
            'away constraints, or treat the reference as permission to skip evidence or preparation checks. '
            'Verify the file SHA-256 before using it. Return exactly the JSON required by the original '
            'request and response contract.\nORIGINAL REQUEST FILE: ' + json.dumps(source, ensure_ascii=True))


def analyze(prompt, checkout, *, validate=None, on_call=None, diagnostic_scope='engineering-preparation'):
    from shared.executors import execute_json, execute
    def counted(request, directory, **kwargs):
        request = bounded_prompt(request, directory)
        if on_call:
            on_call()
        return execute(request, directory, inherit_architecture=False, **kwargs)
    return execute_json(prompt, checkout, validate=validate, executor=counted,
                        diagnostic_scope=diagnostic_scope,
                        retain_accepted=(lambda value: isinstance(value, dict) and value.get('full_reassessment') is True)
                            if isinstance(diagnostic_scope, dict) and diagnostic_scope.get('phase') == 'incremental'
                            else None,
                        correction_instructions='worker_instructions and completion_criteria must be nonempty strings.')


def text(value):
    return isinstance(value, str) and bool(value.strip())


def validate_analysis(analysis, sources, reviewers, prior=()):
    if not isinstance(analysis, dict):
        raise ValueError('preparation analysis must be an object')
    def evidence(item):
        refs = item.get('evidence')
        if not isinstance(refs, list) or not refs or any(
                not isinstance(ref, str) or ref.split('#', 1)[0] not in sources for ref in refs):
            raise ValueError('conclusion requires exact indexed evidence references')
    def conclusion(item):
        if (not isinstance(item, dict) or not text(item.get('text'))
                or item.get('agreement') not in AGREEMENTS
                or type(item.get('material')) is not bool or not text(item.get('rationale'))):
            raise ValueError('conclusion requires agreement, materiality and rationale')
        evidence(item)
    boundaries = analysis.get('boundaries')
    if not isinstance(boundaries, dict) or set(boundaries) != set(DIMENSIONS):
        raise ValueError('all engineering boundary dimensions required')
    for entries in boundaries.values():
        if not isinstance(entries, list) or not entries:
            raise ValueError('each boundary dimension requires an assessment')
        for entry in entries:
            conclusion(entry)
    placement = analysis.get('placement')
    if not isinstance(placement, dict) or set(placement) != {'production', 'tests', 'reuse', 'entrypoints', 'dependencies'}:
        raise ValueError('concrete production and test placement required')
    for name, item in placement.items():
        conclusion(item)
        if not isinstance(item.get('locations'), list) or not item['locations'] or any(not text(v) for v in item['locations']):
            raise FieldError('placement.' + name + '.locations',
                             'expected a nonempty array of nonempty location strings')
    strategy = analysis.get('test_strategy')
    if not isinstance(strategy, list) or not strategy:
        raise ValueError('test strategy must map required behavior to sufficient validation')
    for item in strategy:
        if not isinstance(item, dict) or any(not text(item.get(key)) for key in (
                'behavior', 'existing', 'missing', 'level', 'location', 'command', 'rationale', 'limitation')):
            raise ValueError('test strategy lacks behavior, evidence, placement or completion procedure')
        if item['level'] not in {'inspection', 'unit', 'integration', 'system', 'performance'}:
            raise ValueError('invalid test level')
        evidence(item)
    questions = analysis.get('questions')
    if not isinstance(questions, list):
        raise ValueError('questions must be retained explicitly')
    ids = set()
    for item in questions:
        if (not isinstance(item, dict) or not text(item.get('id')) or item['id'] in ids
                or item.get('status') not in {'open', 'resolved'} or type(item.get('material')) is not bool
                or any(not text(item.get(key)) for key in ('statements', 'affected_work', 'recommendation', 'proposed_changes'))
                or not isinstance(item.get('options'), list) or len(item['options']) < 2):
            raise ValueError('question requires conflicting statements, affected work, options and recommendation')
        for option in item['options']:
            if not isinstance(option, dict) or any(not text(option.get(k)) for k in ('choice', 'benefits', 'costs')):
                raise ValueError('each decision option requires benefits and costs')
        if item['status'] == 'resolved' and not text(item.get('resolution')):
            raise ValueError('resolved contradiction requires explicit resolution reasoning')
        evidence(item)
        ids.add(item['id'])
    for previous in prior:
        if not isinstance(previous, dict) or 'analysis' not in previous:
            continue  # Explicit absence artifact, not a previous preparation.
        for question in previous['analysis']['questions']:
            if question['status'] == 'open' and question['id'] not in ids:
                raise ValueError('unanswered question lost across preparation refresh')
    addenda = analysis.get('reviewer_addenda')
    if not isinstance(addenda, dict) or set(addenda) != set(reviewers) or any(not text(v) for v in addenda.values()):
        raise ValueError('each configured reviewer requires a tailored addendum')
    if not text(analysis.get('worker_instructions')) or not text(analysis.get('completion_criteria')):
        raise ValueError('worker instructions and completion criteria required')
    if type(analysis.get('sync_required')) is not bool:
        raise ValueError('explicit synchronization assessment required')
    if analysis['sync_required'] and not text(analysis.get('sync_reason')):
        raise ValueError('pending synchronization requires a reason')
    return analysis


def readiness(analysis):
    proposals = [item for entries in analysis['boundaries'].values() for item in entries]
    proposals += list(analysis['placement'].values())
    if (any(item['agreement'] == 'proposal' and item['material'] for item in proposals)
            or any(q['status'] == 'open' and q['material'] for q in analysis['questions'])):
        return 'needs_decision'
    return 'sync_pending' if analysis['sync_required'] else 'ready'


def policy():
    import shared.engineering_identity as engineering
    plugin = Path(__file__).resolve().parents[3]
    return digest({p.resolve().relative_to(plugin).as_posix(): file_hash(p) for p in (
        Path(__file__), Path(graph_api.__file__), Path(engineering.__file__),
        Path(architecture.__file__), architecture.PROCEDURE,
        architecture.PROCEDURE.parent.parent / 'dk-architecture-review/procedure.md',
        architecture.PROCEDURE.parent.parent / 'dk-architecture-enforcer/procedure.md',
        Path(__file__).resolve().parents[1] / 'references/preparation.md',
        Path(__file__).resolve().parents[1] / 'references/preparation-contract.md',
        plugin / 'common/instruction-sources.md')})


def retained_source(artifact_path, artifact, key):
    source = artifact['sources'][key]
    return Path(source.get('snapshot', Path(artifact_path).parent / 'evidence' / key))


def historical(path):
    """Validate a selected parent from immutable evidence, never its vanished inputs."""
    path = Path(path).resolve()
    try:
        artifact = read(path)
        for key, source in artifact['sources'].items():
            snapshot = retained_source(path, artifact, key)
            if file_hash(snapshot) != source['sha256']:
                raise ValueError('parent evidence snapshot changed: ' + key)
        context = read(retained_source(path, artifact, 'context'))
        raw = read(retained_source(path, artifact, 'decisions'))
        decisions = raw.get('data', raw)
        validate(artifact, context, decisions, require_ready=False, check_sources=False)
        return artifact
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError('invalid selected parent preparation: ' + str(exc)) from exc


def reassessment_prompt(prompt, parent_path, parent, paths, changed_sources, selected, unmapped, full=False):
    values = graph_api.conclusions(parent['analysis'])
    return (prompt + '\n\nREASSESSMENT (one batch, read original changed and governing sources):\n' +
            json.dumps({'parent_path': str(parent_path), 'parent': parent,
                        'changed_paths': paths, 'changed_sources': changed_sources,
                        'affected': {k: values[parent['graph'][k]['location']] for k in sorted(selected)},
                        'remaining_index': {k: v['location'] for k, v in parent['graph'].items() if k not in selected},
                        'unmapped_changes': unmapped, 'full_reassessment': full}, ensure_ascii=True) +
            '\nReturn the reassessment protocol from the preparation contract, not a new analysis. '
            'Address every affected conclusion and every unmapped change explicitly. Identify additional '
            'affected conclusions and their dependents. Retain unchanged values exactly. '
            'Use addition:N in watches.depends_on to reference the zero-based additions array index N '
            '(canonical decimal, no leading zeros). Existing updated nodes may use these references too; '
            'forward references and cycles are allowed. Runtime assigns all fresh stable IDs and resolves '
            'these batch-local references before graph validation. Thread resolution cannot answer a '
            'question or settle a Decision. Only an explicit bounded-impact failure may '
            'request full_reassessment; a full reassessment cannot escalate again.')


def prepare(manifest_path, output_dir, analyzer=None, *, previous_preparation=None, full_analysis=False):
    calls = 0
    def count():
        nonlocal calls
        calls += 1
    manifest, context, decisions, sources, descriptors, prior = collect(manifest_path, on_call=count)
    collection_sources = dict(sources)
    # Capture collection order before rebinding citation slots. Manifest aliases
    # can disappear after publication; retained paths must not resolve them again.
    reviewer_paths = [source['path'] for key, source in sources.items() if key.startswith('reviewers/')]
    output_dir = outside(output_dir, context.get('repository', context['checkout']))
    outside(output_dir, context['checkout'])
    current = graph_api.inputs(context, sources)
    current['policy'] = policy()
    parent = historical(previous_preparation) if previous_preparation else None
    parent_hash = file_hash(previous_preparation) if parent else None
    consumed_lineage = ({str(Path(previous_preparation).resolve()): (parent_hash, parent['preparation_id'])}
                        if parent else {})
    if parent and parent['identity']['pr_url'] != context['url']:
        raise ValueError('selected parent belongs to a different PR')
    if parent:
        outside(previous_preparation, context.get('repository', context['checkout']))
        outside(previous_preparation, context['checkout'])
        prior = [*prior, parent]
    migration = parent is not None and parent['preparation_version'] == 1
    paths, changed_sources, selected, unmapped = [], [], set(), []
    force_full = bool(full_analysis or migration)
    if parent and not migration:
        sources, current = graph_api.rebind(sources, current, parent)
        paths, changed_sources = graph_api.changes(parent, current)
        previous_priors = {key: read(retained_source(previous_preparation, parent, key))
                           for key in parent['sources'] if key.startswith('prior_preparation/')}
        lineage, known_absences = set(), set()
        ancestor_path = previous_preparation
        ancestor = parent
        while True:
            if ancestor['preparation_id'] in lineage:
                raise ValueError('cyclic preparation lineage')
            lineage.add(ancestor['preparation_id'])
            for key, source in ancestor['sources'].items():
                if key.startswith('prior_preparation/'):
                    value = read(retained_source(ancestor_path, ancestor, key))
                    if 'analysis' not in value and value.get('verified_absence') is True:
                        known_absences.add(source['sha256'])
            if not ancestor.get('parent'):
                break
            ancestor_path = ancestor['parent']['path']
            ancestor = historical(ancestor_path)
            consumed_lineage[str(Path(ancestor_path).resolve())] = (file_hash(ancestor_path), ancestor['preparation_id'])
        prior_inputs = {key: read(source['path']) for key, source in sources.items()
                        if key.startswith('prior_preparation/')}
        absence_counts = Counter(sources[key]['sha256'] for key, value in prior_inputs.items()
                                 if 'analysis' not in value and value.get('verified_absence') is True)
        previous_absence_counts = Counter(parent['sources'][key]['sha256'] for key, value in previous_priors.items()
                                          if 'analysis' not in value and value.get('verified_absence') is True)
        has_lineage = any('analysis' in value and value['preparation_id'] in lineage
                          for value in prior_inputs.values())
        only_lineage = all('analysis' in value and value['preparation_id'] in lineage
                           for value in prior_inputs.values())
        for key in sources.keys() | parent['sources'].keys():
            if key.startswith('prior_preparation/') and (
                    sources.get(key, {}).get('sha256') != parent['sources'].get(key, {}).get('sha256')):
                previous = previous_priors.get(key, {})
                current_prior = prior_inputs.get(key, {})
                previous_absence = 'analysis' not in previous and previous.get('verified_absence') is True
                absence_changed = ('analysis' not in current_prior and current_prior.get('verified_absence') is True and
                                   (previous_absence or sources[key]['sha256'] not in known_absences or
                                    absence_counts[sources[key]['sha256']] > 1 or
                                    key not in parent['sources'] and not has_lineage))
                absence_removed = previous_absence and (key not in prior_inputs and not only_lineage or
                    previous_absence_counts[parent['sources'][key]['sha256']] > 1)
                if any('analysis' in value and value['preparation_id'] not in lineage
                       for value in (previous, current_prior)) or absence_changed or absence_removed:
                    changed_sources.append(key)
        changed_sources = sorted(set(changed_sources))
        force_full |= (parent['mode'] != manifest['mode'] or
                       parent['identity']['base_sha'] != context['base_sha'] or
                       parent['inputs']['policy'] != current['policy'])
        try:
            git(context['checkout'], 'cat-file', '-e', parent['identity']['head_sha'] + '^{tree}')
        except ValueError:
            force_full = True
        selected, unmapped = graph_api.affected(parent['graph'], paths, changed_sources)
        # A new source/file needs an impact decision even when a broad watch matches.
        unmapped = sorted(set(unmapped) |
                          {'path:' + p for p in paths if p not in parent['inputs']['tree']} |
                          {'source:' + k for k in changed_sources if k not in parent['inputs']['sources']})
    identity = {'pr_url': context['url'], 'base_sha': context['base_sha'],
                'head_sha': context['head_sha'], 'body_hash': decisions['body_hash'],
                'decisions_hash': digest(decisions)}
    # Reuse engineering compatibility's governing-evidence normalization before
    # an analysis exists. Capture locations and transport formatting must not
    # grant another correction; live opaque identities and unknown fields govern.
    diagnostic_evidence = engineering_id({
        'preparation_version': VERSION, 'mode': manifest['mode'], 'identity': identity,
        'inputs': current, 'sources': {key: {**source, 'snapshot': source['path']}
                                     for key, source in sources.items()},
        'reviewers': descriptors,
        'parent': {'path': str(previous_preparation), 'sha256': parent_hash,
                   'preparation_id': parent['preparation_id']} if parent else None})
    diagnostic_scope = {'operation': 'engineering-preparation', 'evidence': diagnostic_evidence,
                        'parent': engineering_id(parent) if parent else None}
    phases = []
    def invoke(prompt, validator, phase):
        def checked(value):
            try:
                return validator(value)
            except (KeyError, TypeError, AttributeError, IndexError) as exc:
                raise ValueError('malformed preparation response: ' + str(exc)) from exc
        if analyzer is None:
            value = analyze(prompt, context['checkout'], validate=checked, on_call=count,
                            diagnostic_scope={**diagnostic_scope, 'phase': phase})
        else:
            count()
            value = analyzer(prompt, context['checkout'])
            checked(value)
        phases.append(value)
        return checked(value)
    def initial(value):
        if isinstance(value, dict) and isinstance(value.get('analysis'), dict):
            validate_analysis(value['analysis'], sources, descriptors, prior)
        result = graph_api.initial(value, sources, decisions)
        validate_analysis(result[0], sources, descriptors, prior)
        return result
    prompt = analysis_prompt(manifest, context, decisions, sources, descriptors)
    if migration:
        prompt += '\nMIGRATION: inspect original v1 preparation at ' + str(previous_preparation) + '\n' + json.dumps(parent)
    if not parent or migration:
        result, graph = invoke(prompt, initial, 'migration' if migration else 'initial')
        retired = []
        mode = 'migration' if migration else 'initial'
    elif not force_full and not paths and not changed_sources:
        result, graph, retired = parent['analysis'], parent['graph'], parent['retired_ids']
        mode = 'operational'
    else:
        if force_full:
            selected = set(parent['graph'])
        def reassess(value, allow_escalation=True):
            result = graph_api.merge(parent, value, selected, unmapped, sources, decisions)
            if result is None:
                if not allow_escalation:
                    raise ValueError('full reassessment cannot escalate again')
                return None
            validate_analysis(result[0], sources, descriptors, prior)
            return result
        merged = invoke(reassessment_prompt(prompt, previous_preparation, parent, paths, changed_sources,
                                            selected, unmapped, full=force_full),
                        lambda value: reassess(value, not force_full), 'full' if force_full else 'incremental')
        mode = 'full' if force_full else 'incremental'
        if merged is None:
            selected = set(parent['graph'])
            merged = invoke(reassessment_prompt(prompt, previous_preparation, parent, paths, changed_sources,
                                                selected, unmapped, full=True),
                            lambda value: reassess(value, False), 'full')
            mode = 'full_fallback'
        result, graph, retired = merged
    # Recollect the entire package: additions, removals and input movement cannot
    # hide behind the initial index. Check again immediately before publication.
    def unchanged():
        again = collect(manifest_path, on_call=count)
        live = graph_api.inputs(again[1], again[3])
        live['policy'] = policy()
        live_sources = again[3]
        if parent and not migration:
            live_sources, live = graph_api.rebind(live_sources, live, parent)
        if (live != current or {k: (v['path'], v['sha256']) for k, v in live_sources.items()} !=
                {k: (v['path'], v['sha256']) for k, v in sources.items()}):
            raise ValueError('preparation source changed during analysis')
        if (again[0], again[1], again[2], again[4]) != (manifest, context, decisions, descriptors):
            raise ValueError('preparation checkout or identity changed during analysis')
        if parent and file_hash(previous_preparation) != parent_hash:
            raise ValueError('selected parent changed during analysis')
        for path, (fingerprint, preparation_id) in consumed_lineage.items():
            verified = historical(path)
            if verified['preparation_id'] != preparation_id or file_hash(path) != fingerprint:
                raise ValueError('preparation lineage changed during analysis')
    unchanged()
    validate_analysis(result, sources, descriptors, prior)
    report = {'mode': mode, 'changed_paths': paths, 'changed_sources': changed_sources,
              'unmapped_changes': unmapped,
              'changed_bindings': sorted(k for k in sources.keys() | (parent['sources'].keys() if parent else set())
                  if not parent or k not in sources or k not in parent['sources'] or
                  any(sources[k][f] != parent['sources'][k][f] for f in ('path', 'sha256'))),
              'identity_changes': {k: v for k, v in identity.items() if not parent or parent['identity'][k] != v},
              'affected_conclusions': sorted(selected | {d['id'] for phase in phases
                  for d in phase.get('dispositions', [])}),
              'reused_conclusions': sorted(k for k, v in graph.items() if parent and not migration and
                  k in parent['graph'] and graph_api.conclusions(result)[v['location']] ==
                  graph_api.conclusions(parent['analysis'])[parent['graph'][k]['location']]),
              'model_call_count': calls, 'phases': phases if parent and not migration else []}
    # Return the selected immutable artifact only when every live binding is exact.
    if (parent and parent['preparation_version'] == VERSION and not force_full and mode == 'operational' and parent['identity'] == identity
            and parent['inputs'] == current and
            parent.get('reviewer_paths') == reviewer_paths and
            {k: (v['path'], v['sha256']) for k, v in parent['sources'].items()} ==
            {k: (v['path'], v['sha256']) for k, v in sources.items()}):
        validate(parent, context, decisions, require_ready=False)
        report['mode'] = 'unchanged'
        return {'artifact': str(Path(previous_preparation).resolve()), 'preparation_id': parent['preparation_id'],
                'engineering_id': engineering_id(parent), 'readiness': parent['readiness'], 'refresh': report}
    workflow_dir = (owned(parent['workflow_dir']) if parent and parent.get('workflow_dir') else
                    owned(context['workflow_dir']) if context.get('workflow_dir') else
                    allocate(repository=context.get('repository', context['checkout'])))
    outside(workflow_dir, context.get('repository', context['checkout']))
    outside(workflow_dir, context['checkout'])
    version_dir = Path(tempfile.mkdtemp(prefix='preparation-', dir=workflow_dir))
    try:
        workspace = version_dir / 'checkout'
        git(context['checkout'], 'clone', '--quiet', '--no-hardlinks', '--no-checkout',
            context['checkout'], str(workspace))
        git(workspace, 'checkout', '--quiet', '--detach', context['head_sha'])
        for key, source in sources.items():
            target = version_dir / 'evidence' / key
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            content = Path(source['path']).read_bytes()
            if hashlib.sha256(content).hexdigest() != source['sha256']:
                raise ValueError('preparation source changed while retaining evidence')
            target.write_bytes(content)
            os.chmod(target, 0o400)
            source['snapshot'] = str(target)
        # Retain a directly usable collection manifest, not only its old aliases.
        # Live inputs keep their original paths; captured inputs use private copies.
        from shared.private_state import write
        recovered_context = {**context, 'checkout': str(workspace),
                             'metadata': sources['metadata']['snapshot'], 'patch': sources['patch']['snapshot'],
                             'workflow_dir': str(workflow_dir), 'context_file': str(version_dir / 'context.json')}
        context_path = version_dir / 'context.json'
        write(context_path, recovered_context)
        recovery = {**manifest, 'context': str(context_path), 'decisions': sources['decisions']['snapshot'],
                    'evidence': {}, 'source_modes': {}, 'source_origins': {}}
        for key in ('manifest', 'context', 'decisions', 'metadata', 'patch'):
            recovery['source_modes'][key] = sources[key]['mode']
            if sources[key]['mode'] == 'capture':
                recovery['source_origins'][key] = sources[key]['origin_identity']
        for category, values in manifest['evidence'].items():
            recovery['evidence'][category] = []
            for index, value in enumerate(values):
                original = str((Path(manifest_path).resolve().parent / value).resolve())
                key = category + '/' + str(index)
                source = collection_sources[key]
                recovery['evidence'][category].append(source['snapshot'] if source['mode'] == 'capture' else original)
                recovery['source_modes'][key] = source['mode']
                if source['mode'] == 'capture':
                    recovery['source_origins'][key] = source['origin_identity']
        # Reviewer instruction sources are collected from the preserved descriptor.
        for key, source in collection_sources.items():
            if key.startswith('reviewer-source/'):
                recovery['source_modes'][key] = source['mode']
                if source['mode'] == 'capture':
                    recovery['source_origins'][key] = source['origin_identity']
        manifest_target = version_dir / 'manifest.json'
        write(manifest_target, recovery)
        context_path.chmod(0o400)
        manifest_target.chmod(0o400)
        captured_paths = {(source['path'], source['sha256']): source['snapshot'] for source in sources.values()}
        architecture_scope = architecture.governing_sources({key: captured_paths[source['path'], source['sha256']]
            for key, source in collection_sources.items() if key.split('/')[0] in architecture.SCOPE_SOURCES})
        unchanged()
        report['model_call_count'] = calls
        artifact = {'preparation_version': VERSION, 'mode': manifest['mode'], 'identity': identity,
                    'architecture': {'packet': {'path': sources['architecture/0']['snapshot'],
                        'fingerprint': read(sources['architecture/0']['snapshot'])['fingerprint']},
                        'catalog': read(sources['architecture/0']['snapshot'])['catalog'],
                        'exceptions': manifest.get('architecture_exceptions', []),
                        'governing': architecture_scope},
                    'workflow_dir': str(workflow_dir), 'workspace': str(workspace),
                    'recovery_manifest': {'path': str(manifest_target), 'sha256': file_hash(manifest_target)},
                    'recovery_context': {'path': str(context_path), 'sha256': file_hash(context_path)},
                    'reviewer_order': list(descriptors),
                    'sources': sources, 'reviewers': descriptors, 'reviewer_paths': reviewer_paths, 'analysis': result,
                    'graph': graph, 'retired_ids': retired, 'inputs': current,
                    'parent': ({'preparation_id': parent['preparation_id'],
                                'path': str(Path(previous_preparation).resolve()), 'sha256': parent_hash,
                                'migration': migration} if parent else None),
                    'refresh': report, 'readiness': readiness(result)}
        artifact['engineering_id'] = engineering_id(artifact)
        artifact['preparation_id'] = digest(artifact)
        validate(artifact, context, decisions, require_ready=False)
        target = version_dir / 'preparation.json'
        write(target, artifact)
        os.chmod(target, 0o400)
    except BaseException:
        import shutil
        shutil.rmtree(version_dir)
        raise
    return {'artifact': str(target), 'preparation_id': artifact['preparation_id'],
            'engineering_id': engineering_id(artifact), 'readiness': artifact['readiness'], 'refresh': report}


def validate(artifact, context, decisions, *, require_ready=True, check_sources=True):
    if not isinstance(artifact, dict) or artifact.get('preparation_version') not in {1, 2, VERSION}:
        raise ValueError('unsupported preparation artifact')
    if check_sources and artifact['preparation_version'] < VERSION:
        raise ValueError('historical preparation cannot authorize current work; refresh preparation')
    if artifact.get('mode') not in {'standalone', 'iteration'}:
        raise ValueError('invalid preparation mode')
    content = {k: v for k, v in artifact.items() if k != 'preparation_id'}
    if artifact.get('preparation_id') != digest(content):
        raise ValueError('preparation artifact identity mismatch')
    expected = {'pr_url': context['url'], 'base_sha': context['base_sha'],
                'head_sha': context['head_sha'], 'body_hash': decisions['body_hash'],
                'decisions_hash': digest(decisions)}
    if artifact.get('identity') != expected:
        raise ValueError('preparation source identity is stale')
    sources = artifact.get('sources')
    required = {'manifest', 'context', 'decisions', 'metadata', 'patch'}
    if (not isinstance(sources, dict) or not required.issubset(sources)
            or any(not any(k.startswith(name + '/') for k in sources) for name in EVIDENCE)):
        raise ValueError('preparation source index is incomplete')
    for source in sources.values():
        if (not isinstance(source, dict) or not Path(source.get('path', '')).is_absolute()
                or not isinstance(source.get('sha256'), str) or len(source['sha256']) != 64):
            raise ValueError('invalid preparation source fingerprint')
    if not isinstance(artifact.get('reviewers'), dict) or not artifact['reviewers']:
        raise ValueError('preparation reviewer adaptation is incomplete')
    for name, descriptor in artifact['reviewers'].items():
        if not isinstance(descriptor, dict) or descriptor.get('reviewer') != name or descriptor.get('complete') is not True:
            raise ValueError('preparation reviewer adaptation is incomplete')
    validate_analysis(artifact['analysis'], artifact['sources'], artifact['reviewers'])
    if artifact['preparation_version'] >= 2:
        if 'reviewer_paths' in artifact:
            paths = artifact['reviewer_paths']
            expected_paths = [source['path'] for key, source in sources.items() if key.startswith('reviewers/')]
            if (not isinstance(paths, list) or any(not isinstance(path, str) for path in paths)
                    or len(set(paths)) != len(paths) or len(paths) != len(expected_paths)
                    or set(paths) != set(expected_paths)):
                raise ValueError('preparation ordered reviewer paths differ from retained descriptors')
        if not isinstance(artifact.get('inputs'), dict) or set(artifact['inputs']) != {'policy', 'tree', 'sources'}:
            raise ValueError('invalid preparation input fingerprints')
        if (not isinstance(artifact['inputs']['sources'], dict) or
                set(artifact['inputs']['sources']) != set(sources)):
            raise ValueError('preparation input source coverage differs')
        same_policy = artifact['inputs']['policy'] == policy()
        graph_api.validate_graph(artifact.get('graph'), artifact['analysis'], sources, decisions,
                                 artifact.get('retired_ids'), require_mandatory=same_policy or check_sources)
        if check_sources and not same_policy:
            raise ValueError('preparation policy changed; refresh preparation')
        for key, source in sources.items():
            if not Path(source.get('snapshot', '')).is_absolute() or file_hash(source['snapshot']) != source['sha256']:
                raise ValueError('preparation immutable evidence snapshot changed')
            fingerprint = artifact['inputs']['sources'][key]
            if (not isinstance(fingerprint, dict) or set(fingerprint) != {'identity', 'governing'}
                    or not text(fingerprint['identity']) or not isinstance(fingerprint['governing'], str)
                    or len(fingerprint['governing']) != 64):
                raise ValueError('invalid preparation governing input fingerprint')
            if same_policy:
                content = Path(source['snapshot']).read_bytes()
                original_context = read(sources['context']['snapshot'])
                expected_input = {'identity': graph_api.source_identity(key, source, original_context['checkout'], content),
                                  'governing': digest(graph_api.semantic(key, content))}
                if expected_input != fingerprint:
                    raise ValueError('preparation governing input fingerprint mismatch')
        if check_sources and artifact['inputs']['tree'] != graph_api.entries(context['checkout'], context['head_sha']):
            raise ValueError('preparation Git tree fingerprint mismatch')
        parent = artifact.get('parent')
        if parent is not None:
            if (not isinstance(parent, dict) or set(parent) != {'path', 'sha256', 'preparation_id', 'migration'}
                    or file_hash(parent['path']) != parent['sha256']
                    or read(parent['path']).get('preparation_id') != parent['preparation_id']):
                raise ValueError('preparation parent fingerprint mismatch')
    state = readiness(artifact['analysis'])
    if artifact.get('readiness') != state:
        raise ValueError('preparation readiness does not match analysis')
    if require_ready and state != 'ready':
        raise ValueError('preparation is ' + state)
    if check_sources:
        for field in ('patch', 'metadata'):
            if file_hash(context[field]) != sources[field]['sha256']:
                raise ValueError(f'preparation source identity mismatch: supplied {field} differs from prepared evidence')
        for source in artifact['sources'].values():
            if artifact['preparation_version'] >= 3 and source.get('mode') == 'capture':
                continue
            if file_hash(source['path']) != source['sha256']:
                raise ValueError('preparation source changed; refresh preparation')
    if artifact['preparation_version'] >= 3:
        if check_sources or artifact.get('architecture'):
            architecture_context = {**context, 'metadata': source_path(artifact, 'metadata')}
            architecture.validate_preparation(artifact.get('architecture'), architecture_context, decisions, live=check_sources)
        for key in ('recovery_manifest', 'recovery_context'):
            reference = artifact.get(key, {})
            if not Path(reference.get('path', '')).is_absolute() or file_hash(reference['path']) != reference.get('sha256'):
                raise ValueError('workflow recovery capture changed: ' + key)
        if artifact.get('engineering_id') != engineering_id(artifact):
            raise ValueError('preparation engineering identity mismatch')
        if (set(artifact.get('reviewer_order', [])) != set(artifact['reviewers'])
                or len(artifact['reviewer_order']) != len(artifact['reviewers'])):
            raise ValueError('invalid ordered reviewer identity')
        if any(source.get('mode') not in {'capture', 'live'} for source in sources.values()):
            raise ValueError('preparation source requires capture/live classification')
        if check_sources:
            from shared.bindings import pinned_bindings
            with pinned_bindings(artifact['reviewers']):
                pass
    return artifact


def source_path(artifact, key):
    source = artifact['sources'][key]
    return source['snapshot'] if artifact.get('preparation_version', 1) >= 3 else source['path']


def context_for(artifact):
    context = read(source_path(artifact, 'context'))
    if artifact.get('preparation_version', 1) >= 3:
        context.update(checkout=artifact['workspace'],
                       patch=source_path(artifact, 'patch'), metadata=source_path(artifact, 'metadata'))
    return context


def load(path, context, decisions, **kwargs):
    outside(path, context.get('repository', context['checkout']))
    outside(path, context['checkout'])
    return validate(read(path), context, decisions, **kwargs)


def gate(path, *, pr_url, head_sha, body=None, require_ready=True):
    """For work/completion gates: recover authoritative context from retained sources."""
    artifact = read(path)
    context = context_for(artifact)
    raw = read(source_path(artifact, 'decisions'))
    decisions = raw.get('data', raw)
    if context['url'] != pr_url or context['head_sha'] != head_sha:
        raise ValueError('preparation PR or head mismatch')
    if body is not None and hashlib.sha256(body.encode()).hexdigest() != decisions['body_hash']:
        raise ValueError('live PR scope changed; refresh preparation')
    loaded = load(path, context, decisions, require_ready=require_ready)
    if (git(context['checkout'], 'rev-parse', 'HEAD') != context['head_sha']
            or git(context['checkout'], 'status', '--porcelain=v1')):
        raise ValueError('prepared isolated checkout changed')
    return loaded


def retained(path):
    """Read historical evidence without consulting refreshable original paths."""
    path = Path(path).resolve()
    artifact = read(path)
    copies = {}
    for key, source in artifact['sources'].items():
        target = (path.parent / 'evidence' / key).resolve()
        if (not target.is_relative_to(path.parent / 'evidence')
                or file_hash(target) != source['sha256']):
            raise ValueError('retained preparation evidence changed: ' + key)
        copies[key] = {'path': str(target), 'sha256': source['sha256']}
    context = context_for(artifact)
    raw = read(copies['decisions']['path'])
    decisions = raw.get('data', raw)
    outside(path, context['repository'])
    outside(path, context['checkout'])
    validate(artifact, context, decisions, check_sources=False)
    return artifact, context, decisions, copies


def verify_completion(path, artifact):
    """Require actual engineering assessment, independent of reviewer convergence and CI."""
    context = context_for(artifact)
    outside(path, context.get('repository', context['checkout']))
    value = read(path)
    version = 2 if artifact.get('preparation_version', 1) >= 3 else 1
    if (value.get('verification_version') != version
            or not compatible_reference(value, artifact)
            or not text(value.get('preparation_id'))
            or value.get('head_sha') != artifact['identity']['head_sha']
            or value.get('pr_url') != artifact['identity']['pr_url']):
        raise ValueError('engineering verification identity is stale')
    checks = value.get('checks')
    if not isinstance(checks, dict) or set(checks) != {
            'behavior', 'placement', 'test_sufficiency', 'compatibility', 'complexity'}:
        raise ValueError('engineering verification must assess all completion criteria')
    for item in checks.values():
        if (not isinstance(item, dict) or item.get('satisfied') is not True
                or not text(item.get('rationale')) or not isinstance(item.get('evidence'), list)
                or not item['evidence']):
            raise ValueError('engineering completion criterion remains unverified')
        for source in item['evidence']:
            if (not isinstance(source, dict) or not Path(source.get('path', '')).is_absolute()
                    or file_hash(source['path']) != source.get('sha256')):
                raise ValueError('engineering verification evidence changed or is missing')
    return value
