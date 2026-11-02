"""Owned reconstruction of an existing preparation's collection manifest."""
import copy
from pathlib import Path
import tempfile

from shared.private_state import read, write, mutate
from shared.workflow_storage import owned
from shared import preparation_graph as graph
from shared.engineering_identity import engineering_id


def refresh(record_path, previous, context_path, decisions_path, inline_path,
            conversation_path, api, *, replacements=None, analyzer=None):
    prep = api.preparation_api
    record_path = Path(record_path)
    record = read(record_path, {})
    parent = prep.historical(previous)
    if record.get('preparation', {}).get('id') != parent['preparation_id']:
        raise ValueError('refresh requires the selected active preparation')
    if parent.get('preparation_version') != 4:
        raise ValueError('older active preparations use their retained runtime')
    context = prep.read(context_path)
    old_context = prep.read(prep.source_path(parent, 'context'))
    if context['url'] != parent['identity']['pr_url']:
        raise ValueError('refresh context belongs to another PR')
    decisions_raw = prep.read(decisions_path)
    decisions = decisions_raw.get('data', decisions_raw)
    old_raw = prep.read(prep.source_path(parent, 'decisions'))
    old_decisions = old_raw.get('data', old_raw)
    feedback = [prep.read(inline_path), prep.read(conversation_path)]
    if not (feedback[0].get('schemaVersion') == 1 and 'schemaVersion' not in feedback[1]):
        raise ValueError('refresh requires complete inline and conversation snapshots')
    for value in feedback:
        graph.feedback(value, pr_url=context['url'], head_sha=context['head_sha'])
    replacements = replacements or {}
    allowed = {key for key in parent['sources'] if key.split('/')[0] in prep.EVIDENCE}
    if (not isinstance(replacements, dict) or replacements.keys() - allowed
            or any(not isinstance(path, str) or not path for path in replacements.values())):
        raise ValueError('source additions/removals require the explicit manifest workflow')
    if any(key.split('/')[0] in {'feedback', 'run_history', 'reviewers', 'prior_preparation'}
           for key in replacements):
        raise ValueError('managed source or role changes require the explicit manifest workflow')
    directory = Path(tempfile.mkdtemp(prefix='refresh-inputs-', dir=owned(parent['workflow_dir'])))
    workspace = directory / 'checkout'
    prep.git(context['checkout'], 'clone', '--quiet', '--no-hardlinks', '--no-checkout',
             context['checkout'], str(workspace))
    prep.git(workspace, 'checkout', '--quiet', '--detach', context['head_sha'])
    context = {**context, 'checkout': str(workspace), 'workflow_dir': parent['workflow_dir']}
    write(directory / 'context.json', context)
    history = api.preparation_history(record)
    write(directory / 'history.json', history)
    write(directory / 'decisions.json', decisions_raw)
    manifest = copy.deepcopy(prep.read(parent['recovery_manifest']['path']))
    manifest.update(context=str(directory / 'context.json'), decisions=str(directory / 'decisions.json'),
                    evidence={}, source_modes={}, source_origins={})
    for key in ('manifest', 'context', 'decisions', 'metadata', 'patch'):
        manifest['source_modes'][key] = parent['sources'][key]['mode']
        if parent['sources'][key]['mode'] == 'capture':
            manifest['source_origins'][key] = parent['sources'][key]['origin_identity']
    for category in prep.EVIDENCE:
        keys = [key for key in parent['sources'] if key.startswith(category + '/')]
        if category == 'reviewers':
            keys.sort(key=lambda key: parent['reviewer_order'].index(
                prep.read(prep.source_path(parent, key))['reviewer']))
        else:
            keys.sort(key=lambda key: int(key.split('/')[1]))
        manifest['evidence'][category] = []
        for index, key in enumerate(keys):
            source = parent['sources'][key]
            path = replacements.get(key) or (prep.source_path(parent, key)
                                             if source['mode'] == 'capture' else source['path'])
            content = Path(path).read_bytes()
            value = graph.decode(content)
            if category == 'feedback':
                if isinstance(value, dict) and value.get('complete') is True:
                    path = inline_path if value.get('schemaVersion') == 1 else conversation_path
                else:
                    raise ValueError('feedback source roles changed; use an explicit manifest')
            elif category == 'run_history':
                if len(keys) != 1:
                    raise ValueError('refresh requires one typed run-history source')
                path = str(directory / 'history.json')
                if isinstance(value, dict) and isinstance(value.get('source_id'), str):
                    write(Path(path), {**history, 'source_id': value['source_id']})
            elif category == 'choices' and value in (old_raw, old_decisions):
                # Only a verified mirrored context follows the current Decisions.
                data = value.get('data', value)
                if data.get('verified') is True and data.get('agreement_binding', {}).get('version') == 1:
                    path = str(directory / ('choice-' + str(index) + '.json'))
                    write(Path(path), decisions_raw if value == old_raw else decisions)
            origin_prefix = category + ':repo:'
            origin = source.get('origin_identity', '')
            repo_path = (origin[len(origin_prefix):] if origin.startswith(origin_prefix)
                         else graph.relative(source['path'], old_context['checkout']))
            if category in {'production', 'tests'} and repo_path is not None and key not in replacements:
                path = str(workspace / repo_path)
            new_key = category + '/' + str(index)
            manifest['evidence'][category].append(str(Path(path).resolve()))
            manifest['source_modes'][new_key] = source['mode']
            if source['mode'] == 'capture':
                manifest['source_origins'][new_key] = source['origin_identity']
    # Preserve retained prompt bindings and their delivery contracts.
    for key, source in parent['sources'].items():
        if key.startswith('reviewer-source/'):
            manifest['source_modes'][key] = source['mode']
            if source['mode'] == 'capture':
                manifest['source_origins'][key] = source['origin_identity']
    path = directory / 'manifest.json'
    write(path, manifest)
    result = prep.prepare(path, directory, previous_preparation=previous, analyzer=analyzer)
    def attach(current):
        if (current.get('preparation', {}).get('id') != parent['preparation_id']
                or api.preparation_history(current) != history):
            raise ValueError('run evidence changed during preparation refresh; result remains unattached')
        api.attach_preparation(current, result['artifact'], context['url'], context['head_sha'],
                               expected_parent=parent['preparation_id'])
    mutate(record_path, {}, attach)
    current = prep.read(result['artifact'])
    return {**result, 'status': 'refreshed' if result['readiness'] == 'ready' else 'blocked',
            'changed_governing_inputs': result['refresh']['changed_sources'],
            'model_call_count': result['refresh']['model_call_count'],
            'coverage_requirements': {'head_changed': parent['identity']['head_sha'] != context['head_sha'],
                'engineering_changed': engineering_id(parent) != engineering_id(current),
                'fresh_live_checks': True}}
