#!/usr/bin/env python3
"""Prepare, submit and reconcile a complete pinned inline review."""
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import argparse
import codecs
from collections import Counter
import json
import re
import subprocess

from shared import publication
from shared.publication_github import identity, api, pages, pr_snapshot
from shared.github_reads import run
from shared.diagnostics import Diagnostic, emit, preparation_failure

QUERY = ('mutation($input:AddPullRequestReviewInput!){addPullRequestReview(input:$input)'
         '{pullRequestReview{id fullDatabaseId url state body commit{oid}}}}')
STATES = {'COMMENT': 'COMMENTED', 'APPROVE': 'APPROVED', 'REQUEST_CHANGES': 'CHANGES_REQUESTED'}


def locations(patch):
    result, path, oldpath, old, new = set(), None, None, None, None
    def filename(raw):
        value = raw[4:].split('\t', 1)[0]
        if value.startswith('"') and value.endswith('"'):
            value = codecs.escape_decode(value[1:-1].encode('utf-8', 'surrogateescape'))[0].decode('utf-8', 'surrogateescape')
        return value[2:] if value.startswith(('a/', 'b/')) else None
    for line in patch.splitlines():
        if line.startswith('diff --git '):
            path, oldpath, old, new = None, None, None, None
        elif old is None and line.startswith('--- '):
            oldpath = filename(line)
        elif old is None and line.startswith('+++ '):
            path = filename(line) or oldpath
        elif line.startswith('@@ '):
            match = re.match(r'@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
            if not match:
                raise ValueError('malformed diff hunk')
            old, new = map(int, match.groups())
        elif path is not None and old is not None:
            if line.startswith('+'):
                result.add((path, new, 'RIGHT')); new += 1
            elif line.startswith('-'):
                result.add((path, old, 'LEFT')); old += 1
            elif line.startswith(' '):
                result.add((path, old, 'LEFT')); result.add((path, new, 'RIGHT'))
                old += 1; new += 1
    return result


def validate(payload, metadata, head, patch, allow_summary=False):
    if not isinstance(payload, dict) or set(payload) != {'query', 'variables'}:
        raise ValueError('expected a GraphQL review payload')
    if not isinstance(payload['variables'], dict) or set(payload['variables']) != {'input'}:
        raise ValueError('review payload requires variables.input')
    value = payload['variables']['input']
    if (not isinstance(value, dict) or set(value) - {'pullRequestId', 'commitOID', 'event', 'threads', 'body'}
            or value.get('pullRequestId') != metadata.get('node_id') or value.get('commitOID') != head
            or metadata['head']['sha'] != head or value.get('event') not in STATES
            or not isinstance(value.get('threads'), list)):
        raise ValueError('review must bind the PR node, pinned head, event and complete threads')
    if not isinstance(value.get('body', ''), str) or value.get('body') and not allow_summary:
        raise ValueError('review body requires --allow-summary after an explicit request')
    if not value['threads'] and (value['event'] == 'REQUEST_CHANGES'
                                 or value['event'] == 'COMMENT' and not value.get('body')):
        raise ValueError('no inline findings to submit for the requested review action')
    valid = locations(patch)
    for item in value['threads']:
        if (not isinstance(item, dict) or set(item) - {'path', 'line', 'side', 'body', 'startLine', 'startSide'}
                or not isinstance(item.get('body'), str) or not item['body'].strip()
                or not isinstance(item.get('path'), str) or not isinstance(item.get('side'), str)
                or type(item.get('line')) is not int or (item.get('path'), item['line'], item.get('side')) not in valid):
            raise ValueError('inline finding is not anchored on the reviewed diff')
        if 'startLine' in item or 'startSide' in item:
            if (type(item.get('startLine')) is not int or item.get('startSide') != item['side']
                    or item['startLine'] > item['line'] or any((item['path'], n, item['side']) not in valid
                        for n in range(item['startLine'], item['line'] + 1))):
                raise ValueError('invalid inline finding range')
    # Own the mutation document; never forward caller-supplied executable queries.
    return {'query': QUERY, 'variables': {'input': value}}


def reviews(pr):
    host, repo, number = identity(pr)
    result = pages(host, f'repos/{repo}/pulls/{number}/reviews')
    for review in result:
        review['comments'] = pages(host, f'repos/{repo}/pulls/{number}/reviews/{review["id"]}/comments')
    return result


def prepare(pr, head, payload, allow_summary=False):
    metadata = pr_snapshot(pr)
    host, _, _ = identity(pr)
    author = api(host, 'user')['login']
    patch = run(['gh', 'pr', 'diff', pr], text=True, capture_output=True, check=True).stdout
    payload = validate(payload, metadata, head, patch, allow_summary)
    existing = reviews(pr)
    if pr_snapshot(pr)['head']['sha'] != head:
        raise ValueError('PR head changed during review preparation')
    return publication.prepare('post-review', pr, {'head': head, 'base': metadata['base']['sha'],
        'author': author, 'reviews': existing, 'patch': patch, 'patch_hash': publication.digest(patch)},
        {'payload': payload, 'payload_hash': publication.digest(payload)},
        [sys.executable, Path(__file__).resolve(), 'reconcile'])


def signature(comment):
    return tuple(comment.get(k) for k in ('path', 'line', 'side', 'body', 'start_line', 'start_side'))


def matches(review, request):
    value = request['approved']['payload']['variables']['input']
    expected = request['expected']
    comments = [dict(path=t['path'], line=t['line'], side=t['side'], body=t['body'],
                     start_line=t.get('startLine'), start_side=t.get('startSide')) for t in value['threads']]
    return (review.get('user', {}).get('login', '').lower() == expected['author'].lower()
            and review.get('commit_id') == expected['head'] and review.get('state') == STATES[value['event']]
            and (review.get('body') or '') == value.get('body', '')
            and Counter(map(signature, review['comments'])) == Counter(map(signature, comments)))


def publish(path, reconcile=False):
    def check(value, verified):
        request = value['request']
        if publication.digest(request['approved']['payload']) != request['approved']['payload_hash']:
            raise ValueError('review payload changed')
        live = pr_snapshot(request['target'])
        if (live['head']['sha'] != request['expected']['head'] or live['base']['sha'] != request['expected']['base']
                or publication.digest(request['expected']['patch']) != request['expected']['patch_hash']):
            raise ValueError('PR head, base or reviewed diff changed; refresh the review')
        if not verified and reviews(request['target']) != request['expected']['reviews']:
            raise ValueError('existing reviews changed; prepare again before submitting')

    def send(value):
        return api(identity(value['request']['target'])[0], 'graphql',
                   payload=value['request']['approved']['payload'])

    def observe(value):
        request = value['request']
        existing = {r['id'] for r in request['expected']['reviews']}
        response = (value.get('response') or {}).get('data', {}).get('addPullRequestReview', {}).get('pullRequestReview') or {}
        if not reconcile and value['status'] != 'verified' and (not response.get('id') or not response.get('fullDatabaseId')
                or response.get('state') != STATES[request['approved']['payload']['variables']['input']['event']]):
            raise ValueError('submission response is missing a complete submitted review; reconcile by live match')
        review_id = response.get('fullDatabaseId')
        candidates = [r for r in reviews(request['target']) if r['id'] not in existing
                      and r.get('user', {}).get('login', '').lower() == request['expected']['author'].lower()
                      and r.get('commit_id') == request['expected']['head']]
        if review_id:
            candidates = [r for r in candidates if str(r['id']) == str(review_id)]
        exact = [r for r in candidates if matches(r, request)]
        if len(exact) != 1 or len(candidates) != 1:
            raise ValueError('review has no unique complete submitted match; inspect retained snapshot')
        review = exact[0]
        return {'id': review['id'], 'url': review['html_url'], 'comments': len(review['comments']),
                'head': request['expected']['head']}

    return publication.execute(path, 'post-review', check=check, send=send, observe=observe, reconcile=reconcile)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pr')
    sub = p.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--head', required=True)
    prep.add_argument('--payload', required=True)
    prep.add_argument('--allow-summary', action='store_true')
    for name in ('publish', 'reconcile'):
        sub.add_parser(name).add_argument('--operation', required=True)
    args = p.parse_args()
    if args.command == 'prepare':
        if not args.pr:
            p.error('--pr is required for prepare')
        result = prepare(args.pr, args.head, json.loads(Path(args.payload).read_text()), args.allow_summary)
    else:
        publication.require_target(args.operation, 'post-review', args.pr)
        result = publish(args.operation, args.command == 'reconcile')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Diagnostic as exc:
        emit(exc); sys.exit(1)
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as exc:
        emit(preparation_failure(exc, sys.argv))
        print(str(exc), file=sys.stderr); sys.exit(1)
