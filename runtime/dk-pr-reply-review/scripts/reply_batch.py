#!/usr/bin/env python3
"""Prepare and publish a source-matched reply batch approved by an independent agent."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))

import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlparse

SCRIPTS = next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir()) / 'dk-pr-address-reviews' / 'scripts'


def module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


inline = module('fetch_unresolved_review_threads')
top = module('fetch_pr_conversation')


class GuardError(ValueError):
    pass


class EvidenceChanged(GuardError):
    def __init__(self, message, changes, next_action='refresh_affected_sources_and_revalidate'):
        super().__init__(message)
        self.diagnostic = {'changes': changes, 'next_action': next_action}


def evidence_changes(expected, live):
    """Describe changed identities without copying conversations into diagnostics."""
    changes = {'evidence_digest': {'expected': digest(expected), 'actual': digest(live)}}
    for key in ('pr', 'head'):
        if expected.get(key) != live.get(key):
            changes[key] = {'expected': expected.get(key), 'actual': live.get(key)}
    scope_keys = expected.get('scope', {}).keys() | live.get('scope', {}).keys()
    changed_scope = sorted(key for key in scope_keys
                           if expected.get('scope', {}).get(key) != live.get('scope', {}).get(key))
    if changed_scope:
        changes['scope_fields'] = changed_scope

    def indexed(evidence, threads=False):
        if threads:
            return {t['id']: {k: v for k, v in t.items() if k != 'comments'}
                    for t in evidence.get('threads', [])}
        result = {s['reference']: s for s in evidence.get('items', [])}
        for thread in evidence.get('threads', []):
            result.update({s['reference']: dict(s, threadReference=thread['id'])
                           for s in thread.get('comments', [])})
        return result

    for key, is_thread in (('sources', False), ('threads', True)):
        before, after = indexed(expected, is_thread), indexed(live, is_thread)
        delta = {'added': sorted(after.keys() - before.keys()),
                 'removed': sorted(before.keys() - after.keys()),
                 'changed': sorted(ref for ref in before.keys() & after.keys() if before[ref] != after[ref])}
        if any(delta.values()):
            changes[key] = delta
    return changes


def verify_receipt(candidates, receipt, category):
    matches = [c for c in candidates if c.get('url') == receipt['url']]
    if len(matches) != 1 or matches[0]['text'] != receipt['body']:
        raise EvidenceChanged(
            f"saved reply missing or edited: {receipt['url']} [{category}]; reconcile live evidence",
            {'receipt': {'url': receipt['url'], 'category': category,
                         'status': 'missing' if not matches else ('ambiguous' if len(matches) > 1 else 'edited'),
                         'expected_sha256': digest(receipt['body']),
                         'actual_sha256': digest(matches[0]['text']) if len(matches) == 1 else None}},
            'reconcile_receipt_provenance_before_retry')
    return matches[0]


def require(ok, message):
    if not ok:
        raise GuardError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def context(snapshot, conversation):
    require(snapshot.get('complete') is True and conversation.get('complete') is True,
            'complete snapshots required')
    require(snapshot.get('itemCount') == len(snapshot['items']) and
            snapshot.get('itemsSha256') == digest(snapshot['items']), 'invalid inline snapshot')
    require(snapshot.get('threadCount') == len(snapshot['threads']) and
            snapshot.get('threadsSha256') == digest(snapshot['threads']),
            'complete all-thread context required; refresh with the current fetcher')
    require(conversation.get('sha256') == digest({k: v for k, v in conversation.items() if k != 'sha256'}),
            'invalid conversation snapshot')
    require(inline.prepare_items(snapshot['threads']) == snapshot['items'],
            'inline worklist disagrees with complete threads')
    pr = snapshot['pullRequest']
    require(all(pr.get(k) and pr[k] == conversation['pullRequest'].get(k)
                for k in ('url', 'headRefOid')), 'snapshot identity/head mismatch')
    require(conversation['items'] == conversation['reviews'] + conversation['comments'] and
            conversation['itemCount'] == len(conversation['items']), 'inconsistent conversation')
    return {'pr': pr['url'], 'head': pr['headRefOid'],
            'threads': snapshot['threads'], 'items': conversation['items'],
            'scope': {k: pr.get(k) for k in ('title', 'description', 'baseRefName',
                      'baseRefOid', 'headRefName', 'headRepository')}}


def sources(evidence):
    result = {item['reference']: item for item in evidence['items']}
    threads = {thread['id']: thread for thread in evidence['threads']}
    for thread in threads.values():
        for comment in thread['comments']:
            require(comment['reference'] not in result, 'duplicate source reference')
            result[comment['reference']] = dict(comment, kind='inline', threadReference=thread['id'])
    return result, threads


def quote(excerpt):
    return '\n'.join('> ' + line for line in excerpt.split('\n'))


def validate(batch):
    require(batch.get('schemaVersion') == 1, 'unsupported reply batch')
    evidence = context(batch['snapshot'], batch['conversation'])
    source_map, threads = sources(evidence)
    selection = batch['snapshot'].get('selection')
    if selection is not None:
        require(selection == inline.selection_for(selection['reviewReference'], evidence['threads']),
                'invalid review selection')
    requests = {}
    request_sources = set()
    for request in batch['requests']:
        key = request['id']
        require(isinstance(key, str) and key and key not in requests, 'duplicate/empty request id')
        requests[key] = request
        source = source_map.get(request['source'])
        require(source is not None, 'request source is missing')
        excerpt = request['excerpt']
        require(isinstance(excerpt, str) and excerpt.strip() and excerpt in source['text'],
                'request excerpt must be exact source text')
        identity = (request['source'], excerpt)
        require(identity not in request_sources, 'duplicate source request')
        request_sources.add(identity)
        require(request['disposition'] in ('reply', 'skip', 'held'), 'invalid disposition')
        if request['disposition'] != 'reply':
            require(bool(request.get('reason', '').strip()), 'skipped/held request needs a reason')
        target = request.get('threadReference')
        if source['kind'] == 'inline':
            require(target == source['threadReference'], 'inline source requires its matching thread')
        if target:
            require(target in threads, 'matching thread is missing')
            same_review = source['kind'] == 'review' and any(
                c.get('reviewReference') == source['reference'] for c in threads[target]['comments'])
            if source['kind'] != 'inline' and not same_review:
                matched = source_map.get(request.get('matchedCommentReference'))
                require(matched is not None and matched.get('kind') == 'inline' and
                        matched.get('threadReference') == target,
                        'cross-source mapping requires a matching comment in the destination thread')
                matched_excerpt = request.get('matchedExcerpt')
                require(isinstance(matched_excerpt, str) and matched_excerpt.strip() and
                        matched_excerpt in matched['text'],
                        'cross-source mapping requires an exact matching comment excerpt')
        elif source['kind'] == 'review':
            # Exact repeats are mechanically detectable; semantic repeats belong to the validator.
            matches = [t for t in threads.values() if any(
                c.get('reviewReference') == source['reference'] and excerpt in c['text']
                for c in t['comments'])]
            require(not matches, 'review request repeats inline feedback; map its thread')
    used = set()
    destinations = set()
    reply_ids = set()
    for reply in batch['replies']:
        require(reply['id'] not in reply_ids, 'duplicate reply id')
        reply_ids.add(reply['id'])
        ids = reply['requestIds']
        require(ids and len(ids) == len(set(ids)), 'empty/duplicate reply request ids')
        require(all(key in requests and key not in used and requests[key]['disposition'] == 'reply'
                    for key in ids), 'request duplicated, held, skipped, or missing')
        used.update(ids)
        chosen = [requests[key] for key in ids]
        dest = reply['destination']
        kind, ref = dest['kind'], dest['reference']
        if selection is not None:
            require(ref in selection['threadReferences'] if kind == 'inline' else
                    ref == selection['reviewReference'], 'reply outside selected review')
        require((kind, ref) not in destinations, 'group replies to the same destination')
        destinations.add((kind, ref))
        body = reply['body']
        require(isinstance(body, str) and body.strip(), 'empty reply')
        if kind == 'inline':
            require(ref in threads and threads[ref]['isResolved'] is False,
                    'inline destination must be an open thread')
            require(all(r.get('threadReference') == ref for r in chosen), 'nonmatching inline destination')
        elif kind == 'issue_comment':
            require(all(r['source'] == ref and not r.get('threadReference') for r in chosen),
                    'PR-level reply duplicates inline answer or mixes sources')
            source = source_map[ref]
            require(not any(item['text'] == body for item in evidence['items']),
                    'equivalent PR-level reply already exists')
            require(source['kind'] in ('review', 'issue_comment'), 'inline reply cannot fall back to PR comment')
            require(source.get('url') and body.startswith(f"[In reply to]({source['url']})\n\n"),
                    'PR-level reply requires exact source link')
            positions = []
            for request in chosen:
                quoted = quote(request['excerpt'])
                require(quoted in body.split('\n\n'), 'PR-level reply requires exact blockquoted excerpt')
                positions.append((body.index(quoted), quoted))
            positions.sort()
            for i, (start, quoted) in enumerate(positions):
                end = positions[i + 1][0] if i + 1 < len(positions) else len(body)
                answer = body[start + len(quoted):end].strip()
                require(answer and not answer.startswith('>'), 'each quoted request needs its own answer')
        else:
            raise GuardError('invalid reply destination')
    require(used == {key for key, r in requests.items() if r['disposition'] == 'reply'},
            'every reply request must have exactly one response')
    return evidence


def require_approval(batch, verdict):
    require(verdict.get('batchSha256') == digest(batch), 'approved batch changed')
    require(verdict.get('status') == 'approved' and verdict.get('corrections') == [],
            'validator rejected the batch or left corrections')
    require(isinstance(verdict.get('validatorAgent'), str) and verdict['validatorAgent'].strip(),
            'independent validator identity required')
    require(verdict.get('pass') in (1, 2), 'at most two validator passes permitted')


def fetch_live(pr, cwd):
    snapshot = inline.fetch_stable_snapshot(pr, cwd)
    return context(snapshot, top.fetch(snapshot, cwd))


def unchanged(expected, live, receipts):
    live = copy.deepcopy(live)
    # Only confirmed replies from this invocation may differ from approved evidence.
    for receipt in receipts:
        candidates = live['items'] if receipt['kind'] == 'issue_comment' else next(
            (t['comments'] for t in live['threads'] if t['id'] == receipt['reference']), [])
        found = verify_receipt(candidates, receipt, 'controller_published')
        candidates.remove(found)
    if expected != live:
        raise EvidenceChanged('PR head or source conversation changed; rebuild and revalidate',
                              evidence_changes(expected, live))


def post(reply, pr, cwd):
    parsed = urlparse(pr)
    parts = parsed.path.strip('/').split('/')
    require(parsed.scheme == 'https' and parsed.hostname and len(parts) == 4 and
            parts[2] == 'pull' and parts[3].isdigit(), 'invalid PR URL')
    dest = reply['destination']
    if dest['kind'] == 'inline':
        query = ('mutation($thread:ID!,$body:String!){addPullRequestReviewThreadReply('
                 'input:{pullRequestReviewThreadId:$thread,body:$body}){comment{url body}}}')
        payload = {'query': query, 'variables': {'thread': dest['reference'], 'body': reply['body']}}
        args = ['graphql']
    else:
        payload = {'body': reply['body']}
        args = ['--method', 'POST', f'repos/{parts[0]}/{parts[1]}/issues/{parts[3]}/comments']
    result = subprocess.run(['gh', 'api', '--hostname', parsed.hostname, *args, '--input', '-'],
                            cwd=cwd, input=json.dumps(payload), text=True, capture_output=True)
    require(result.returncode == 0, result.stderr.strip() or 'reply mutation failed')
    response = json.loads(result.stdout)
    require(not response.get('errors'), 'reply mutation returned GraphQL errors')
    comment = response['data']['addPullRequestReviewThreadReply']['comment'] if dest['kind'] == 'inline' else response
    url = comment.get('url') if dest['kind'] == 'inline' else comment.get('html_url')
    require(comment.get('body') == reply['body'] and isinstance(url, str) and url,
            'reply mutation response is uncertain')
    return dict(dest, id=reply['id'], url=url, body=reply['body'])


def publish(batch, verdict, journal_path, cwd):
    expected = validate(batch)
    if not batch['replies']:
        return {'status': 'empty', 'replies': []}
    require_approval(batch, verdict)
    # An existing journal is deliberately not replayable, including uncertain outcomes.
    with Path(journal_path).open('x') as stream:
        json.dump({'status': 'started', 'batchSha256': digest(batch), 'replies': []}, stream)
    journal = {'status': 'started', 'batchSha256': digest(batch), 'replies': []}
    for reply in batch['replies']:
        unchanged(expected, fetch_live(expected['pr'], cwd), journal['replies'])
        journal.update(status='in_flight', pending=reply['id'])
        write(journal_path, journal)
        receipt = post(reply, expected['pr'], cwd)
        journal['replies'].append(receipt)
        journal.update(status='posted', pending=None)
        write(journal_path, journal)
    unchanged(expected, fetch_live(expected['pr'], cwd), journal['replies'])
    journal['status'] = 'complete'
    write(journal_path, journal)
    return journal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prepare = sub.add_parser('prepare')
    for name in ('draft', 'snapshot', 'conversation', 'out'):
        prepare.add_argument('--' + name, required=True, type=Path)
    check = sub.add_parser('check')
    check.add_argument('--batch', required=True, type=Path)
    pub = sub.add_parser('publish')
    for name in ('batch', 'verdict', 'journal'):
        pub.add_argument('--' + name, required=True, type=Path)
    pub.add_argument('--cwd', default='.', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            batch = dict(read(args.draft), schemaVersion=1, snapshot=read(args.snapshot),
                         conversation=read(args.conversation))
            validate(batch)
            write(args.out, batch)
            result = {'batchSha256': digest(batch), 'replyCount': len(batch['replies'])}
        elif args.command == 'check':
            batch = read(args.batch)
            validate(batch)
            result = {'batchSha256': digest(batch), 'replyCount': len(batch['replies'])}
        else:
            result = publish(read(args.batch), read(args.verdict), args.journal, args.cwd.resolve())
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, inline.FetchError, top.FetchError) as exc:
        parser.exit(1, f'reply batch: {exc}\n')


if __name__ == '__main__':
    main()
