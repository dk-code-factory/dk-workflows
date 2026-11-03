"""Model-facing projections; the complete preparation still owns every gate."""
import copy
from shared.codex_profiles import SETTINGS, MODEL_FIELDS


def strings(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def model_view(prepared):
    """Remove known transport bookkeeping, retaining unfamiliar fields and shapes."""
    result = copy.deepcopy(prepared)
    for key in ('workflow_dir', 'workspace'):
        if isinstance(result.get(key), str):
            result.pop(key)
    if strings(result.get('reviewer_paths')):
        result.pop('reviewer_paths')
    for key in ('recovery_manifest', 'recovery_context'):
        value = result.get(key)
        if (isinstance(value, dict) and set(value) == {'path', 'sha256'}
                and all(isinstance(item, str) for item in value.values())):
            result.pop(key)
    for source in result.get('sources', {}).values():
        # An immutable snapshot replaces the duplicated original transport path.
        # Preserve the original shape when an extension could depend on it.
        if (set(source) <= {'path', 'snapshot', 'sha256', 'mode', 'origin_identity', 'reviewer', 'derived_from'}
                and isinstance(source.get('snapshot'), str) and source['snapshot']):
            source['path'] = source.pop('snapshot')
    report = result.get('refresh')
    if isinstance(report, dict):
        for key in ('changed_paths', 'changed_sources', 'unmapped_changes', 'changed_bindings',
                    'affected_conclusions', 'reused_conclusions'):
            if strings(report.get(key)):
                report.pop(key)
        if isinstance(report.get('mode'), str):
            report.pop('mode')
        if type(report.get('model_call_count')) is int and report['model_call_count'] >= 0:
            report.pop('model_call_count')
        identity = report.get('identity_changes')
        if (isinstance(identity, dict) and set(identity) <= {
                'pr_url', 'base_sha', 'head_sha', 'body_hash', 'decisions_hash'}
                and all(isinstance(item, str) for item in identity.values())):
            report.pop('identity_changes')
        if report.get('phases') == []:
            report.pop('phases')
        if not report:
            result.pop('refresh')
    return result


def reviewer_view(prepared, reviewer):
    """A git-diff-v2 guidance view, never a replacement preparation artifact."""
    result = model_view(prepared)
    result['analysis']['reviewer_addenda'] = {
        reviewer: result['analysis']['reviewer_addenda'][reviewer]}
    # Graphs are runtime scheduling metadata; their full form remains in the
    # fingerprinted original, including nodes for other reviewers' addenda.
    result.pop('graph', None)
    result.pop('retired_ids', None)
    descriptor_fields = {'descriptor_version', 'reviewer', 'complete', 'missing',
                         'sources', 'execution', 'effective_prompt', 'aspects', 'handoff_contract'}
    execution_fields = {'binding', 'identity', 'executor', 'plugin_id', 'root', 'procedure', 'version',
                        'origin_root', 'bundle_digest', 'argv', 'bundles', 'codex_execution'}
    def known_identity(value):
        if value is None:
            return True
        if (not isinstance(value, dict) or set(value) != {'version', 'workspace', 'paths',
                'layers', 'provider', 'baseline', 'exact_sha256'} or value['version'] != 1
                or not isinstance(value['workspace'], str) or not isinstance(value['exact_sha256'], str)
                or not isinstance(value['paths'], dict) or not isinstance(value['layers'], list)):
            return False
        if any(not isinstance(path, str) or not isinstance(item, dict) or set(item) not in ({'relative', 'target'}, {'external_target'})
               or any(not isinstance(field, str) for field in item.values())
               for path, item in value['paths'].items()):
            return False
        if any(not isinstance(item, dict) or set(item) != {'name', 'version', 'config_sha256',
                'comparison_sha256', 'source_sha256', 'projected', 'source_path', 'source_target'} or not isinstance(item['name'], dict)
                or set(item['name']) - {'type', 'file', 'profile', 'dotCodexFolder', 'domain', 'key', 'id', 'name'}
                or any(field is not None and not isinstance(field, str) for field in item['name'].values())
                or any(not isinstance(item[key], str) for key in ('version', 'config_sha256', 'comparison_sha256'))
                or item['source_sha256'] is not None and not isinstance(item['source_sha256'], str)
                or any(item[field] is not None and not isinstance(item[field], str)
                       for field in ('source_path', 'source_target'))
                or type(item['projected']) is not bool for item in value['layers']):
            return False
        provider = value['provider']
        if provider is not None and (not isinstance(provider, dict) or set(provider) != {'exact', 'comparison'}
                                     or any(not isinstance(field, str) for field in provider.values())):
            return False
        return (isinstance(value['baseline'], dict) and set(value['baseline']) == {
            'config', 'comparison', 'origins', 'comparison_origins'}
            and all(isinstance(field, str) for field in value['baseline'].values()))

    def known_profile(value):
        if value is None:
            return True
        def source(item):
            return (item is None or isinstance(item, dict)
                and set(item) <= {'name', 'version'} and isinstance(item.get('name'), dict)
                and set(item['name']) <= {'type', 'file', 'profile', 'dotCodexFolder',
                                         'domain', 'key', 'id', 'name'}
                and all(value is None or isinstance(value, str) for value in item['name'].values())
                and isinstance(item.get('version'), str))
        return (isinstance(value, dict) and set(value) <= {
            'profile', 'profile_source', 'codex_version', 'settings', 'origins', 'setting_sources',
            'effective_settings', 'expected_observations', 'catalog', 'provider', 'layers', 'requirements_sha256', 'identity_evidence'}
            and known_identity(value.get('identity_evidence'))
            and isinstance(value.get('profile'), str) and isinstance(value.get('codex_version'), str)
            and isinstance(value.get('settings'), dict) and set(value['settings']) <= set(SETTINGS)
            and all(item is None or type(item) in (str, int, bool) for item in value['settings'].values())
            and isinstance(value.get('origins'), dict) and set(value['origins']) <= set(SETTINGS)
            and all(source(item) for item in value['origins'].values())
            and isinstance(value.get('setting_sources'), dict)
            and set(value['setting_sources']) <= {'model_catalog_json', 'model_instructions_file'}
            and all(isinstance(item, dict) and set(item) <= {'path', 'resolved_path', 'sha256'}
                    and all(isinstance(field, str) for field in item.values())
                    for item in value['setting_sources'].values())
            and source(value.get('profile_source'))
            and all(isinstance(value.get(key), dict) and set(value[key]) <= set(SETTINGS) | {'model_usable_context_window'}
                    and all(item is None or type(item) in (str, int, bool) for item in value[key].values())
                    for key in ('effective_settings', 'expected_observations'))
            and isinstance(value.get('layers'), list)
            and all(isinstance(item, dict) and set(item) <= {'name', 'version', 'disabled'}
                    and source({k: v for k, v in item.items() if k != 'disabled'})
                    and isinstance(item.get('disabled'), bool) for item in value['layers'])
            and isinstance(value.get('requirements_sha256'), str)
            and isinstance(value.get('provider', {}), dict)
            and set(value.get('provider', {})) <= {'id', 'definition_sha256', 'environment_sha256'}
            and all(isinstance(item, str) for item in value.get('provider', {}).values())
            and isinstance(value.get('catalog'), dict)
            and set(value['catalog']) <= {'sha256', 'selected_row_sha256', 'metadata', 'origin', 'route', 'selection'}
            and isinstance(value['catalog'].get('metadata', {}), dict)
            and set(value['catalog'].get('metadata', {})) <= set(MODEL_FIELDS)
            and all(key == 'metadata' or isinstance(item, str) for key, item in value['catalog'].items())
            and all(key == 'supported_reasoning_levels' or item is None or type(item) in (str, int)
                    for key, item in value['catalog'].get('metadata', {}).items())
            and all(isinstance(item, dict) and set(item) <= {'effort', 'description'}
                    and all(isinstance(field, str) for field in item.values())
                    for item in value['catalog'].get('metadata', {}).get('supported_reasoning_levels') or []))
    def known_descriptor(descriptor):
        execution = descriptor.get('execution') or {}
        return (set(descriptor) <= descriptor_fields and strings(descriptor.get('aspects'))
                and all(isinstance(source, dict) and set(source) <= {'path', 'sha256'}
                        for source in descriptor['sources'])
                and isinstance(execution, dict)
                and set(execution) <= execution_fields
                and known_profile(execution.get('codex_execution'))
                and set(execution.get('binding', {})) <= {'skill', 'host', 'command', 'handoff_contract', 'codex_profile'}
                and all(isinstance(bundle, dict) and set(bundle) <= {'root', 'digest'}
                        for bundle in execution.get('bundles', [])))
    result['reviewers'] = {name: descriptor for name, descriptor in result['reviewers'].items()
                           if not known_descriptor(descriptor)}
    return result
