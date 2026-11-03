"""Keep partition work ahead of review allocation and iteration completion."""
import json
from pathlib import Path
from urllib.parse import urlparse

from shared import pr_partition, pr_size, publication
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
    # Partial publication must remain visible after source removal shrinks the
    # diff, even if interruption preceded run-record registration.
    cache = pr_partition.cache_path(context.get('repository', context['checkout']), prepared['identity']['pr_url'])
    if cache.exists():
        for retained in set(json.loads(cache.read_text()).values()):
            prior = pr_partition.load(retained)
            if prior['operations'] and prior['status'] != 'published':
                raise ValueError('PR partition publication is pending; reconcile and finish it before CI or review')
    item = record.get('partition_assessment')
    if item:
        data = pr_partition.load(item['path'])
        if data['status'] not in ('no_op', 'declined', 'published'):
            raise ValueError('PR partition is pending; finish or reconcile it before CI or review')
        if any(publication.load(path)['status'] == 'pending' for path in data['operations'].values()):
            raise ValueError('PR partition publication is uncertain; reconcile it before continuing')
    config = settings(prepared, context)
    values = config['pr_size']
    if all(values[key] == 'disabled' for key in pr_partition.BEHAVIORS):
        return {'outcome': 'disabled'}
    cwd = context['checkout']
    base = pr_partition.git(cwd, 'merge-base', prepared['identity']['base_sha'], prepared['identity']['head_sha'])
    added = pr_size.additions(cwd, base, prepared['identity']['head_sha'])
    if added < values['added_lines_threshold']:
        return {'outcome': 'below_threshold', 'added_lines': added}
    if not item or item['engineering_id'] != engineering_id(prepared):
        raise ValueError('PR size assessment required for current scope before review or completion')
    if data['snapshot']['key'] != item['key'] or data['snapshot']['binding']['settings'] != config:
        raise ValueError('PR size assessment settings or evidence changed')
    if data['status'] == 'published':
        raise ValueError('partition changed scope; refresh preparation and reassess the retained PR')
    pr_partition.verify(item['path'])
    return {'outcome': data['status'], 'added_lines': added, 'state': item['path']}
