"""Conservative structural gates for independently assessed discussion additions."""
from pathlib import Path

from shared.private_state import read


def additions(original, current):
    def strict(value):
        return {k: v for k, v in value.items()
                if k not in {'head_sha', 'discussion_decisions', 'decisions'}}
    if original.get('verified') is not True or current.get('verified') is not True or strict(original) != strict(current):
        raise ValueError('Decisions identities or human choices changed')
    old, new = original.get('discussion_decisions', []), current.get('discussion_decisions', [])
    if any(item not in new for item in old) or [item for item in new if item in old] != old:
        raise ValueError('Decisions removed, reordered or superseded')
    extra = {f'/discussion_decisions/{i}': item for i, item in enumerate(new) if item not in old}
    for item in extra.values():
        if (not isinstance(item, dict) or set(item) != {'text', 'references'}
                or not isinstance(item['text'], str) or not item['text'].strip()
                or not isinstance(item['references'], list) or not item['references']
                or any(not isinstance(ref, str) or not ref.startswith('https://') for ref in item['references'])):
            raise ValueError('added discussion decision lacks verified references')
    old_records, records = original.get('decisions', []), current.get('decisions', [])
    if any(item not in records for item in old_records) or [item for item in records if item in old_records] != old_records:
        raise ValueError('active Decisions changed or were superseded')
    for item in records:
        if item in old_records:
            continue
        if (item.get('source_type') != 'discussion' or item.get('status') != 'active'
                or item.get('superseded_by') is not None
                or not any(item.get('text') == addition['text'] and item.get('references') == addition['references']
                           for addition in extra.values())):
            raise ValueError('new Decisions are not discussion restatements')
    return extra


def discussion_sources(value, url):
    """Only retained discussion bodies with their own URL establish provenance."""
    if isinstance(value, dict):
        text = value.get('text', value.get('body'))
        if (value.get('url', value.get('html_url')) == url
                and isinstance(text, str) and text.strip()):
            return True
        return any(discussion_sources(item, url) for item in value.values())
    return isinstance(value, list) and any(discussion_sources(item, url) for item in value)


def index(original, current, sources, original_analysis):
    extra = additions(original, current)
    targets = []
    keys = ('decisions',) if 'decisions' in original else ('local_decisions', 'discussion_decisions')
    for key in keys:
        targets.extend(f'original/decisions#/{key}/{i}' for i, item in enumerate(original.get(key, []))
                       if item.get('status', 'active' if key != 'decisions' else None) == 'active')
    if original.get('live_decisions') and 'decisions' not in original:
        targets.append('original/decisions#/live_decisions')
    targets.extend(f'original/analysis#/boundaries/{name}/{i}'
                   for name, entries in original_analysis['boundaries'].items() for i in range(len(entries)))
    indexed = {}
    for pointer, item in extra.items():
        refs = {}
        for url in item['references']:
            found = []
            for key, source in sources.items():
                if key.split('/')[0] != 'feedback':
                    continue
                try:
                    value = read(Path(source['path']), {})
                except ValueError:
                    continue
                if discussion_sources(value, url):
                    found.append('current/' + key)
            if not found:
                raise ValueError('discussion restatement source body is missing: ' + url)
            refs[url] = found
        indexed[pointer] = {'decision': item, 'discussion_sources': refs}
    return indexed, targets


def choices_match(key, old_sources, sources, api):
    if key not in old_sources or key not in sources:
        return False
    return (api.read(old_sources[key]['path']) == api.read(old_sources['decisions']['path'])
            and api.read(sources[key]['path']) == api.read(sources['decisions']['path']))
