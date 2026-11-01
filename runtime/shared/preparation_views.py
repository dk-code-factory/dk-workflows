"""Model-facing projections; the complete preparation still owns every gate."""
import copy


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
        if (set(source) <= {'path', 'snapshot', 'sha256', 'mode', 'origin_identity'}
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
                        'origin_root', 'bundle_digest', 'argv', 'bundles'}
    def known_descriptor(descriptor):
        execution = descriptor.get('execution') or {}
        return (set(descriptor) <= descriptor_fields and strings(descriptor.get('aspects'))
                and all(isinstance(source, dict) and set(source) <= {'path', 'sha256'}
                        for source in descriptor['sources'])
                and isinstance(execution, dict)
                and set(execution) <= execution_fields
                and set(execution.get('binding', {})) <= {'skill', 'host', 'command', 'handoff_contract'}
                and all(isinstance(bundle, dict) and set(bundle) <= {'root', 'digest'}
                        for bundle in execution.get('bundles', [])))
    result['reviewers'] = {name: descriptor for name, descriptor in result['reviewers'].items()
                           if not known_descriptor(descriptor)}
    return result
