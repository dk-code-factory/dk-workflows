#!/usr/bin/env python3
"""Drive inline-first PR replies from live feedback and the iteration run record."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlparse

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
from shared import private_state
from shared.diagnostics import command as recovery_command, human

spec = importlib.util.spec_from_file_location('reply_batch', Path(__file__).with_name('reply_batch.py'))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
spec = importlib.util.spec_from_file_location('reply_resolution', Path(__file__).with_name('reply_resolution.py'))
resolution = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resolution)
spec = importlib.util.spec_from_file_location('reply_answers', Path(__file__).with_name('reply_answers.py'))
answer_state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(answer_state)


def require(ok, message):
    guard.require(ok, message)


def fingerprint(source):
    return guard.digest(source)


def all_sources(evidence):
    return guard.sources(evidence)[0]


def find_receipt(evidence, receipt, category='controller_published'):
    candidates = evidence['items'] if receipt['kind'] == 'issue_comment' else next(
        (t['comments'] for t in evidence['threads'] if t['id'] == receipt['reference']), [])
    guard.verify_receipt(candidates, receipt, category)


def verify_history(workflow, evidence):
    strict = answer_state.strict_receipts(guard, workflow)
    for receipt, category in strict:
        find_receipt(evidence, receipt, category)
    strict_urls = {r['url'] for r, _ in strict}
    for answer in workflow.get('answers', {}).values():
        if answer.get('provenance') == 'controller_published' or answer['receipt']['url'] in strict_urls:
            find_receipt(evidence, answer['receipt'])


def fetch(pr, cwd):
    snapshot = guard.inline.fetch_stable_snapshot(pr, cwd)
    conversation = guard.top.fetch(snapshot, cwd)
    return snapshot, conversation, guard.context(snapshot, conversation)


class Controller:
    guard = guard
    find_receipt = staticmethod(find_receipt)

    def resolve(self, assessment):
        return resolution.resolve(self, assessment)

    def __init__(self, record, path, pr, cwd, agent):
        self.record, self.path = record, Path(path)
        canonical, selection = guard.inline.split_pr_reference(pr)
        self.pr, self.cwd, self.agent = canonical, Path(cwd), agent
        self.selection = selection
        parts = urlparse(pr).path.strip('/').split('/')
        require(len(parts) == 4 and parts[2] == 'pull' and parts[3].isdigit(), 'PR URL required')
        require(record.get('pr') == int(parts[3]), 'run record belongs to a different PR')
        self.work = record.setdefault('reply_workflow', {
            'schemaVersion': 2, 'pr': canonical, 'selection': selection, 'agent': agent, 'head': None,
            'phase': 'inline', 'history': [], 'requests': [], 'knownSources': {},
            'observations': [], 'active': None, 'blocked': None, 'answers': {},
        })
        saved_pr, saved_selection = guard.inline.split_pr_reference(self.work['pr'])
        require(self.work.get('selection', saved_selection) == selection,
                'review selection changed; resume with the original PR/review URL')
        self.work.update(pr=saved_pr, selection=selection)
        self.work.setdefault('resolutions', [])
        require(self.work['schemaVersion'] in (1, 2) and self.work['pr'] == canonical,
                'invalid or mismatched reply workflow')
        # A crash between saving a verdict and updating status must not discard it.
        active = self.work['active']
        if active is not None:
            entry = self.work['history'][active]
            verdict = entry['attempts'][-1]['verdict'] if entry['attempts'] else None
            if verdict is not None and verdict.get('status') == 'rejected':
                entry['status'] = 'rejected'
                if verdict.get('pass') == 2:
                    self.work['blocked'] = {'kind': 'rejected', 'reason': 'final validator rejection'}
        self.directory = self.path.parent / (self.path.stem + '-replies')
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self):
        private_state.write(self.path, self.record)

    def stop(self, kind, reason, diagnostic=None):
        self.work['blocked'] = {'kind': kind, 'reason': str(reason)}
        diagnostic = diagnostic or getattr(reason, 'diagnostic', None)
        if diagnostic:
            self.work['blocked']['diagnostic'] = diagnostic
        self.save()
        return {'action': 'stop' if kind == 'rejected' else 'reconcile',
                'reason': str(reason), 'phase': self.work['phase'],
                **({'diagnostic': diagnostic} if diagnostic else {}),
                'stale_answers': self.stale_answers()}

    def stale_answers(self, evidence=None):
        evidence = evidence if evidence is not None else getattr(self, 'evidence', None)
        sources = self.selected_sources(evidence) if evidence is not None else None
        return answer_state.stale(self.work, sources)

    def live(self):
        target = self.pr + ('#' + self.selection if self.selection else '')
        snapshot, conversation, evidence = fetch(target, self.cwd)
        if self.selection:
            expected = guard.inline.selection_for(self.selection, evidence['threads'])
            require(snapshot.get('selection') == expected, 'snapshot lost selected review')
            if self.selection.startswith('pullrequestreview-'):
                require(any(s['reference'] == self.selection for s in evidence['items']),
                        'selected review is missing from complete conversation')
        self.evidence = evidence
        observations = self.work.setdefault('evidenceObservations', [])
        number = len(observations) + 1
        folder = Path(tempfile.mkdtemp(prefix=f'observation-{number}-', dir=self.directory))
        guard.write(folder / 'inline.json', snapshot)
        guard.write(folder / 'conversation.json', conversation)
        observation = {'snapshot': str(folder / 'inline.json'),
                       'conversation': str(folder / 'conversation.json'),
                       'head': evidence['head'], 'phase': self.work['phase']}
        observations.append(observation)
        self.observation = observation
        try:
            answer_state.migrate(guard, self.work, observation)
            verify_history(self.work, evidence)
            answer_state.inspect(guard, self.work, evidence, observation)
        finally:
            self.save()
        return snapshot, conversation, evidence

    def selected_threads(self, evidence):
        if self.selection is None:
            return evidence['threads']
        ids = guard.inline.selection_for(self.selection, evidence['threads'])['threadReferences']
        return [t for t in evidence['threads'] if t['id'] in ids]

    def selected_sources(self, evidence):
        ids = {t['id'] for t in self.selected_threads(evidence)}
        return {ref: s for ref, s in all_sources(evidence).items() if self.selection is None or
                ref == self.selection or s.get('threadReference') in ids}

    def scope_fingerprint(self, evidence):
        return fingerprint({'pr': self.pr, 'scope': evidence['scope'], 'selection': self.selection,
                            'threads': sorted(t['id'] for t in self.selected_threads(evidence))})

    def conversation_fingerprint(self, evidence, thread):
        return fingerprint({'thread': {k: v for k, v in thread.items() if k not in ('isResolved', 'viewerCanResolve')},
                            'items': evidence['items']})

    def pending(self, evidence):
        sources = self.selected_sources(evidence)
        known = self.work['knownSources']
        outstanding = []
        stale = self.stale_answers(evidence)
        stale_keys = {s['answerKey'] for s in stale if s['requestPresent']}
        requests = {answer_state.key(guard, r): r for r in self.work['requests']}
        requests.update({s['answerKey']: s['request'] for s in stale if s['requestPresent'] and
                         s['answerKey'] not in requests})
        for identity, request in requests.items():
            source = sources.get(request['source'])
            if source is None or request['excerpt'] not in source['text']:
                continue  # Edited requests must be classified again from the complete snapshot.
            if (identity in stale_keys or request['disposition'] in ('reply', 'held') or
                    known.get(request['source']) != fingerprint(source)):
                outstanding.append(copy.deepcopy(request))
        unknown = [source for ref, source in sources.items() if known.get(ref) != fingerprint(source)]
        refs = {r['source'] for r in outstanding} | {s['reference'] for s in unknown}
        outstanding_threads = {r.get('threadReference') for r in outstanding}
        threads = [t for t in evidence['threads'] if not t['isResolved'] and (
            t['id'] in outstanding_threads or any(c['reference'] in refs for c in t['comments']))]
        comments = [s for s in evidence['items'] if s['reference'] in refs]
        unresolved = [dict(reference=t['id'], path=t.get('path'),
                           conversationFingerprint=self.conversation_fingerprint(evidence, t))
                      for t in self.selected_threads(evidence) if not t['isResolved']]
        return {'unresolved_threads': unresolved,
                'cleanup_complete': not unresolved and not outstanding and not unknown,
                'scopeFingerprint': self.scope_fingerprint(evidence),
                'unreplied_threads': [dict(reference=t['id'], path=t.get('path'),
                                          url=t['comments'][0].get('url') if t['comments'] else None)
                                     for t in threads],
                'unreplied_comments': [dict(reference=s['reference'], kind=s['kind'], url=s.get('url'))
                                      for s in comments],
                'unclassified_sources': [s['reference'] for s in unknown],
                'outstanding_requests': outstanding, 'stale_answers': stale}

    def next(self):
        require(self.work['agent'] == self.agent, 'reuse the reply agent, or reconcile on resume')
        if self.work['blocked'] and self.work['blocked']['kind'] != 'evidence':
            block = self.work['blocked']
            return {'action': 'stop' if block['kind'] == 'rejected' else 'reconcile', **block,
                    'stale_answers': self.stale_answers()}
        if any(r['status'] == 'in_flight' for r in self.work['resolutions']):
            return self.stop('resolution', 'unfinished resolution; reconcile live state')
        if self.work['active'] is not None and self.work['history'][self.work['active']]['status'] != 'rejected':
            return self.stop('publication', 'unfinished batch; reconcile its journal before replying')
        try:
            snapshot, conversation, evidence = self.live()
        except (guard.GuardError, guard.inline.FetchError, guard.top.FetchError) as exc:
            return self.stop('evidence', exc)
        self.work['blocked'] = None
        if self.work['head'] is not None and self.work['head'] != evidence['head']:
            return self.stop('head_changed', 'PR head changed; refresh preflight and decision context',
                {'changes': {'head': {'expected': self.work['head'], 'actual': evidence['head']}},
                 'next_action': 'refresh_preflight_decisions_and_preparation_then_reconcile'})
        self.work['head'] = evidence['head']
        changed = self.work['knownSources'] != {ref: fingerprint(source)
                                               for ref, source in self.selected_sources(evidence).items()}
        stale = any(s['requestPresent'] and self.work['answers'][s['answerKey']].get('heldChange') !=
                    self.work['answers'][s['answerKey']].get('changeFingerprint')
                    for s in self.stale_answers(evidence))
        if (changed or stale) and self.work['phase'] != 'inline' and self.work['active'] is None:
            self.work['phase'] = 'inline'
        observation = self.observation
        observation['phase'] = self.work['phase']
        self.work['observations'].append(copy.deepcopy(observation))
        self.save()
        active = self.work['active']
        action = 'correct_batch' if active is not None else self.work['phase']
        pending = self.pending(evidence)
        unanswered = {t['reference'] for t in pending['unreplied_threads']}
        answered = {a['receipt']['reference'] for a in self.work['answers'].values()
                    if a['status'] == 'current' and a['receipt']['kind'] == 'inline'}
        candidates = [t for t in pending['unresolved_threads']
                      if t['reference'] in answered - unanswered]
        if (active is None and resolution.authorized(self) and candidates
                and not pending['unclassified_sources']):
            action = 'resolve'
        else:
            candidates = []
        instructions = {
            'inline': 'Match all sources to threads. Draft only missing inline replies; hold unmatched '
                      'requests for the top-level pass and preserve pending user decisions.',
            'top_level': 'Reconcile retained mappings with live replies, including resolved threads. '
                         'Skip equivalent answers; draft only unanswered requests without a suitable '
                         'thread, grouped by source. Submit new inline mappings to return to inline first.',
            'done': 'No new feedback to classify. Report outstanding holds; do not repeat verified replies.',
            'resolve': 'Assess resolution_candidates against the entire conversation and published code. '
                       'Use guarded resolve with a fresh assessment for each fully answered thread, then next. '
                       'Do this now, independently of overall review/CI completion. Do not post another reply. '
                       'Keep disputed, partial or pending-decision work open with held mappings and reasons.',
            'correct_batch': 'Correct the rejected batch and use the same validator for pass 2. '
                             'Do not start another batch or reset the rejection limit.',
        }
        return {'action': action, 'instructions': instructions[action], 'agent': self.work['agent'],
                **observation, **pending, 'resolution_candidates': candidates, 'run_record': str(self.path),
                'request_mappings': self.work['requests']}

    def finish(self, entry, batch, evidence, receipts):
        requests = copy.deepcopy(batch['requests'])
        by_id = {r['id']: r for r in requests}
        receipt_map = {r['id']: r for r in receipts}
        expected = guard.validate(batch)
        reviewed = all_sources(expected)
        answers = self.work.setdefault('answers', {})
        observation = self.observation
        approved = entry['attempts'][-1]['verdict']
        approved = approved is not None and approved.get('status') == 'approved'
        try:
            guard.unchanged(expected, evidence, receipts)
            fresh = True
        except guard.GuardError:
            fresh = False
        strict = answer_state.strict_receipts(guard, self.work)
        strict_urls = {r['url'] for r, _ in strict} | {r['url'] for r in receipts}
        proposed = {}
        for reply in batch['replies']:
            for key in reply['requestIds']:
                request = by_id[key]
                if reply['id'] in receipt_map:
                    receipt = receipt_map[reply['id']]
                    request.update(disposition='skip', reason='Answered at ' + receipt['url'])
                    proposed[answer_state.key(guard, request)] = (request, receipt)
        for request in requests:
            if request['disposition'] == 'skip' and answer_state.key(guard, request) not in proposed:
                receipt = answer_state.semantic_receipt(guard, request, expected)
                if receipt is not None:
                    proposed[answer_state.key(guard, request)] = (request, receipt)
        # A recovered old batch may not dismiss a mapping invalidated by newer evidence.
        for identity, old in list(answers.items()):
            if old['status'] != 'stale':
                continue
            request = next((r for r in requests if answer_state.key(guard, r) == identity), None)
            source = all_sources(evidence).get(old['request']['source'])
            present = source is not None and old['request']['excerpt'] in source['text']
            if not present:
                proposed.pop(identity, None)
                continue  # Removed requests keep stale audit history without supplying an answer.
            if request is not None and request['disposition'] == 'held':
                proposed.pop(identity, None)
                if fresh:
                    old['heldChange'] = old['changeFingerprint']
                continue
            if not (approved and fresh):
                proposed.pop(identity, None)
                if present:
                    requests = [r for r in requests if answer_state.key(guard, r) != identity]
                    requests.append(copy.deepcopy(old['request']))
                continue
            if identity not in proposed:
                answer_state.revision(self.work, identity, 'dismissed', old, observation)
                del answers[identity]
        for identity, (request, receipt) in proposed.items():
            answer = dict(request=copy.deepcopy(request),
                          sourceFingerprint=fingerprint(reviewed[request['source']]), receipt=receipt,
                          provenance='controller_published' if receipt['url'] in strict_urls else 'semantic_evidence',
                          status='current')
            old = answers.get(identity)
            if old != answer:
                if old is not None:
                    answer_state.revision(self.work, identity, 'superseded', old, observation)
                answers[identity] = answer
                answer_state.revision(self.work, identity, 'assessed', answer, observation)
        self.work['requests'] = requests
        # In particular, never reactivate edited semantic evidence from an interrupted batch.
        answer_state.inspect(guard, self.work, evidence, observation)
        # Mark only reviewed sources and our verified replies as classified. Recovery may
        # encounter new/edited feedback that the interrupted batch never examined.
        self.work['knownSources'] = {ref: fingerprint(s) for ref, s in
                                    self.selected_sources(expected).items()}
        reply_urls = {r['url'] for r in receipts}
        for ref, source in all_sources(evidence).items():
            if source.get('url') in reply_urls:
                self.work['knownSources'][ref] = fingerprint(source)
        self.work['phase'] = 'top_level' if entry['phase'] == 'inline' else 'done'
        entry.update(status='complete', receipts=receipts)
        self.work['active'] = None
        self.work['blocked'] = None
        self.save()

    def publish(self, batch_path, verdict_path=None):
        require(self.work['agent'] == self.agent, 'reuse the reply agent, or reconcile on resume')
        require(not self.work['blocked'], 'reconcile the blocked workflow before publication')
        require(self.work['observations'], 'run next before preparing a batch')
        observation = self.work['observations'][-1]
        require(self.work['phase'] == observation['phase'] and self.work['phase'] != 'done',
                'run next for the current phase')
        batch = guard.read(batch_path)
        expected = guard.validate(batch)
        source_map = all_sources(expected)
        selection = batch['snapshot'].get('selection')
        reference = (selection.get('reviewReference') or selection.get('commentReference')) if selection else None
        require(reference == self.selection,
                'batch changed selected review')
        allowed = self.selected_sources(expected)
        require(all(r['source'] in allowed and (not r.get('threadReference') or
                    r['threadReference'] in {t['id'] for t in self.selected_threads(expected)})
                    for r in batch['requests']), 'request outside selected review')
        verdict = guard.read(verdict_path) if verdict_path else None
        stale_requests = {s['answerKey'] for s in self.stale_answers(expected) if s['requestPresent']}
        held = {answer_state.key(guard, r) for r in batch['requests'] if r['disposition'] == 'held'}
        needs_verdict = bool(batch['replies'] or stale_requests - held)
        if needs_verdict:
            require(verdict is not None, 'independent verdict required for replies or stale request reassessment')
        if verdict_path is not None:
            require(isinstance(verdict, dict) and
                    verdict.get('batchSha256') == guard.digest(batch) and
                    verdict.get('status') in ('approved', 'rejected') and
                    isinstance(verdict.get('validatorAgent'), str) and verdict['validatorAgent'].strip() and
                    verdict['validatorAgent'] != self.agent and
                    type(verdict.get('pass')) is int and verdict['pass'] in (1, 2) and
                    isinstance(verdict.get('corrections'), list) and
                    all(isinstance(c, str) and c.strip() for c in verdict['corrections']),
                    'invalid or non-independent verdict')
        # A rejection can use an earlier observation, never unrelated evidence.
        require(expected['pr'] == self.pr and expected['head'] == self.work['head'],
                'batch belongs to a different PR or pinned head')
        require(any(o['head'] == expected['head'] and o['phase'] == self.work['phase'] and
                    batch['snapshot'] == guard.read(o['snapshot']) and
                    batch['conversation'] == guard.read(o['conversation'])
                    for o in reversed(self.work['observations'])),
                'batch must use retained controller snapshots for the current phase')
        active = self.work['active']
        if active is None:
            require(verdict is None or verdict.get('pass') == 1, 'first verdict must be pass 1')
        else:
            entry = self.work['history'][active]
            previous = entry['attempts'][-1]['verdict']
            require(entry['status'] == 'rejected' and entry['head'] == expected['head'] and
                    entry['phase'] == self.work['phase'] and previous['pass'] == 1 and verdict is not None and
                    verdict.get('pass') == 2 and verdict.get('validatorAgent') == previous['validatorAgent'],
                    'one correction using the same validator is permitted')
        rejected = verdict is not None and verdict['status'] == 'rejected'
        if not rejected:
            guard.validate(batch, require_details=True)
            if verdict is not None:
                guard.require_approval(batch, verdict)
            for request in batch['requests']:
                answer = self.work.get('answers', {}).get(guard.digest([request['source'], request['excerpt']]))
                if (request['disposition'] == 'reply' and answer and answer['status'] == 'current' and
                        answer['sourceFingerprint'] == fingerprint(source_map[request['source']])):
                    find_receipt(expected, answer['receipt'])
                    raise guard.GuardError('request already has a verified answer; retain its mapping and skip it')
            require(batch['snapshot'] == guard.read(observation['snapshot']) and
                    batch['conversation'] == guard.read(observation['conversation']),
                    'batch must use the latest controller snapshots')
            kind = 'inline' if self.work['phase'] == 'inline' else 'issue_comment'
            if kind == 'issue_comment' and any(r['disposition'] == 'reply' and r.get('threadReference')
                                              for r in batch['requests']):
                require(self.work['active'] is None, 'correct the active rejection before changing passes')
                self.work['phase'] = 'inline'
                self.save()
                return {'action': 'inline', 'reason': 'new request needs an inline answer; run next again',
                        'stale_answers': self.stale_answers(expected)}
            require(all(r['destination']['kind'] == kind for r in batch['replies']),
                    'reply destinations do not match the controller phase')
            require(not any(r['disposition'] == 'reply' and not r.get('threadReference')
                            for r in batch['requests']) if kind == 'inline' else True,
                    'hold unmatched requests for the top-level pass')
        if active is None:
            entry = {'phase': self.work['phase'], 'head': expected['head'], 'status': 'prepared',
                     'attempts': [], 'receipts': []}
            self.work['history'].append(entry)
            self.work['active'] = len(self.work['history']) - 1
        folder = Path(tempfile.mkdtemp(
            prefix=f"batch-{len(self.work['history'])}-pass-{len(entry['attempts']) + 1}-", dir=self.directory))
        saved_batch, saved_verdict, journal = folder / 'batch.json', folder / 'verdict.json', folder / 'journal.json'
        guard.write(saved_batch, batch)
        if verdict is not None:
            guard.write(saved_verdict, verdict)
        entry['attempts'].append({'batch': str(saved_batch), 'batchSha256': guard.digest(batch),
                                  'verdictPath': str(saved_verdict) if verdict else None,
                                  'verdict': verdict, 'journal': str(journal) if batch['replies'] else None})
        self.save()
        if verdict is not None and verdict['status'] == 'rejected':
            entry['status'] = 'rejected'
            self.save()
            if verdict['pass'] == 2:
                return self.stop('rejected', 'final validator rejection; no further reply publication')
            return {'action': 'correct_batch', 'corrections': verdict.get('corrections', []),
                    'stale_answers': self.stale_answers(expected)}
        entry['status'] = 'in_flight'
        self.save()  # Persist intent before calling the existing guard.
        try:
            _, _, before = self.live()
            guard.unchanged(expected, before, [])
            if batch['replies']:
                result = guard.publish(batch, verdict, journal, self.cwd)
                receipts = result['replies']
            else:
                receipts = []
            _, _, live = self.live()
            guard.unchanged(expected, live, receipts)
            self.finish(entry, batch, live, receipts)
        except (OSError, ValueError, KeyError, guard.inline.FetchError, guard.top.FetchError) as exc:
            return self.stop('publication', exc)
        return {'action': 'next', 'phase': self.work['phase'], 'replies': receipts,
                'stale_answers': self.stale_answers(live),
                'instructions': 'Run next to refresh both complete snapshots before the next pass.'}

    def reconcile(self):
        require(not self.work['blocked'] or self.work['blocked']['kind'] != 'rejected',
                'final rejection cannot be reset by reconciliation or restart')
        snapshot, conversation, evidence = self.live()
        resolution.reconcile(self, evidence)
        active = self.work['active']
        if active is not None:
            entry = self.work['history'][active]
            if entry['status'] == 'rejected':
                require(evidence['head'] == self.work['head'],
                        'head changed with an outstanding rejection; do not reset the rejection limit')
                self.work.update(agent=self.agent, blocked=None)
                self.save()
                return {'action': 'correct_batch', 'instructions': 'Run next for fresh evidence, then '
                        'correct the active batch with the same validator for pass 2. Rejection history is retained.',
                        'stale_answers': self.stale_answers(evidence)}
            attempt = entry['attempts'][-1]
            batch = guard.read(attempt['batch'])
            require(guard.digest(batch) == attempt['batchSha256'], 'saved batch changed')
            journal = guard.read(attempt['journal']) if attempt['journal'] and Path(attempt['journal']).exists() else {}
            require(not journal or journal.get('batchSha256') == attempt['batchSha256'], 'journal mismatch')
            receipts = copy.deepcopy(journal.get('replies', []))
            for receipt in receipts:
                find_receipt(evidence, receipt)
            pending = journal.get('pending')
            if pending:
                reply = next(r for r in batch['replies'] if r['id'] == pending)
                destination = reply['destination']
                candidates = evidence['items'] if destination['kind'] == 'issue_comment' else next(
                    (t['comments'] for t in evidence['threads'] if t['id'] == destination['reference']), [])
                old_urls = {s.get('url') for s in all_sources(guard.validate(batch)).values()}
                matches = [s for s in candidates if s['text'] == reply['body'] and s.get('url') not in old_urls]
                require(len(matches) <= 1, 'uncertain reply has multiple candidates; reconcile manually')
                if matches:
                    receipts.append(dict(destination, id=reply['id'], body=reply['body'], url=matches[0]['url']))
            self.finish(entry, batch, evidence, receipts)
            entry['status'] = 'reconciled'
            entry['reconciliation'] = 'verified live replies; unobserved replies require fresh drafting'
        # Preserve history across heads; force full inline-first reassessment from live evidence.
        self.work.update(agent=self.agent, head=evidence['head'], phase='inline', blocked=None)
        self.save()
        return {'action': 'next', 'head': evidence['head'],
                'stale_answers': self.stale_answers(evidence),
                'instructions': 'Refresh preflight/decisions after a head change, then run next. '
                                'Retained replies must not be repeated.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', type=Path, required=True)
    parser.add_argument('--pr', required=True)
    parser.add_argument('--cwd', type=Path, default=Path('.'))
    parser.add_argument('--agent', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('next')
    sub.add_parser('reconcile')
    res = sub.add_parser('resolve')
    res.add_argument('--assessment', type=Path, required=True)
    pub = sub.add_parser('publish')
    pub.add_argument('--batch', type=Path, required=True)
    pub.add_argument('--verdict', type=Path)
    args = parser.parse_args()
    try:
        path = args.record.resolve()
        require(path.is_file(), 'initialize the iteration run record before starting replies')

        def run(record):
            controller = Controller(record, path, args.pr, args.cwd.resolve(), args.agent)
            if args.command == 'resolve':
                return controller.resolve(args.assessment)
            if args.command == 'publish':
                return controller.publish(args.batch, args.verdict)
            return getattr(controller, args.command)()

        result = private_state.mutate(path, {}, run)
        if result.get('action') in {'stop', 'reconcile'}:
            diagnostic = result.setdefault('diagnostic', {})
            diagnostic.update(code='reply_' + result['action'], message=result.get('reason', ''),
                action=(human('The independent validator rejected publication; report the retained verdict.', path)
                        if result['action'] == 'stop' else recovery_command([
                            sys.executable, str(Path(__file__).resolve()), '--record', str(path),
                            '--pr', args.pr, '--cwd', str(args.cwd.resolve()), '--agent', args.agent,
                            'reconcile'], path)))
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result['action'] in ('stop', 'reconcile') else 0
    except (OSError, ValueError, KeyError, TypeError, guard.inline.FetchError, guard.top.FetchError) as exc:
        parser.exit(1, f'reply passes: {exc}\n')


if __name__ == '__main__':
    sys.exit(main())
