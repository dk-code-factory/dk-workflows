import copy
import importlib.util
import io
import json
from pathlib import Path
from contextlib import redirect_stdout
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('reply_passes', Path(__file__).parents[1] / 'scripts/reply_passes.py')
passes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(passes)
guard = passes.guard
PR = 'https://github.com/owner/repo/pull/7'
REVIEW = 'pullrequestreview-42'
SHA = 'a' * 40


class GitHub:
    """Mock API responses; exercise the real conversation fetcher and reply mutations."""
    def __init__(self):
        self.head = SHA
        self.threads = [{'id': 'T1', 'isResolved': False, 'path': 'a.py', 'line': 1,
                         'comments': [{'reference': 'C1', 'reviewReference': REVIEW,
                                       'text': 'Add a test.', 'url': PR + '#discussion_r1',
                                       'createdAt': '2026-01-01', 'updatedAt': '2026-01-01'}]}]
        self.reviews = [{'id': 42, 'body': 'Add a test. Document the timeout. Thanks.',
                         'html_url': PR + '#' + REVIEW}]
        self.comments = []
        self.posts = []
        self.inline_reads = 0
        self.top_reads = 0
        self.uncertain = False
        self.resolves = []

    def snapshot(self, pr, cwd):
        self.inline_reads += 1
        _, review = guard.inline.split_pr_reference(pr)
        return guard.inline.build_output(dict(reviewReference=review, owner='owner', repo='repo', number=7, url=PR,
                                             title='Fix', description='', headRefOid=self.head),
                                         copy.deepcopy(self.threads))

    def run(self, args, **kwargs):
        if args[1:3] == ['pr', 'view']:
            response = {'headRefOid': self.head}
        elif '--method' in args and args[args.index('--method') + 1] == 'GET':
            self.top_reads += 1
            response = [self.reviews if any(a.endswith('/reviews') for a in args) else self.comments]
        else:
            payload = (json.loads(kwargs['input']) if 'input' in kwargs else
                       {'query': next(a[6:] for a in args if a.startswith('query=')),
                        'variables': {'thread': next(a[7:] for a in args if a.startswith('thread='))}})
            inline = 'graphql' in args
            if inline and 'resolveReviewThread' in payload.get('query', ''):
                thread = next(t for t in self.threads if t['id'] == payload['variables']['thread'])
                thread['isResolved'] = True
                self.resolves.append(thread['id'])
                response = {'data': {'resolveReviewThread': {'thread': {'id': thread['id'], 'isResolved': True}}}}
                if self.uncertain:
                    self.uncertain = False
                    return subprocess.CompletedProcess(args, 1, '', 'lost after resolution')
                return subprocess.CompletedProcess(args, 0, json.dumps(response), '')
            body = payload['variables']['body'] if inline else payload['body']
            number = len(self.posts) + 100
            url = PR + ('#discussion_r' if inline else '#issuecomment-') + str(number)
            self.posts.append({'kind': 'inline' if inline else 'issue_comment', 'body': body, 'url': url})
            if inline:
                thread = next(t for t in self.threads if t['id'] == payload['variables']['thread'])
                thread['comments'].append({'reference': f'C{number}', 'reviewReference': None,
                                           'text': body, 'url': url,
                                           'createdAt': '2026-01-02', 'updatedAt': '2026-01-02'})
                response = {'data': {'addPullRequestReviewThreadReply': {'comment': {'url': url, 'body': body}}}}
            else:
                self.comments.append({'id': number, 'body': body, 'html_url': url,
                                      'created_at': '2026-01-02', 'updated_at': '2026-01-02'})
                response = {'html_url': url, 'body': body}
            if self.uncertain:
                self.uncertain = False
                return subprocess.CompletedProcess(args, 1, '', 'connection lost after publication')
        return subprocess.CompletedProcess(args, 0, json.dumps(response), '')


def request(key, source=REVIEW, excerpt='Add a test.', thread=None, disposition='reply', reason=None):
    result = dict(id=key, source=source, excerpt=excerpt, threadReference=thread, disposition=disposition)
    if reason:
        result['reason'] = reason
    return result


def response(key, ids, reference='T1', kind='inline', excerpt='Document the timeout.'):
    body = 'fixed.' if kind == 'inline' else f'[In reply to]({PR}#{reference})\n\n> {excerpt}\n\nfixed.'
    return dict(id=key, requestIds=ids, destination=dict(kind=kind, reference=reference), body=body)


class ReplyPassesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.record = self.folder / 'run.json'
        guard.write(self.record, {'pr': 7, 'events': [], 'cycles': [],
                                  'local_decisions': [{'text': 'Keep API stable', 'source': 'user'}]})
        self.github = GitHub()
        patcher = patch.object(guard.inline, 'fetch_stable_snapshot', side_effect=self.github.snapshot)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(guard.subprocess, 'run', side_effect=self.github.run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def controller(self, agent='reply-agent'):
        return passes.Controller(guard.read(self.record), self.record, PR, self.folder, agent)

    def test_head_change_diagnostic_survives_resume(self):
        self.next()
        self.github.head = 'b' * 40
        result = self.next()
        self.assertEqual(result['action'], 'reconcile')
        self.assertEqual(result['diagnostic']['changes']['head'],
                         {'expected': SHA, 'actual': 'b' * 40})
        self.assertEqual(self.next()['diagnostic'], result['diagnostic'])
        self.assertEqual(self.github.posts, [])

    def test_edited_reply_diagnostic_retains_provenance_and_blocks_publication(self):
        self.publish(self.inline_batch(self.next()))
        self.github.threads[0]['comments'][-1]['text'] = 'Edited published reply'
        result = self.next()
        self.assertEqual(result['action'], 'reconcile')
        receipt = result['diagnostic']['changes']['receipt']
        self.assertEqual(receipt['category'], 'controller_published')
        self.assertEqual(receipt['status'], 'edited')
        self.assertNotEqual(receipt['expected_sha256'], receipt['actual_sha256'])
        self.assertEqual(len(self.github.posts), 1)
        saved = guard.read(self.record)['reply_workflow']['blocked']
        self.assertEqual(saved['diagnostic'], result['diagnostic'])

    def next(self):
        return self.controller().next()

    def batch(self, step, requests, replies):
        batch = dict(schemaVersion=1, snapshot=guard.read(step['snapshot']),
                     conversation=guard.read(step['conversation']), requests=requests, replies=replies)
        guard.validate(batch)
        return batch

    def publish(self, batch, status='approved', validator='validator', number=1):
        path = self.folder / 'draft-batch.json'
        guard.write(path, batch)
        verdict = None
        if batch['replies'] or number == 2:
            verdict = self.folder / 'verdict.json'
            guard.write(verdict, dict(batchSha256=guard.digest(batch), validatorAgent=validator,
                                      status=status, corrections=[] if status == 'approved' else ['Review answer'],
                                      **{'pass': number}))
        return self.controller().publish(path, verdict)

    def inline_batch(self, step, body_only=True):
        requests = [request('inline', source='C1', thread='T1'), request('repeat', thread='T1')]
        if body_only:
            requests.append(request('body', excerpt='Document the timeout.', disposition='held',
                                    reason='Unmatched; top-level pass pending'))
        return self.batch(step, requests, [response('inline-reply', ['inline', 'repeat'])])

    def top_batch(self, step):
        url = self.github.posts[0]['url']
        return self.batch(step, [request('inline', source='C1', thread='T1', disposition='skip',
                                        reason='Answered at ' + url),
                                 request('repeat', thread='T1', disposition='skip', reason='Answered at ' + url),
                                 request('body', excerpt='Document the timeout.')],
                          [response('body-reply', ['body'], REVIEW, 'issue_comment')])

    def test_two_batches_refresh_evidence_and_reply_only_to_remaining_body_request(self):
        first = self.next()
        self.assertEqual(first['action'], 'inline')
        self.assertEqual([t['reference'] for t in first['unreplied_threads']], ['T1'])
        batch = self.inline_batch(first)
        result = self.publish(batch)
        self.assertEqual(result['phase'], 'top_level')
        inline_reads, top_reads = self.github.inline_reads, self.github.top_reads
        second = self.next()
        self.assertEqual(second['action'], 'top_level')
        self.assertGreater(self.github.inline_reads, inline_reads)
        self.assertGreater(self.github.top_reads, top_reads)
        self.assertEqual(second['unreplied_threads'], [])
        self.assertEqual([r['id'] for r in second['outstanding_requests']], ['body'])
        top = self.top_batch(second)
        self.assertNotEqual(guard.digest(batch), guard.digest(top))
        self.assertEqual(self.publish(top)['phase'], 'done')
        self.assertEqual([p['kind'] for p in self.github.posts], ['inline', 'issue_comment'])
        self.assertNotIn('Add a test.', self.github.posts[1]['body'])
        history = guard.read(self.record)['reply_workflow']['history']
        self.assertEqual(len(history), 2)
        for entry in history:
            attempt = entry['attempts'][0]
            self.assertTrue(Path(attempt['batch']).is_file())
            self.assertTrue(Path(attempt['verdictPath']).is_file())
            self.assertEqual(guard.read(attempt['journal'])['status'], 'complete')
            self.assertEqual(entry['receipts'][0]['url'], self.github.posts[history.index(entry)]['url'])
        self.assertEqual(guard.read(self.record)['local_decisions'][0]['text'], 'Keep API stable')

    def test_repeated_inline_feedback_needs_no_top_level_publication(self):
        self.github.reviews[0]['body'] = 'Add a test.'
        self.publish(self.inline_batch(self.next(), body_only=False))
        self.github.threads[0]['isResolved'] = True
        step = self.next()
        self.assertEqual(step['action'], 'top_level')
        requests = step['request_mappings']
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(r['disposition'] == 'skip' for r in requests))
        self.publish(self.batch(step, requests, []))
        self.assertEqual(len(self.github.posts), 1)
        self.assertEqual(self.next()['action'], 'done')

    def test_verified_answers_survive_completion_restart_and_new_head(self):
        self.publish(self.inline_batch(self.next()))
        self.publish(self.top_batch(self.next()))
        # Recreate the controller each time, as completion/restart would.
        for _ in range(2):
            done = self.next()
            self.assertEqual(done['action'], 'done')
            self.assertEqual(done['unreplied_comments'], [])
        self.github.head = 'b' * 40
        self.assertEqual(self.next()['action'], 'reconcile')
        resumed = self.controller('resumed-agent')
        self.assertEqual(resumed.reconcile()['action'], 'next')
        step = self.controller('resumed-agent').next()
        self.assertEqual(step['unreplied_comments'], [])
        self.assertEqual(step['unreplied_threads'], [])
        self.assertEqual(len(self.github.posts), 2)

    def test_new_feedback_returns_to_inline_before_top_level_publication(self):
        self.publish(self.inline_batch(self.next()))
        self.github.threads[0]['comments'].append(dict(reference='C2', reviewReference=None,
                                                     text='Explain the test.', url=PR + '#discussion_r2'))
        step = self.next()
        self.assertEqual(step['action'], 'inline')
        self.assertIn('C2', step['unclassified_sources'])
        self.assertEqual([t['reference'] for t in step['unreplied_threads']], ['T1'])

    def test_agent_can_route_new_top_level_request_back_to_inline(self):
        self.publish(self.inline_batch(self.next()))
        step = self.next()
        batch = self.batch(step, [request('new', excerpt='Document the timeout.', thread='T1')],
                           [response('new-reply', ['new'])])
        self.assertEqual(self.publish(batch)['action'], 'inline')
        self.assertEqual(self.next()['action'], 'inline')
        self.assertEqual(len(self.github.posts), 1)

    def test_top_level_batch_cannot_use_pre_inline_evidence_or_approval(self):
        first = self.next()
        inline = self.inline_batch(first)
        self.publish(inline)
        stale = self.batch(first, [request('body', excerpt='Document the timeout.')],
                           [response('body-reply', ['body'], REVIEW, 'issue_comment')])
        with self.assertRaises(guard.GuardError):
            self.publish(stale)
        top = self.top_batch(self.next())
        path, verdict = self.folder / 'top.json', self.folder / 'wrong-verdict.json'
        guard.write(path, top)
        guard.write(verdict, dict(batchSha256=guard.digest(inline), status='approved', corrections=[],
                                  validatorAgent='validator', **{'pass': 1}))
        with self.assertRaisesRegex(guard.GuardError, 'verdict'):
            self.controller().publish(path, verdict)
        self.assertEqual(len(self.github.posts), 1)

    def test_one_correction_per_batch_and_final_rejection_stops_later_passes(self):
        inline = self.inline_batch(self.next())
        self.assertEqual(self.publish(inline, status='rejected')['action'], 'correct_batch')
        self.assertEqual(self.next()['action'], 'correct_batch')
        with self.assertRaisesRegex(guard.GuardError, 'same validator'):
            self.publish(inline, validator='another-validator', number=2)
        self.assertEqual(self.publish(inline, number=2)['phase'], 'top_level')
        top = self.top_batch(self.next())
        self.assertEqual(self.publish(top, status='rejected')['action'], 'correct_batch')
        self.assertEqual(self.publish(top, status='rejected', number=2)['action'], 'stop')
        self.assertEqual(self.next()['action'], 'stop')
        with self.assertRaisesRegex(guard.GuardError, 'final rejection'):
            self.controller().reconcile()
        self.assertEqual(len(self.github.posts), 1)
        self.assertEqual([len(h['attempts']) for h in guard.read(self.record)['reply_workflow']['history']], [2, 2])

    def test_empty_passes_skip_publication_and_preserve_pending_decisions(self):
        step = self.next()
        held = [request('pending', thread='T1', disposition='held', reason='User decision pending')]
        with patch.object(guard, 'publish') as publish:
            self.assertEqual(self.publish(self.batch(step, held, []))['phase'], 'top_level')
            second = self.next()
            self.assertEqual(self.publish(self.batch(second, held, []))['phase'], 'done')
            publish.assert_not_called()
        done = self.next()
        self.assertEqual(done['outstanding_requests'][0]['reason'], 'User decision pending')
        self.assertEqual(self.github.posts, [])

    def test_uncertain_reply_is_reconciled_before_another_pass(self):
        inline = self.inline_batch(self.next())
        self.github.uncertain = True
        self.assertEqual(self.publish(inline)['action'], 'reconcile')
        self.assertEqual(self.next()['action'], 'reconcile')
        # Feedback arriving during interruption must still be classified after recovery.
        self.github.comments.append({'id': 50, 'body': 'Check documentation.',
                                     'html_url': PR + '#issuecomment-50'})
        self.assertEqual(self.controller().reconcile()['action'], 'next')
        step = self.next()
        self.assertEqual(step['action'], 'inline')
        self.assertIn('issuecomment-50', step['unclassified_sources'])
        repeat = next(r for r in step['request_mappings'] if r['id'] == 'repeat')
        self.assertEqual(repeat['disposition'], 'skip')
        self.assertIn(self.github.posts[0]['url'], repeat['reason'])
        self.assertEqual(len(self.github.posts), 1)

    def test_missing_published_reply_and_incomplete_feedback_block_progress(self):
        self.publish(self.inline_batch(self.next()))
        self.github.threads[0]['comments'].pop()
        self.assertEqual(self.next()['action'], 'reconcile')
        self.assertEqual(len(self.github.posts), 1)
        with patch.object(guard.inline, 'fetch_stable_snapshot', side_effect=guard.inline.FetchError('partial')):
            with self.assertRaises(guard.inline.FetchError):
                self.controller().reconcile()

    def test_phase_and_agent_identity_are_enforced(self):
        first = self.next()
        top = self.batch(first, [request('body', excerpt='Document the timeout.')],
                         [response('body-reply', ['body'], REVIEW, 'issue_comment')])
        with self.assertRaisesRegex(guard.GuardError, 'phase'):
            self.publish(top)
        with self.assertRaisesRegex(guard.GuardError, 'reuse the reply agent'):
            self.controller('another-reply-agent').next()
        self.assertEqual(self.github.posts, [])

    def test_existing_equivalent_answer_in_resolved_thread_is_retained_and_not_replied(self):
        self.github.threads[0]['isResolved'] = True
        self.github.threads[0]['comments'].append(dict(reference='C2', reviewReference=None,
                                                     text='The regression case now covers the timeout.',
                                                     url=PR + '#discussion_r22'))
        self.github.reviews[0]['body'] = 'Please include regression coverage.'
        step = self.next()
        # Semantic equivalence is supplied by the agent, with live matching evidence.
        matched = request('body', excerpt='Please include regression coverage.', thread='T1', disposition='skip',
                          reason='Equivalent answer at ' + PR + '#discussion_r22')
        matched.update(matchedCommentReference='C1', matchedExcerpt='Add a test.')
        self.publish(self.batch(step, [matched], []))
        second = self.next()
        self.assertEqual(second['unreplied_threads'], [])
        self.assertEqual(second['unreplied_comments'], [])
        self.publish(self.batch(second, [matched], []))
        saved = guard.read(self.record)['reply_workflow']['answers']
        self.assertEqual(len(saved), 1)
        self.assertEqual(next(iter(saved.values()))['receipt']['url'], PR + '#discussion_r22')
        self.github.head = 'b' * 40
        self.next()
        self.controller().reconcile()
        self.assertEqual(self.next()['unreplied_comments'], [])
        self.assertEqual(self.github.posts, [])

    def test_controller_rejects_repeating_verified_request_after_head_change(self):
        self.publish(self.inline_batch(self.next()))
        self.github.head = 'b' * 40
        self.next()
        self.controller().reconcile()
        step = self.next()
        repeat = self.inline_batch(step)
        with self.assertRaisesRegex(guard.GuardError, 'already has a verified answer'):
            self.publish(repeat)
        self.assertEqual(len(self.github.posts), 1)

    def test_resume_keeps_first_rejection_and_requires_same_validator_pass_two(self):
        inline = self.inline_batch(self.next())
        self.publish(inline, status='rejected')
        self.assertEqual(self.controller('resumed-agent').reconcile()['action'], 'correct_batch')
        step = self.controller('resumed-agent').next()
        self.assertEqual(step['action'], 'correct_batch')
        path, verdict = self.folder / 'corrected.json', self.folder / 'corrected-verdict.json'
        guard.write(path, inline)
        guard.write(verdict, dict(batchSha256=guard.digest(inline), validatorAgent='validator',
                                  status='approved', corrections=[], **{'pass': 1}))
        with self.assertRaisesRegex(guard.GuardError, 'same validator'):
            self.controller('resumed-agent').publish(path, verdict)
        self.assertEqual(self.github.posts, [])

    def test_controller_rejects_approval_from_reply_agent(self):
        inline = self.inline_batch(self.next())
        with self.assertRaisesRegex(guard.GuardError, 'non-independent'):
            self.publish(inline, validator='reply-agent')
        self.assertEqual(self.github.posts, [])

    def test_cli_next_and_empty_publication_preserve_existing_run_record_fields(self):
        args = ['reply_passes.py', '--record', str(self.record), '--pr', PR, '--cwd', str(self.folder),
                '--agent', 'reply-agent']
        stream = io.StringIO()
        with patch.object(passes.sys, 'argv', args + ['next']), redirect_stdout(stream):
            self.assertEqual(passes.main(), 0)
        step = json.loads(stream.getvalue())
        empty = self.batch(step, [], [])
        path = self.folder / 'empty.json'
        guard.write(path, empty)
        stream = io.StringIO()
        with patch.object(passes.sys, 'argv', args + ['publish', '--batch', str(path)]), redirect_stdout(stream):
            self.assertEqual(passes.main(), 0)
        self.assertEqual(json.loads(stream.getvalue())['phase'], 'top_level')
        self.assertEqual(guard.read(self.record)['cycles'], [])
        self.assertEqual(guard.read(self.record)['local_decisions'][0]['text'], 'Keep API stable')
        self.assertEqual(self.github.posts, [])

    def test_interruption_cannot_hide_final_rejection_or_in_flight_publication(self):
        inline = self.inline_batch(self.next())
        self.publish(inline, status='rejected')
        self.publish(inline, status='rejected', number=2)
        record = guard.read(self.record)
        record['reply_workflow']['blocked'] = None
        record['reply_workflow']['history'][-1]['status'] = 'prepared'
        guard.write(self.record, record)
        self.assertEqual(self.next()['action'], 'stop')
        with self.assertRaisesRegex(guard.GuardError, 'final rejection'):
            self.controller().reconcile()

    def test_interrupted_publication_requires_reconciliation_even_without_saved_blocker(self):
        inline = self.inline_batch(self.next())
        self.github.uncertain = True
        self.publish(inline)
        record = guard.read(self.record)
        record['reply_workflow']['blocked'] = None
        guard.write(self.record, record)
        self.assertEqual(self.next()['action'], 'reconcile')
        self.assertEqual(len(self.github.posts), 1)

    def test_head_race_before_mutation_leaves_reply_unobserved_on_reconciliation(self):
        batch = self.inline_batch(self.next())
        self.github.head = 'b' * 40
        self.assertEqual(self.publish(batch)['action'], 'reconcile')
        self.assertEqual(self.github.posts, [])
        self.controller().reconcile()
        step = self.next()
        self.assertEqual(step['action'], 'inline')
        self.assertEqual([r['id'] for r in step['outstanding_requests']], ['inline', 'repeat', 'body'])

    def test_uncertain_publication_with_multiple_candidates_blocks_recovery(self):
        batch = self.inline_batch(self.next())
        self.github.uncertain = True
        self.publish(batch)
        self.github.threads[0]['comments'].append(dict(reference='C999', reviewReference=None,
                                                     text='fixed.', url=PR + '#discussion_r999'))
        with self.assertRaisesRegex(guard.GuardError, 'multiple candidates'):
            self.controller().reconcile()
        self.assertEqual(self.next()['action'], 'reconcile')


if __name__ == '__main__':
    unittest.main()
