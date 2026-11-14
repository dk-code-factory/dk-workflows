#!/usr/bin/env python3
"""Build transient, evidence-bound follow-up plans; never publish or run agents."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import subprocess
from urllib.parse import quote, urlsplit

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import pr_evidence as evidence
import fetch_pr_conversation as conversation
from shared import publication
from shared.review_request_recovery import Recovery
from shared.workflow_storage import operations_root


def local_history(pr, metadata, listed, anchors, cwd):
    """Prove capped membership and anchor distances from pinned local objects."""
    parsed = urlsplit(pr)
    owner, repo, _, _ = parsed.path.strip('/').split('/')
    base = metadata.get('base', {})
    if (base.get('repo', {}).get('full_name', '').lower() != f'{owner}/{repo}'.lower()
            or not isinstance(base.get('ref'), str) or not base['ref']):
        raise ValueError('restore PR target repository/ref evidence for local commit-age capture')
    route = f'repos/{owner}/{repo}/git/ref/heads/{quote(base["ref"], safe="")}'

    def target():
        value = conversation.gh_json(['api', '--hostname', parsed.hostname, route], cwd)
        obj = value.get('object', {}) if isinstance(value, dict) else {}
        if obj.get('type') != 'commit' or not re.fullmatch(r'[0-9a-f]{40}', obj.get('sha', '')):
            raise ValueError('restore the live PR target commit evidence before calculating age')
        return obj['sha']

    head, tip = metadata['head']['sha'], target()

    def git(*args, allowed=(0,)):
        value = subprocess.run(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
                                '-c', 'core.hooksPath=/dev/null', *args], cwd=cwd,
                               env={**os.environ, 'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_GRAFT_FILE': os.devnull},
                               text=True, capture_output=True, timeout=60)
        if value.returncode not in allowed:
            raise ValueError(f'restore full local Git objects for PR head {head} and live target {tip}; '
                             'then recapture complete PR evidence and retry commit-age inspection')
        return value

    if git('rev-parse', '--is-shallow-repository').stdout.strip() != 'false':
        raise ValueError('restore full non-shallow local history for the captured PR head and live target')
    members = git('rev-list', '--reverse', '--topo-order', head, '^' + tip).stdout.splitlines()
    if (len(members) != metadata['commits'] or len(set(members)) != len(members)
            or not set(listed).issubset(members) or not members or members[-1] != head):
        raise ValueError('local PR membership disagrees with GitHub count/list; refresh source pins and objects')
    ages = {}
    for anchor in sorted(set(anchors) & set(members)):
        if git('merge-base', '--is-ancestor', anchor, head, allowed=(0, 1)).returncode == 0:
            ages[anchor] = int(git('rev-list', '--count', head, '^' + anchor, '^' + tip).stdout)
    if target() != tip:
        raise ValueError('live PR target changed during local commit-age capture')
    return {'mode': 'local', 'commits': members, 'target': tip, 'targetRef': base['ref'], 'newCommits': ages}


def commits(pr, head, cwd, anchors=()):
    """Read stable PR membership; supplement capped API lists with pinned Git."""
    parsed = urlsplit(pr)
    owner, repo, _, number = parsed.path.strip('/').split('/')
    route = f'repos/{owner}/{repo}/pulls/{number}'
    prior = None
    for _ in range(3):
        metadata = conversation.gh_json(['api', '--hostname', parsed.hostname, route], cwd)
        count = metadata.get('commits') if isinstance(metadata, dict) else None
        if (type(count) is not int or count <= 0
                or metadata.get('head', {}).get('sha') != head):
            raise ValueError('PR commit count/head unavailable or changed; recapture complete PR evidence')
        pages = conversation.gh_json(['api', '--hostname', parsed.hostname, '--method', 'GET',
                                     '--paginate', '--slurp', route + '/commits',
                                     '-F', 'per_page=100'], cwd)
        if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
            raise ValueError('incomplete PR commit pagination')
        values = [item.get('sha') if isinstance(item, dict) else None
                  for page in pages for item in page]
        if (len(values) != min(count, 250) or any(not isinstance(sha, str) or
                not re.fullmatch(r'[0-9a-f]{40}', sha) for sha in values)
                or len(set(values)) != len(values) or count <= 250 and values[-1] != head):
            raise ValueError('PR commit membership incomplete or inconsistent; age is uncertain')
        captured = local_history(pr, metadata, values, anchors, cwd) if count > 250 else values
        live = conversation.gh_json(['pr', 'view', pr, '--json', 'headRefOid'], cwd)
        if not isinstance(live, dict) or live.get('headRefOid') != head:
            raise ValueError('PR head changed during commit capture')
        if captured == prior:
            return captured
        prior = captured
    raise ValueError('PR commit membership changed during capture')


def sources(inline, top):
    """Keep identities and candidate evidence; agents decide what links mean."""
    result = {}
    reviews = {item['reference']: item for item in top['reviews']}
    pr = evidence.identity(inline)['pr']

    def add(item, kind, thread=None):
        review = reviews.get(item.get('reviewReference'), {}) if thread else item
        # A code permalink can cite historical code. It is only a candidate,
        # never an inferred report/request head.
        linked = []
        for url in re.findall(r'https://[^\s<>\[\]()]+', item['text']):
            parsed = urlsplit(url.rstrip('.,;'))
            if parsed.hostname != urlsplit(pr).hostname:
                continue
            match = re.fullmatch(r'/(?:[^/]+/[^/]+/commit|[^/]+/[^/]+/pull/[0-9]+/commits)/([0-9a-f]{40})',
                                 parsed.path)
            if match:
                linked.append(match[1])
        record = {'reference': item['reference'], 'kind': kind, 'id': item.get('databaseId', item.get('id')),
                  'url': item.get('url'), 'createdAt': item.get('createdAt'),
                  'reviewCommit': review.get('commitId'), 'linkedCommits': sorted(set(linked))}
        if thread:
            record.update(thread=thread['id'], resolved=thread['isResolved'],
                          parent=thread['comments'][0].get('databaseId'))
        result[item['reference']] = record

    for thread in inline['threads']:
        for item in thread['comments']:
            add(item, 'inline_comment', thread)
    for item in top['items']:
        add(item, item['kind'])
    return result


def operations(pr, cwd):
    """Discover original intents, including writes absent from discussion reads."""
    # Match publisher preparation's scope without changing original intents.
    pr = pr.rstrip('/').lower()
    directory = operations_root(repository=cwd) / publication.digest(pr)
    result = {}
    for path in sorted(directory.glob('*/*.json')):
        value = publication.load(path)
        if value['request']['target'].rstrip('/').lower() != pr:
            raise ValueError('publication target disagrees with retained scope')
        result[str(path.resolve())] = value
    return result


def inspect(inline, top, history, retained):
    subject = evidence.identity(inline)
    return {**subject, 'commits': history['commits'] if isinstance(history, dict) else history,
            **({'commitEvidence': history} if isinstance(history, dict) else {}),
            'sources': sources(inline, top),
            'discussion': evidence.digest([inline, top]),
            'operations': [{'path': path, 'status': value['status'],
                            'kind': value['request']['kind'],
                            'head': value['request']['approved'].get('head'),
                            'source': value['request']['approved'].get('source'),
                            'sha256': evidence.digest(value)}
                           for path, value in retained.items()],
            'publicationAuthority': False}


def inspection_output(context, *, full_commits=False):
    """Keep complete proof internal; expose only candidate ages by default."""
    result = {**context, 'context': evidence.digest(context)}
    if full_commits:
        return result
    members = result.pop('commits')
    proof = result.pop('commitEvidence', {})
    anchors = {sha for source in context['sources'].values()
               for sha in [source['reviewCommit'], *source['linkedCommits']] if sha}
    anchors.update(item['head'] for item in context['operations'] if item['head'])
    ages = proof.get('newCommits')
    if ages is None:
        positions = {sha: index for index, sha in enumerate(members)}
        ages = {sha: len(members) - 1 - positions[sha] for sha in anchors if sha in positions}
    result['commitEvidence'] = {
        'mode': proof.get('mode', 'github'), 'count': len(members),
        **{key: proof[key] for key in ('target', 'targetRef') if key in proof},
        'newCommits': ages}
    return result


def plan(context, assessments, retained):
    """Consume native semantics; construct counts, routing and exact payloads."""
    if assessments.get('head') != context['head']:
        raise ValueError('assessment head changed; independently verify again')
    if assessments.get('context') != evidence.digest(context):
        raise ValueError('follow-up inputs changed; reassess discussion, commits and intents')
    findings = assessments.get('findings')
    if not isinstance(findings, list):
        raise ValueError('native finding assessments required')
    result = []
    seen = set()
    for finding in findings:
        key = finding['key']
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError('unique native finding key required')
        seen.add(key)
        source = context['sources'][finding['source']]
        disposition = finding['disposition']
        if disposition not in {'confirmed', 'fixed', 'rejected', 'uncertain', 'accepted', 'deferred'}:
            raise ValueError('unsupported finding disposition')
        if not isinstance(finding.get('evidence'), str) or not finding['evidence'].strip():
            raise ValueError('current independent verification/disposition evidence required')
        outcome = {'key': key, 'source': finding['source'], 'disposition': disposition}
        if source.get('resolved') or disposition != 'confirmed':
            result.append({**outcome, 'status': 'excluded'})
            continue
        requests = finding.get('requests')
        if (not isinstance(requests, list) or not requests or
                finding['source'] not in [item.get('reference') for item in requests]):
            raise ValueError('include the original report and every equivalent substantive request')
        latest = finding.get('latestRequest')
        if latest not in [item.get('reference') for item in requests] + finding.get('operations', []):
            raise ValueError('identify the latest substantive request for this finding')
        anchors = []
        uncertain = False
        for request in requests:
            original = context['sources'][request['reference']]
            anchor = request.get('commit', original.get('reviewCommit'))
            # Explicit links need a native finding-specific explanation that
            # they bind this request, rather than merely citing earlier code.
            if anchor != original.get('reviewCommit'):
                if anchor not in original['linkedCommits'] or not request.get('anchorEvidence'):
                    anchor = None
            if anchor not in context['commits']:
                # A later evidenced request can supersede an unanchored old
                # report. Native semantics must establish which request is
                # latest; an unanchored latest request always blocks age.
                uncertain = uncertain or request['reference'] == latest
            else:
                age = (context['commitEvidence']['newCommits'].get(anchor)
                       if 'commitEvidence' in context else len(context['commits']) - 1 - context['commits'].index(anchor))
                if age is None:
                    uncertain = uncertain or request['reference'] == latest
                else:
                    anchors.append(age)
        for path in finding.get('operations', []):
            value = retained[path]
            approved = value['request']['approved']
            if value['status'] == 'not_applied':
                if latest == path:
                    raise ValueError('a not-applied operation is not a substantive request')
                continue
            if value['status'] != 'verified':
                uncertain = True
            elif approved.get('head') in context['commits']:
                age = (context['commitEvidence']['newCommits'].get(approved['head'])
                       if 'commitEvidence' in context else len(context['commits']) - 1 - context['commits'].index(approved['head']))
                if age is None:
                    uncertain = uncertain or path == latest
                else:
                    anchors.append(age)
            else:
                uncertain = uncertain or path == latest
        reply_kind = 'reply-inline' if source['kind'] == 'inline_comment' else 'reply-comment'
        pending = [path for path, value in retained.items()
                   if value['status'] == 'pending' and
                   (path in finding.get('operations', []) or
                    Recovery(None).blocks(value, {'kind': reply_kind}))]
        if pending:
            result.append({**outcome, 'status': 'recover_required', 'operations': pending})
            continue
        if uncertain:
            result.append({**outcome, 'status': 'uncertain_age'})
            continue
        age = min(anchors)
        if age < 2:
            result.append({**outcome, 'status': 'recent_request', 'newCommits': age})
            continue
        correction = finding.get('correction')
        if not isinstance(correction, str) or not correction.strip():
            raise ValueError('a targeted correction for the author at the current head is required')
        url = source['url']
        if (not isinstance(url, str) or '#' not in url or
                url.partition('#')[0].rstrip('/').lower() != context['pr'].rstrip('/').lower()):
            raise ValueError('original source URL must belong to this PR')
        body = (f'Still actionable at [{context["head"][:12]}]'
                f'({context["pr"]}/commits/{context["head"]}): {correction.strip()}\n\n'
                f'Earlier finding: {url}')
        source_record = {name: source[name] for name in ('kind', 'id', 'url')}
        if type(source_record['id']) is not int or source_record['id'] <= 0:
            raise ValueError('original source database ID required')
        request = {'pr': context['pr'], 'head': context['head'], 'source': source_record, 'body': body}
        if source['kind'] == 'inline_comment':
            if type(source['parent']) is not int or source['parent'] <= 0:
                raise ValueError('original inline parent database ID required')
            request.update(kind='reply-inline', parent=source['parent'])
            source_record['thread_id'] = source['thread']
        else:
            request.update(kind='reply-comment', body=f'In Reply To: [Earlier finding]({url})\n\n' + body)
        result.append({**outcome, 'status': 'eligible', 'newCommits': age, 'request': request})
    return {'head': context['head'], 'findings': result, 'publicationAuthority': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['inspect', 'plan'])
    parser.add_argument('--cwd', default='.')
    for name in ('handoff', 'sha256', 'pr', 'head'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--assessment', type=Path)
    parser.add_argument('--full-commits', action='store_true',
                        help='Include complete commit membership for targeted evidence inspection')
    args = parser.parse_args()
    cwd = Path(args.cwd).resolve()
    try:
        _, inline, top = evidence.load_handoff(args.handoff, cwd, sha256=args.sha256,
                                             pr=args.pr, head=args.head)
        # Old receipts never establish current discussion or head freshness.
        evidence.verify(args.handoff, cwd, sha256=args.sha256, pr=args.pr, head=args.head)
        retained = operations(args.pr, cwd)
        anchors = [sha for item in sources(inline, top).values()
                   for sha in [item['reviewCommit'], *item['linkedCommits']] if sha]
        anchors += [value['request']['approved'].get('head') for value in retained.values()]
        history = commits(args.pr, args.head, cwd, anchors=[sha for sha in anchors if sha])
        context = inspect(inline, top, history, retained)
        if args.command == 'plan':
            if not args.assessment:
                raise ValueError('--assessment required for plan')
            result = plan(context, json.loads(args.assessment.read_bytes()), retained)
        else:
            result = inspection_output(context, full_commits=args.full_commits)
        print(evidence.encoded(result).decode())
    except (ValueError, OSError, KeyError, TypeError, AttributeError,
            subprocess.SubprocessError, conversation.FetchError, evidence.threads.FetchError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
