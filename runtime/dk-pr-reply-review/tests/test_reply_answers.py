"""Evidence edits reopen semantic assessments while publication receipts stay strict."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

import test_reply_passes as helpers
from test_reply_passes import guard, passes, PR, REVIEW, request, response


class AnswerEvidenceTest(unittest.TestCase):
    setUp = helpers.ReplyPassesTest.setUp
    controller = helpers.ReplyPassesTest.controller
    next = helpers.ReplyPassesTest.next
    batch = helpers.ReplyPassesTest.batch
    publish = helpers.ReplyPassesTest.publish
    inline_batch = helpers.ReplyPassesTest.inline_batch
    top_batch = helpers.ReplyPassesTest.top_batch

    def workflow(self):
        return guard.read(self.record)['reply_workflow']

    def save_workflow(self, workflow):
        record = guard.read(self.record)
        record['reply_workflow'] = workflow
        guard.write(self.record, record)

    def approved(self, batch, status='approved', number=1, validator='validator'):
        path, verdict = self.folder / 'batch.json', self.folder / 'verdict.json'
        guard.write(path, batch)
        guard.write(verdict, dict(batchSha256=guard.digest(batch), validatorAgent=validator,
                                  status=status, corrections=[] if status == 'approved' else ['Reassess'],
                                  **{'pass': number}))
        return self.controller().publish(path, verdict)

    def semantic(self, multiple=False):
        self.github.comments.append(dict(id=50, body='Already covered in the regression test.',
                                         html_url=PR + '#issuecomment-50'))
        mapped = [request('test', source='C1', thread='T1', disposition='skip',
                          reason='Covered at ' + PR + '#issuecomment-50')]
        if multiple:
            mapped.append(request('docs', excerpt='Document the timeout.', disposition='skip',
                                  reason='Covered at ' + PR + '#issuecomment-50'))
        self.publish(self.batch(self.next(), mapped, []))
        self.publish(self.batch(self.next(), mapped, []))
        self.assertEqual(self.next()['action'], 'done')
        return mapped

    def legacy(self):
        workflow = self.workflow()
        workflow['schemaVersion'] = 1
        workflow.pop('answerRevisions', None)
        for answer in workflow['answers'].values():
            for field in ('request', 'provenance', 'status'):
                answer.pop(field, None)
        self.save_workflow(workflow)

    def edit(self, body='Updated summary adds another finding.'):
        self.github.comments[0]['body'] = body

    def test_legacy_summary_edit_with_published_receipts_and_new_inline_request(self):
        self.publish(self.inline_batch(self.next()))
        self.github.comments.append(dict(id=50, body='Documentation is covered.',
                                         html_url=PR + '#issuecomment-50'))
        mapped = self.next()['request_mappings']
        mapped[-1].update(disposition='skip', reason='Covered at ' + PR + '#issuecomment-50')
        self.publish(self.batch(self.next(), mapped, []))
        self.legacy()
        original = copy.deepcopy(self.workflow())
        self.edit()
        self.github.threads[0]['comments'].append(dict(reference='C2', reviewReference=None,
                                                     text='Explain the test.', url=PR + '#discussion_r2'))
        step = self.next()
        self.assertEqual(step['action'], 'inline')
        self.assertEqual(len(step['stale_answers']), 1)
        self.assertIn('C2', step['unclassified_sources'])
        self.assertEqual(step['stale_answers'][0]['request']['id'], 'body')
        workflow = self.workflow()
        self.assertEqual(workflow['schemaVersion'], 2)
        self.assertEqual(workflow['history'][:2], original['history'])
        self.assertTrue(all(a['provenance'] == 'controller_published' for a in workflow['answers'].values()
                            if a['receipt']['url'] == self.github.posts[0]['url']))
        body = next(a for a in workflow['answers'].values() if a['request']['id'] == 'body')
        self.assertEqual(body['receipt']['body'], 'Documentation is covered.')
        mapped = step['request_mappings'] + [request('new', source='C2', excerpt='Explain the test.', thread='T1')]
        self.approved(self.batch(step, mapped, [response('new-answer', ['new'])]))
        self.assertEqual(len(self.github.posts), 2)
        self.assertEqual(self.next()['stale_answers'], [])
        self.assertEqual(guard.read(self.record)['local_decisions'][0]['text'], 'Keep API stable')

    def test_shared_edited_evidence_invalidates_all_mappings_once(self):
        self.semantic(multiple=True)
        self.edit()
        for _ in range(3):
            step = self.next()
            self.assertEqual(step['action'], 'inline')
            self.assertEqual(len(step['stale_answers']), 2)
            self.assertEqual(len(step['outstanding_requests']), 2)
            self.assertFalse(step['cleanup_complete'])
            self.controller().reconcile()
        revisions = self.workflow()['answerRevisions']
        invalidations = [r for r in revisions if r['event'] == 'invalidated']
        self.assertEqual(len(invalidations), 2)
        for revision in invalidations:
            self.assertEqual(revision['answer']['receipt']['body'], 'Already covered in the regression test.')
            self.assertEqual(revision['observed'][0]['text'], 'Updated summary adds another finding.')
            self.assertTrue(Path(revision['observation']['conversation']).is_file())
        self.assertEqual(self.github.posts, [])

    def test_deleted_evidence_and_removed_request_keep_audit_without_cleanup_block(self):
        self.semantic()
        self.github.comments.clear()
        step = self.next()
        self.assertEqual(step['stale_answers'][0]['reason'], 'missing')
        self.github.threads[0]['comments'][0]['text'] = 'Thanks.'
        step = self.next()
        self.assertFalse(step['stale_answers'][0]['requestPresent'])
        self.assertEqual(step['outstanding_requests'], [])
        self.publish(self.batch(step, [], []))
        self.github.threads[0]['isResolved'] = True
        self.publish(self.batch(self.next(), [], []))
        self.assertTrue(self.next()['cleanup_complete'])
        self.assertEqual(next(iter(self.workflow()['answers'].values()))['status'], 'stale')

    def test_empty_restoration_and_dismissal_need_independent_approval(self):
        mapped = self.semantic()
        self.edit()
        step = self.next()
        for requests in (mapped, []):
            batch = self.batch(step, requests, [])
            with self.assertRaisesRegex(guard.GuardError, 'independent verdict'):
                self.publish(batch)
            with self.assertRaisesRegex(guard.GuardError, 'non-independent'):
                self.approved(batch, validator='reply-agent')
        self.approved(self.batch(step, mapped, []))
        self.assertEqual(self.next()['stale_answers'], [])
        self.assertEqual(next(iter(self.workflow()['answers'].values()))['receipt']['body'],
                         'Updated summary adds another finding.')
        self.edit('Another update.')
        step = self.next()
        self.approved(self.batch(step, [], []))
        self.assertEqual(self.workflow()['answers'], {})
        self.assertTrue(any(r['event'] == 'dismissed' for r in self.workflow()['answerRevisions']))
        self.assertEqual(self.github.posts, [])

    def test_stale_empty_rejection_preserves_same_validator_and_final_limit(self):
        mapped = self.semantic()
        self.edit()
        batch = self.batch(self.next(), mapped, [])
        self.assertEqual(self.approved(batch, status='rejected')['action'], 'correct_batch')
        self.assertEqual(self.controller().reconcile()['action'], 'correct_batch')
        step = self.next()
        batch = self.batch(step, mapped, [])
        with self.assertRaisesRegex(guard.GuardError, 'same validator'):
            self.approved(batch, number=2, validator='another')
        self.assertEqual(self.approved(batch, number=2, status='rejected')['action'], 'stop')
        self.assertEqual(self.next()['action'], 'stop')
        with self.assertRaisesRegex(guard.GuardError, 'final rejection'):
            self.controller().reconcile()
        self.assertEqual(len(self.workflow()['history'][-1]['attempts']), 2)

    def test_evidence_race_does_not_restore_an_approved_empty_mapping(self):
        mapped = self.semantic()
        self.edit()
        batch = self.batch(self.next(), mapped, [])
        self.edit('Changed again after validation.')
        self.assertEqual(self.approved(batch)['action'], 'reconcile')
        self.controller().reconcile()
        self.assertEqual(len(self.next()['stale_answers']), 1)
        self.assertEqual(next(iter(self.workflow()['answers'].values()))['receipt']['body'],
                         'Already covered in the regression test.')
        self.assertEqual(self.github.posts, [])

    def test_published_url_reused_semantically_stays_strict_after_migration(self):
        self.publish(self.inline_batch(self.next()))
        self.publish(self.top_batch(self.next()))
        self.legacy()
        self.github.threads[0]['comments'][-1]['text'] = 'Edited published answer.'
        for _ in range(2):
            step = self.next()
            self.assertEqual(step['action'], 'reconcile')
            self.assertIn(self.github.posts[0]['url'], step['reason'])
            self.assertIn('controller_published', step['reason'])
            with self.assertRaisesRegex(guard.GuardError, 'controller_published'):
                self.controller().reconcile()
        self.assertEqual(len(self.github.posts), 2)

    def test_evidence_only_block_retries_complete_snapshot_without_resetting_run(self):
        self.semantic()
        record = guard.read(self.record)
        record['cycles'] = [{'number': 2}]
        record['reply_workflow']['blocked'] = dict(kind='evidence', reason='legacy exact body check failed')
        guard.write(self.record, record)
        self.legacy()
        self.edit()
        with patch.object(guard.inline, 'fetch_stable_snapshot', side_effect=guard.inline.FetchError('partial')):
            self.assertEqual(self.next()['action'], 'reconcile')
        self.assertEqual(self.next()['action'], 'inline')
        self.assertIsNone(self.workflow()['blocked'])
        self.assertEqual(guard.read(self.record)['cycles'], [{'number': 2}])

    def test_ambiguous_legacy_provenance_stays_blocked_with_url(self):
        self.semantic()
        self.legacy()
        workflow = self.workflow()
        workflow['history'] = []
        self.save_workflow(workflow)
        for _ in range(2):
            step = self.next()
            self.assertEqual(step['action'], 'reconcile')
            self.assertIn('legacy_answer', step['reason'])
            self.assertIn(PR + '#issuecomment-50', step['reason'])
            self.assertEqual(self.workflow()['schemaVersion'], 1)

    def test_migration_recovers_missing_request_identity_from_retained_batches(self):
        self.semantic()
        self.legacy()
        workflow = self.workflow()
        workflow['requests'] = []
        self.save_workflow(workflow)
        self.edit()
        self.assertEqual(self.next()['outstanding_requests'][0]['source'], 'C1')

    def test_hold_stays_outstanding_without_approval_or_blocking_top_level_pass(self):
        mapped = self.semantic()
        self.edit()
        step = self.next()
        mapped[0].update(disposition='held', reason='User decision pending')
        self.publish(self.batch(step, mapped, []))
        step = self.next()
        self.assertEqual(step['action'], 'top_level')
        self.assertEqual(len(step['stale_answers']), 1)
        self.assertEqual(step['outstanding_requests'][0]['disposition'], 'held')
        self.publish(self.batch(step, mapped, []))
        self.assertEqual(self.next()['action'], 'done')
        self.assertFalse(self.next()['cleanup_complete'])

    def test_stale_evidence_outside_selected_review_reopens_in_scope_request(self):
        # A complete snapshot includes this evidence, but the selected worklist does not.
        self.github.reviews.append(dict(id=99, body='Already answered.', html_url=PR + '#pullrequestreview-99'))
        self.controller = lambda agent='reply-agent': passes.Controller(
            guard.read(self.record), self.record, PR + '#' + REVIEW, self.folder, agent)
        step = self.next()
        mapped = [request('test', source='C1', thread='T1', disposition='skip',
                          reason='Covered at ' + PR + '#pullrequestreview-99')]
        self.publish(self.batch(step, mapped, []))
        self.github.reviews[-1]['body'] = 'Changed external evidence.'
        step = self.next()
        self.assertEqual(step['action'], 'inline')
        self.assertEqual(step['outstanding_requests'][0]['source'], 'C1')
        self.assertNotIn('pullrequestreview-99', step['unclassified_sources'])

    def test_stale_request_blocks_resolution_even_if_mapping_was_lost(self):
        self.semantic()
        self.github.threads[0]['comments'].append(dict(reference='C2', text='Actual inline answer.',
                                                     url=PR + '#discussion_r2', reviewReference=None))
        self.edit()
        self.next()
        workflow = self.workflow()
        workflow['requests'] = []
        self.save_workflow(workflow)
        controller = self.controller()
        _, _, evidence = controller.live()
        assessment = dict(schemaVersion=1, threadReference='T1', head=evidence['head'],
                          scopeFingerprint=controller.scope_fingerprint(evidence), fullyAddressed=True,
                          conversationFingerprint=controller.conversation_fingerprint(evidence, evidence['threads'][0]),
                          pendingDecision=False, unpublishedFix=False, unresolvedObjections=False)
        with self.assertRaisesRegex(guard.GuardError, 'stale answer evidence'):
            passes.resolution.check(controller, assessment, evidence)

    def test_reverted_evidence_still_needs_approved_reassessment(self):
        mapped = self.semantic()
        self.edit()
        self.next()
        self.edit('Already covered in the regression test.')
        step = self.next()
        self.assertEqual(len(step['stale_answers']), 1)
        with self.assertRaisesRegex(guard.GuardError, 'independent verdict'):
            self.publish(self.batch(step, mapped, []))

    def test_head_change_during_stale_rejection_does_not_reset_limit(self):
        mapped = self.semantic()
        self.edit()
        self.approved(self.batch(self.next(), mapped, []), status='rejected')
        self.github.head = 'b' * 40
        self.assertEqual(self.next()['action'], 'reconcile')
        with self.assertRaisesRegex(guard.GuardError, 'head changed with an outstanding rejection'):
            self.controller().reconcile()
        self.assertEqual(len(self.workflow()['history'][-1]['attempts']), 1)

    def test_interrupted_publication_does_not_reactivate_changed_semantic_mapping(self):
        mapped = self.semantic()
        self.github.threads[0]['comments'].append(dict(reference='C2', reviewReference=None,
                                                     text='Explain the test.', url=PR + '#discussion_r2'))
        step = self.next()
        batch = self.batch(step, mapped + [request('new', source='C2', excerpt='Explain the test.', thread='T1')],
                           [response('new-reply', ['new'])])
        self.github.uncertain = True
        self.assertEqual(self.publish(batch)['action'], 'reconcile')
        self.edit()
        self.assertEqual(self.controller().reconcile()['action'], 'next')
        for _ in range(2):
            step = self.next()
            self.assertEqual(step['action'], 'inline')
            self.assertEqual(len(step['stale_answers']), 1)
            self.assertEqual([r['id'] for r in step['outstanding_requests']], ['test'])
            self.controller().reconcile()
        self.assertEqual(len(self.github.posts), 1)
        self.assertEqual(next(a for a in self.workflow()['answers'].values()
                              if a['request']['id'] == 'test')['receipt']['body'],
                         'Already covered in the regression test.')

    def test_interrupted_empty_batch_cannot_dismiss_evidence_changed_before_recovery(self):
        mapped = self.semantic()
        step = self.next()
        # Restart inline solely to submit another empty assessment.
        self.controller().reconcile()
        step = self.next()
        with patch.object(passes.Controller, 'finish', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.publish(self.batch(step, mapped, []))
        self.edit()
        self.controller().reconcile()
        self.assertEqual(len(self.next()['stale_answers']), 1)
        self.assertEqual(self.github.posts, [])

    def test_journal_receipts_remain_strict_before_finish(self):
        batch = self.inline_batch(self.next())
        with patch.object(passes.Controller, 'finish', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.publish(batch)
        self.github.threads[0]['comments'][-1]['text'] = 'Edited during interruption.'
        with self.assertRaisesRegex(guard.GuardError, 'publication_journal') as error:
            self.controller().reconcile()
        self.assertIn(self.github.posts[0]['url'], str(error.exception))
        self.assertEqual(len(self.github.posts), 1)

    def test_repaired_strict_receipt_clears_only_evidence_block(self):
        self.publish(self.inline_batch(self.next()))
        reply = self.github.threads[0]['comments'].pop()
        step = self.next()
        self.assertEqual(step['action'], 'reconcile')
        self.assertIn(self.github.posts[0]['url'], step['reason'])
        self.github.threads[0]['comments'].append(reply)
        self.assertEqual(self.next()['action'], 'top_level')
        self.assertIsNone(self.workflow()['blocked'])
        self.assertEqual(len(self.workflow()['history']), 1)

    def test_changed_request_text_is_classified_again_without_old_answer(self):
        self.semantic()
        self.edit()
        self.github.threads[0]['comments'][0]['text'] = 'Please explain the coverage.'
        step = self.next()
        self.assertEqual(step['action'], 'inline')
        self.assertFalse(step['stale_answers'][0]['requestPresent'])
        self.assertIn('C1', step['unclassified_sources'])
        batch = self.batch(step, [request('changed', source='C1', excerpt='Please explain the coverage.', thread='T1')],
                           [response('explanation', ['changed'])])
        self.publish(batch)
        self.assertEqual(len(self.github.posts), 1)
        self.assertTrue(any(a['status'] == 'stale' for a in self.workflow()['answers'].values()))

    def test_resolution_receipt_overrides_semantic_provenance(self):
        self.github.threads[0]['comments'].append(dict(reference='C2', reviewReference=None,
                                                     text='Existing answer.', url=PR + '#discussion_r2'))
        mapped = [request('test', source='C1', thread='T1', disposition='skip',
                          reason='Covered at ' + PR + '#discussion_r2')]
        self.publish(self.batch(self.next(), mapped, []))
        self.legacy()
        workflow = self.workflow()
        workflow['resolutions'].append(dict(status='complete', reply=dict(
            kind='inline', reference='T1', url=PR + '#discussion_r2', body='Existing answer.')))
        self.save_workflow(workflow)
        self.github.threads[0]['comments'][-1]['text'] = 'Changed answer.'
        step = self.next()
        self.assertEqual(step['action'], 'reconcile')
        self.assertIn('resolution_reply', step['reason'])
        self.assertEqual(next(iter(self.workflow()['answers'].values()))['provenance'], 'controller_published')

    def test_inconsistent_legacy_receipt_is_not_downgraded_to_semantic_evidence(self):
        self.publish(self.inline_batch(self.next()))
        self.legacy()
        workflow = self.workflow()
        next(iter(workflow['answers'].values()))['receipt']['body'] = 'Inconsistent saved body.'
        self.save_workflow(workflow)
        step = self.next()
        self.assertEqual(step['action'], 'reconcile')
        self.assertIn('inconsistent legacy answer provenance', step['reason'])
        self.assertEqual(self.workflow()['schemaVersion'], 1)

    def test_legacy_reused_top_level_publication_keeps_strict_url_and_body_checks(self):
        self.publish(self.inline_batch(self.next()))
        self.publish(self.top_batch(self.next()))
        self.controller().reconcile()
        step = self.next()
        mapped = step['request_mappings']
        self.publish(self.batch(step, mapped, []))
        # Legacy finish extracted the issue comment identity when it reused a publication URL.
        self.legacy()
        workflow = self.workflow()
        body = next(a for a in workflow['answers'].values() if a['receipt']['kind'] == 'issue_comment')
        body['receipt']['reference'] = 'issuecomment-101'
        self.save_workflow(workflow)
        step = self.next()
        self.assertEqual(step['action'], 'top_level')
        self.assertEqual(next(a for a in self.workflow()['answers'].values()
                              if a['receipt']['kind'] == 'issue_comment')['provenance'], 'controller_published')
        self.github.comments.clear()
        step = self.next()
        self.assertEqual(step['action'], 'reconcile')
        self.assertIn(self.github.posts[1]['url'], step['reason'])
        self.assertIn('controller_published', step['reason'])

    def test_repeated_external_edit_reopens_holds_without_duplicate_invalidation(self):
        self.github.reviews.append(dict(id=99, body='Existing answer.', html_url=PR + '#pullrequestreview-99'))
        self.controller = lambda agent='reply-agent': passes.Controller(
            guard.read(self.record), self.record, PR + '#' + REVIEW, self.folder, agent)
        mapped = [request('test', source='C1', thread='T1', disposition='skip',
                          reason='Covered at ' + PR + '#pullrequestreview-99')]
        self.publish(self.batch(self.next(), mapped, []))
        for text in ('Edit B.', 'Edit C.', 'Edit B.', 'Existing answer.'):
            self.github.reviews[-1]['body'] = text
            step = self.next()
            self.assertEqual(step['action'], 'inline')
            mapped[0].update(disposition='held', reason='User decision pending')
            self.publish(self.batch(step, mapped, []))
            self.assertEqual(self.next()['action'], 'top_level')
            self.publish(self.batch(self.next(), mapped, []))
            self.assertEqual(self.next()['action'], 'done')
        invalidations = [r for r in self.workflow()['answerRevisions'] if r['event'] == 'invalidated']
        self.assertEqual(len(invalidations), 3)
        self.assertEqual(self.github.posts, [])


if __name__ == '__main__':
    unittest.main()
