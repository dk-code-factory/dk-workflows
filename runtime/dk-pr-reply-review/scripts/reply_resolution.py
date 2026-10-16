"""Guard thread resolution separately from reply publication and retain recovery receipts."""
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
from urllib.parse import urlparse


def check(controller, assessment, evidence):
    guard = controller.guard
    require = guard.require
    require(assessment.get('schemaVersion') == 1, 'unsupported resolution assessment')
    threads = {t['id']: t for t in controller.selected_threads(evidence)}
    thread = threads.get(assessment.get('threadReference'))
    require(thread is not None, 'resolution outside selected threads; top-level sources cannot be resolved')
    require(assessment.get('head') == evidence['head'] == controller.work['head'], 'resolution head changed')
    require(assessment.get('scopeFingerprint') == controller.scope_fingerprint(evidence),
            'resolution scope changed')
    require(assessment.get('conversationFingerprint') == controller.conversation_fingerprint(evidence, thread),
            'resolution conversation changed')
    require(assessment.get('fullyAddressed') is True and
            all(assessment.get(k) is False for k in
                ('pendingDecision', 'unpublishedFix', 'unresolvedObjections')),
            'resolution has pending decisions, unpublished fixes, or unresolved objections')
    held = [r for r in controller.work['requests'] if r.get('threadReference') == thread['id'] and
            r['disposition'] in ('held', 'reply')]
    require(not held, 'thread still has held or unanswered requests; reassess reply mappings first')
    require(isinstance(assessment.get('evidence'), list) and assessment['evidence'] and
            all(isinstance(e, str) and e.strip() for e in assessment['evidence']),
            'published supporting evidence required')
    reply = assessment.get('reply', {})
    require(isinstance(reply.get('url'), str) and reply['url'].startswith(controller.pr + '#discussion_r'),
            'verified inline reply URL required')
    comments = thread['comments']
    matches = [c for c in comments[1:] if c.get('url') == reply.get('url') and
               c.get('text') == reply.get('text') and c.get('text', '').strip()]
    require(len(matches) == 1, 'verified reply is missing or edited')
    reply_index = comments.index(matches[0])
    other_comments = [c for c in comments if c is not matches[0]]
    requests = assessment.get('requests', [])
    require(isinstance(requests, list) and len(requests) == len(comments) - 1 and
            {r.get('source') for r in requests} == {c['reference'] for c in other_comments} and
            all(isinstance(r.get('reason'), str) and r['reason'].strip() for r in requests),
            'explain why every request and other response is addressed')
    assessed = {r['source']: r for r in requests}
    require(all(assessed[c['reference']].get('disposition') == 'no_objection'
                for c in comments[reply_index + 1:]),
            'later responses need explicit no-objection assessments; leave unresolved objections open')
    return thread, {'kind': 'inline', 'reference': thread['id'],
                    'url': reply['url'], 'body': reply['text']}


def resolve(controller, path):
    guard = controller.guard
    require = guard.require
    authorization = controller.record.get('resolution_authorization', {})
    require(authorization.get('pr') == controller.pr and
            authorization.get('selection') == controller.selection and
            isinstance(authorization.get('source'), str) and authorization['source'].strip(),
            'existing resolution authorization for this PR and selection is required')
    require(controller.work['agent'] == controller.agent, 'reuse the reply agent, or reconcile on resume')
    require(not controller.work['blocked'] and controller.work['active'] is None and
            not any(r['status'] == 'in_flight' for r in controller.work['resolutions']),
            'reconcile unfinished work before resolution')
    assessment = guard.read(path)
    require(controller.work['observations'] and assessment.get('snapshot') ==
            controller.work['observations'][-1]['snapshot'], 'use the latest controller observation')
    digest = guard.digest(assessment)
    require(not any(r['assessmentSha256'] == digest for r in controller.work['resolutions']),
            'assessment already attempted; reconcile and reassess from a fresh observation')
    _, _, evidence = controller.live()
    thread, reply = check(controller, assessment, evidence)
    if thread['isResolved']:
        return {'action': 'next', 'status': 'already_resolved', 'reference': thread['id']}
    require(thread.get('viewerCanResolve') is not False, 'viewer lacks thread resolution permission')
    entry = {'assessment': assessment, 'assessmentSha256': digest, 'status': 'in_flight',
             'reference': thread['id'], 'reply': reply}
    controller.work['resolutions'].append(entry)
    controller.save()  # Intent must survive interruption, including after GitHub accepted the mutation.
    try:
        result = guard.inline.graphql(
            'mutation($thread:ID!){resolveReviewThread(input:{threadId:$thread}){thread{id isResolved}}}',
            {'thread': thread['id']}, controller.cwd, urlparse(controller.pr).hostname)
        resolved = result.get('data', {}).get('resolveReviewThread', {}).get('thread', {})
        require(resolved.get('id') == thread['id'] and resolved.get('isResolved') is True,
                'uncertain resolution response; reconcile live state')
        _, _, live = controller.live()
        current, _ = check(controller, assessment, live)
        require(current['isResolved'] is True, 'resolution not observed; reconcile live state')
        entry.update(status='complete', receipt={'reference': thread['id'], 'isResolved': True,
                                               'head': live['head'], 'replyUrl': reply['url']})
        controller.save()
        return {'action': 'next', 'status': 'resolved', 'resolution': entry['receipt']}
    except (OSError, ValueError, KeyError, TypeError, guard.inline.FetchError, guard.top.FetchError) as exc:
        return controller.stop('resolution', exc)


def reconcile(controller, evidence):
    guard = controller.guard
    threads = {t['id']: t for t in evidence['threads']}
    for entry in controller.work['resolutions']:
        if entry['status'] != 'in_flight':
            continue
        thread = threads.get(entry['reference'])
        guard.require(thread is not None, 'uncertain resolution thread disappeared')
        # Reconciliation records observed state only. It never retries the mutation.
        controller.find_receipt(evidence, entry['reply'])
        entry.update(status='reconciled', receipt={
            'reference': thread['id'], 'isResolved': thread['isResolved'],
            'head': evidence['head'], 'replyUrl': entry['reply']['url']})
