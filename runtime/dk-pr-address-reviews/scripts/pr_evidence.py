#!/usr/bin/env python3
"""Collect, inspect and freshly verify shared complete PR evidence artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
from shared.private_state import atomic_bytes
from shared.workflow_storage import allocate, owned
import fetch_pr_conversation as conversation
import fetch_unresolved_review_threads as threads

ARTIFACT = 'dk-pr-snapshot-v1'
HANDOFF = 'dk-pr-evidence-v1'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def identity(snapshot):
    pr = snapshot.get('pullRequest')
    if not isinstance(pr, dict):
        raise ValueError('snapshot lacks PR identity')
    url = pr.get('url')
    if not isinstance(url, str):
        raise ValueError('snapshot lacks canonical PR URL')
    parsed = urlsplit(url)
    parts = parsed.path.strip('/').split('/')
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port or parsed.query or parsed.fragment or len(parts) != 4
            or parts[2] != 'pull' or not re.fullmatch(r'[1-9][0-9]*', parts[3])
            or any(not re.fullmatch(r'[A-Za-z0-9_.-]+', p) or p in {'.', '..'} for p in parts[:2])):
        raise ValueError('invalid canonical PR URL')
    head = pr.get('headRefOid')
    if not isinstance(head, str) or not head:
        raise ValueError('snapshot lacks captured head')
    repository = '/'.join([parsed.hostname, *parts[:2]]).lower()
    return {'repository': repository, 'pr': f'https://{repository}/pull/{int(parts[3])}',
            'head': head}


def validate(snapshot, kind):
    if not isinstance(snapshot, dict) or snapshot.get('complete') is not True:
        raise ValueError('complete snapshot required')
    subject = identity(snapshot)
    items = snapshot.get('items')
    if not isinstance(items, list) or snapshot.get('itemCount') != len(items):
        raise ValueError('snapshot item count is invalid')
    if kind == 'threads':
        metadata = snapshot['pullRequest']
        required_metadata = {'number', 'url', 'state', 'title', 'description', 'headRefName',
                             'headRefOid', 'headRepository', 'baseRefName', 'baseRefOid',
                             'closingIssuesReferences', 'isCrossRepository'}
        if (not required_metadata.issubset(metadata)
                or not isinstance(metadata['title'], str) or not metadata['title']
                or not isinstance(metadata['description'], str)):
            raise ValueError('thread snapshot PR metadata is incomplete or invalid')
        values = snapshot.get('threads')
        if (snapshot.get('schemaVersion') != 1 or not isinstance(values, list)
                or snapshot.get('threadCount') != len(values)
                or snapshot.get('threadsSha256') != digest(values)
                or snapshot.get('itemsSha256') != digest(items)
                or snapshot['pullRequest'].get('number') != int(subject['pr'].rsplit('/', 1)[1])
                or snapshot.get('repository', '').lower() != subject['repository'].split('/', 1)[1]):
            raise ValueError('thread snapshot schema, identity or hash is invalid')
        if (any(not isinstance(t, dict) or type(t.get('isResolved')) is not bool
                or not isinstance(t.get('comments'), list) for t in values)
                or len({t.get('id') for t in values}) != len(values)
                or threads.prepare_items(values) != items):
            raise ValueError('thread snapshot derived view is invalid')
        if any(not isinstance(c, dict) or not isinstance(c.get('reference'), str)
               or not isinstance(c.get('text'), str) for t in values for c in t['comments']):
            raise ValueError('thread source is invalid')
        if 'selection' in snapshot:
            selected = snapshot['selection']
            if not isinstance(selected, dict):
                raise ValueError('invalid thread selection')
            reference = selected.get('commentReference') or selected.get('reviewReference')
            if threads.selection_for(reference, values) != selected:
                raise ValueError('thread selection disagrees with source')
    elif kind == 'conversation':
        reviews, comments = snapshot.get('reviews'), snapshot.get('comments')
        unsigned = {key: value for key, value in snapshot.items() if key != 'sha256'}
        if (not isinstance(reviews, list) or not isinstance(comments, list)
                or snapshot.get('reviewCount') != len(reviews)
                or snapshot.get('commentCount') != len(comments)
                or items != reviews + comments or snapshot.get('sha256') != digest(unsigned)):
            raise ValueError('conversation snapshot schema or hash is invalid')
        if any(not isinstance(item, dict) or not isinstance(item.get('reference'), str)
               or not isinstance(item.get('text'), str) for item in items):
            raise ValueError('conversation source is invalid')
        if len({item['reference'] for item in items}) != len(items):
            raise ValueError('duplicate conversation source')
    else:
        raise ValueError('unknown snapshot kind')
    return subject


def source_references(snapshot, kind):
    return ([{'reference': t['id'], 'comments': [c['reference'] for c in t['comments']]}
             for t in snapshot['threads']] if kind == 'threads' else
            [item['reference'] for item in snapshot['items']])


def write_artifact(snapshot, kind, cwd):
    subject = validate(snapshot, kind)
    directory = allocate(repository=cwd)
    path = directory / (kind + '.json')
    content = encoded(snapshot)
    atomic_bytes(path, content)
    refs = source_references(snapshot, kind)
    return {'contract': ARTIFACT, 'kind': kind, 'complete': True, **subject,
            'path': str(path), 'sha256': hashlib.sha256(content).hexdigest(),
            'bytes': len(content), 'itemCount': snapshot['itemCount'],
            **({'threadCount': snapshot['threadCount']} if kind == 'threads' else
               {'reviewCount': snapshot['reviewCount'], 'commentCount': snapshot['commentCount']}),
            'sourceReferences': refs}


def load_artifact(receipt, kind, cwd):
    if (not isinstance(receipt, dict) or receipt.get('contract') != ARTIFACT
            or receipt.get('kind') != kind or receipt.get('complete') is not True):
        raise ValueError('invalid snapshot artifact receipt')
    path = owned(receipt['path'])
    # A private-state environment redirected into this checkout is never acceptable.
    from shared.repository_scope import inside_checkout
    if inside_checkout(path, cwd) or path.is_relative_to(Path(__file__).resolve().parents[3]):
        raise ValueError('snapshot artifacts must stay outside repositories and the plugin')
    content = path.read_bytes()
    if len(content) != receipt.get('bytes') or hashlib.sha256(content).hexdigest() != receipt.get('sha256'):
        raise ValueError('snapshot artifact hash or size is invalid')
    snapshot = json.loads(content)
    subject = validate(snapshot, kind)
    if any(receipt.get(key) != value for key, value in subject.items()):
        raise ValueError('snapshot artifact identity disagrees with receipt')
    counts = ['itemCount', 'threadCount'] if kind == 'threads' else ['itemCount', 'reviewCount', 'commentCount']
    if (any(receipt.get(key) != snapshot[key] for key in counts)
            or receipt.get('sourceReferences') != source_references(snapshot, kind)):
        raise ValueError('snapshot artifact source references or counts disagree with receipt')
    return snapshot


def supplied(path, kind, cwd):
    value = json.loads(Path(path).read_bytes())
    if isinstance(value, dict) and value.get('contract') == ARTIFACT:
        return load_artifact(value, kind, cwd)
    validate(value, kind)
    return value


def collect(pr, cwd, *, thread_file=None, conversation_file=None, instruction_files=()):
    inline = supplied(thread_file, 'threads', cwd) if thread_file else threads.fetch_stable_snapshot(pr, cwd)
    subject = validate(inline, 'threads')
    # Supplied files must match the explicit task, not merely each other.
    if identity({'pullRequest': {'url': pr, 'headRefOid': subject['head']}}) != subject:
        raise ValueError('supplied evidence belongs to another PR')
    top = (supplied(conversation_file, 'conversation', cwd) if conversation_file else
           conversation.fetch(inline, cwd))
    if validate(top, 'conversation') != subject:
        raise ValueError('supplied snapshots disagree on PR or head')
    directory = allocate(repository=cwd)
    instructions = []
    for index, source in enumerate(instruction_files):
        # Keep the lookup path so later symlink changes refresh the governing source.
        original = Path(source).absolute()
        content = original.read_bytes()
        path = directory / f'instructions-{index}.txt'
        atomic_bytes(path, content)
        instructions.append({'source': str(original), 'path': str(path), 'bytes': len(content),
                             'sha256': hashlib.sha256(content).hexdigest()})
    handoff = {'contract': HANDOFF, 'complete': True, **subject,
               'snapshots': {'threads': write_artifact(inline, 'threads', cwd),
                             'conversation': write_artifact(top, 'conversation', cwd)},
               'instructions': instructions}
    path = directory / 'evidence.json'
    atomic_bytes(path, encoded(handoff))
    return {'contract': HANDOFF, 'complete': True, **subject, 'path': str(path),
            'sha256': digest(handoff), 'snapshots': handoff['snapshots'],
            'instructionCount': len(instructions)}


def load_handoff(path, cwd, *, sha256, pr=None, head=None):
    content = owned(path).read_bytes()
    if hashlib.sha256(content).hexdigest() != sha256:
        raise ValueError('evidence handoff hash is invalid')
    value = json.loads(content)
    if not isinstance(value, dict) or value.get('contract') != HANDOFF or value.get('complete') is not True:
        raise ValueError('invalid evidence handoff')
    inline = load_artifact(value['snapshots']['threads'], 'threads', cwd)
    top = load_artifact(value['snapshots']['conversation'], 'conversation', cwd)
    subject = validate(inline, 'threads')
    if (validate(top, 'conversation') != subject or
            any(value.get(key) != item for key, item in subject.items())):
        raise ValueError('handoff snapshots disagree on PR or head')
    if pr and identity({'pullRequest': {'url': pr, 'headRefOid': subject['head']}}) != subject:
        raise ValueError('handoff belongs to another PR')
    if head and head != subject['head']:
        raise ValueError('handoff captured another head')
    for source in value['instructions']:
        original = owned(source['path']).read_bytes()
        if (len(original) != source['bytes'] or hashlib.sha256(original).hexdigest() != source['sha256']):
            raise ValueError('instruction artifact hash or size is invalid')
    return value, inline, top


def view(path, role, cwd, *, sha256, pr=None, head=None):
    handoff, inline, top = load_handoff(path, cwd, sha256=sha256, pr=pr, head=head)
    # Complete discussion is retained for settled/deferred choices in either role.
    # Feedback's unresolved references identify work without duplicating their text.
    result = {'complete': True, 'role': role, 'pullRequest': inline['pullRequest'],
              'threads': inline['threads'], 'conversation': top['items'],
              'instructions': handoff['instructions']}
    if role == 'feedback':
        result['unresolvedReferences'] = [item['reference'] for item in inline['items']]
    if 'selection' in inline:
        result['selection'] = inline['selection']
    return result


def verify(path, cwd, *, sha256, pr=None, head=None):
    """Recollect at the acceptance boundary; a receipt never proves live freshness."""
    handoff, inline, top = load_handoff(path, cwd, sha256=sha256, pr=pr, head=head)
    current_inline = threads.fetch_stable_snapshot(handoff['pr'], cwd)
    current_top = conversation.fetch(current_inline, cwd)
    # Compare full schemas, including Decisions/description and every resolved reply.
    # Selection is caller context, not a remote field.
    old = {k: v for k, v in inline.items() if k != 'selection'}
    new = {k: v for k, v in current_inline.items() if k != 'selection'}
    if old != new or top != current_top:
        raise ValueError('PR evidence changed; recollect and reconsider before recording results')
    for source in handoff['instructions']:
        if hashlib.sha256(Path(source['source']).read_bytes()).hexdigest() != source['sha256']:
            raise ValueError('governing instructions changed; reconsider before recording results')
    return {'complete': True, 'unchanged': True, 'pr': handoff['pr'], 'head': handoff['head'],
            'publicationAuthority': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cwd', default='.')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('collect')
    p.add_argument('--pr', required=True, help='Canonical https PR URL')
    p.add_argument('--threads-file')
    p.add_argument('--conversation-file')
    p.add_argument('--instructions-file', action='append', default=[])
    for command in ('view', 'verify'):
        p = sub.add_parser(command)
        p.add_argument('--handoff', required=True)
        p.add_argument('--sha256', required=True, help='Expected handoff hash from the compact receipt')
        p.add_argument('--pr', required=True)
        p.add_argument('--head', required=True)
        if command == 'view':
            p.add_argument('--role', choices=['feedback', 'decisions'], required=True)
    args = parser.parse_args()
    cwd = Path(args.cwd).resolve()
    try:
        if args.command == 'collect':
            result = collect(args.pr, cwd, thread_file=args.threads_file,
                             conversation_file=args.conversation_file, instruction_files=args.instructions_file)
        elif args.command == 'view':
            result = view(args.handoff, args.role, cwd, sha256=args.sha256, pr=args.pr, head=args.head)
        else:
            result = verify(args.handoff, cwd, sha256=args.sha256, pr=args.pr, head=args.head)
        print(encoded(result).decode())
    except (ValueError, OSError, KeyError, TypeError, threads.FetchError, conversation.FetchError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
