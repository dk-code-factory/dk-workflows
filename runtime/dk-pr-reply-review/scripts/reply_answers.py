"""Retain answer provenance and append-only evidence revisions."""
import copy
from pathlib import Path
import re


def key(guard, request):
    return guard.digest([request['source'], request['excerpt']])


def semantic_receipt(guard, request, evidence):
    urls = {url.rstrip('.,;:') for url in re.findall(
        r'https?://[^\s<>()\[\]]+', request.get('reason', ''))}
    matches = [s for s in guard.sources(evidence)[0].values() if s.get('url') in urls and
               s['reference'] != request['source']]
    if len(matches) != 1:
        return None
    source = matches[0]
    return {'kind': 'inline' if source['kind'] == 'inline' else 'issue_comment',
            'reference': source.get('threadReference', source['reference']),
            'url': source['url'], 'body': source['text']}


def strict_receipts(guard, workflow):
    result = []
    for entry in workflow['history']:
        result.extend((r, 'controller_published') for r in entry.get('receipts', []))
        for attempt in entry.get('attempts', []):
            path = attempt.get('journal')
            if path and Path(path).exists():
                journal = guard.read(path)
                guard.require(journal.get('batchSha256') == attempt['batchSha256'], 'journal mismatch')
                result.extend((r, 'publication_journal') for r in journal.get('replies', []))
    result.extend((entry['reply'], 'resolution_reply') for entry in workflow.get('resolutions', []))
    return result


def revision(workflow, identity, event, answer, observation, **details):
    workflow.setdefault('answerRevisions', []).append(dict(
        answerKey=identity, event=event, answer=copy.deepcopy(answer),
        observation=copy.deepcopy(observation), **details))


def migrate(guard, workflow, observation):
    """Migrate atomically; authorship is never evidence of publication provenance."""
    if workflow['schemaVersion'] == 2:
        return
    strict = strict_receipts(guard, workflow)
    batches = []
    for entry in workflow['history']:
        for attempt in entry.get('attempts', []):
            path = attempt.get('batch')
            if not path or not Path(path).exists():
                continue
            batch = guard.read(path)
            guard.require(guard.digest(batch) == attempt['batchSha256'], 'saved batch changed')
            evidence = guard.validate(batch)
            successful = entry['status'] in ('complete', 'reconciled') and (
                attempt.get('verdict') is None or attempt['verdict'].get('status') == 'approved')
            batches.append((batch, evidence, successful))
    migrated = copy.deepcopy(workflow.get('answers', {}))
    for identity, answer in migrated.items():
        receipt = answer['receipt']
        diagnostic = f"ambiguous or inconsistent legacy answer provenance: {receipt['url']} [legacy_answer]"
        candidates = [r for r in workflow['requests'] if key(guard, r) == identity]
        candidates += [r for batch, _, _ in reversed(batches) for r in batch['requests']
                       if key(guard, r) == identity]
        guard.require(candidates, diagnostic + '; request identity unavailable')
        # IDs/routing may change between assessments; source and exact excerpt define identity.
        request = candidates[0]
        published = [r for r, _ in strict if r['url'] == receipt['url']]
        if published:
            guard.require(all(r['kind'] == receipt['kind'] and r['body'] == receipt['body'] and
                              (r['kind'] == 'issue_comment' or r['reference'] == receipt['reference'])
                              for r in published), diagnostic)
            provenance = 'controller_published'
        else:
            proof = [(r, evidence) for batch, evidence, successful in batches if successful
                     for r in batch['requests'] if key(guard, r) == identity and
                     r['disposition'] == 'skip' and semantic_receipt(guard, r, evidence) == receipt and
                     guard.digest(guard.sources(evidence)[0][r['source']]) == answer['sourceFingerprint']]
            guard.require(proof, diagnostic + '; no retained successful semantic assessment')
            provenance = 'semantic_evidence'
        answer.update(request=copy.deepcopy(request), provenance=provenance, status='current')
    workflow['answers'] = migrated
    workflow['schemaVersion'] = 2
    for identity, answer in migrated.items():
        revision(workflow, identity, 'migrated', answer, observation)


def inspect(guard, workflow, evidence, observation):
    """Invalidate semantic dependencies without replacing their original receipt bodies."""
    sources = guard.sources(evidence)[0]
    published = {r['url'] for r, _ in strict_receipts(guard, workflow)}
    for identity, answer in workflow.get('answers', {}).items():
        if answer['receipt']['url'] in published and answer['provenance'] != 'controller_published':
            revision(workflow, identity, 'publication_provenance', answer, observation)
            answer['provenance'] = 'controller_published'
        if answer['provenance'] != 'semantic_evidence':
            continue
        matches = [s for s in sources.values() if s.get('url') == answer['receipt']['url']]
        original_body = len(matches) == 1 and matches[0]['text'] == answer['receipt']['body']
        if original_body and answer['status'] == 'current':
            continue
        # Reverting a comment cannot independently approve a stale mapping.
        reason = ('body_restored' if original_body else 'missing' if not matches else
                  'body_changed' if len(matches) == 1 else 'ambiguous_url')
        change = dict(reason=reason, observed=copy.deepcopy(matches))
        change_digest = guard.digest([reason, [(s['reference'], s['text']) for s in matches]])
        seen = answer.setdefault('invalidationFingerprints', [])
        if change_digest not in seen:
            revision(workflow, identity, 'invalidated', answer, observation, **change)
            seen.append(change_digest)
            answer['changeObservation'] = copy.deepcopy(observation)
        answer.update(status='stale', changeReason=reason, changeFingerprint=change_digest)


def stale(workflow, sources):
    result = []
    for identity, answer in workflow.get('answers', {}).items():
        if answer.get('status') != 'stale':
            continue
        request = answer['request']
        source = sources.get(request['source']) if sources is not None else None
        result.append(dict(answerKey=identity, request=copy.deepcopy(request),
                           url=answer['receipt']['url'], category=answer['provenance'],
                           reason=answer['changeReason'],
                           requestPresent=(source is not None and request['excerpt'] in source['text'])
                           if sources is not None else None))
    return result
