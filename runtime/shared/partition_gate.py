"""Keep partition work ahead of review allocation and iteration completion."""
from pathlib import Path
from urllib.parse import urlparse

from shared import pr_partition, pr_size, publication, pr_partition_policy
from shared.engineering_identity import engineering_id


def settings(prepared, context):
    parsed = urlparse(prepared['identity']['pr_url'])
    base = 'remote:' + parsed.hostname.lower() + '/' + '/'.join(parsed.path.strip('/').split('/')[:2]).lower()
    head = context['repository_identity']
    return pr_size.load(list(dict.fromkeys((head, base))))


def attach(record, prepared, context, path):
    data = pr_partition.verify(path)
    snap = data['snapshot']
    if (snap['binding']['pr'] != prepared['identity']['pr_url'] or
            snap['binding']['tree'] != pr_partition.git(context['checkout'], 'rev-parse',
                prepared['identity']['head_sha'] + '^{tree}')):
        raise ValueError('partition assessment differs from current prepared scope')
    # Registration is an assessment of the active engineering preparation, not a
    # way to transplant a completed judgment to different obligations.
    if data.get('engineering_id') != engineering_id(prepared):
        raise ValueError('partition requires the current engineering preparation ID')
    record['partition_assessment'] = {'path': str(Path(path).resolve()),
                                    'engineering_id': engineering_id(prepared), 'key': snap['key']}
    return record['partition_assessment']


def require(record, prepared, context):
    # A new preference cannot erase dispatched writes or uncertain receipts.
    pr = prepared['identity']['pr_url']
    if pr_partition.pending_states(context.get('repository', context['checkout']), pr):
        raise ValueError('PR partition publication is pending; reconcile and finish it before CI or review')
    item = record.get('partition_assessment')
    data = pr_partition.load(item['path']) if item else None
    if data and any(publication.load(path)['status'] == 'pending' for path in data['operations'].values()):
        raise ValueError('PR partition publication is uncertain; reconcile it before continuing')
    policy = pr_partition_policy.load(pr)
    if not policy['enabled']:
        return {'outcome': 'pr_disabled', 'partition_policy': policy}
    if data and data['snapshot']['binding'].get('partition_policy') != pr_partition_policy.binding(pr):
        item = data = None  # Reassess new human instructions; never reuse an old interpretation.
    config = settings(prepared, context)
    values = config['pr_size']
    if all(values[key] == 'disabled' for key in pr_partition.BEHAVIORS):
        return {'outcome': 'disabled'}
    cwd = context['checkout']
    base = pr_partition.git(cwd, 'merge-base', prepared['identity']['base_sha'], prepared['identity']['head_sha'])
    added = pr_size.additions(cwd, base, prepared['identity']['head_sha'])
    if not policy['instructions'] and added < values['added_lines_threshold']:
        return {'outcome': 'below_threshold', 'added_lines': added}
    if data and data['status'] not in ('no_op', 'declined', 'published'):
        raise ValueError('PR partition is pending; finish or reconcile it before CI or review')
    if not item or item['engineering_id'] != engineering_id(prepared):
        raise ValueError('PR size assessment required for current scope before review or completion')
    if data['snapshot']['key'] != item['key'] or data['snapshot']['binding']['settings'] != config:
        raise ValueError('PR size assessment settings or evidence changed')
    if data['status'] == 'published':
        raise ValueError('partition changed scope; refresh preparation and reassess the retained PR')
    pr_partition.verify(item['path'])
    return {'outcome': data['status'], 'added_lines': added, 'state': item['path'], 'partition_policy': policy}
