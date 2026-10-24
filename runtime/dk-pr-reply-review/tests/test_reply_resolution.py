import copy
import unittest
from unittest.mock import patch

import test_reply_passes as helpers
from test_reply_passes import guard, passes, PR, REVIEW, request


class ResolutionTest(unittest.TestCase):
    setUp = helpers.ReplyPassesTest.setUp
    batch = helpers.ReplyPassesTest.batch
    publish = helpers.ReplyPassesTest.publish

    def controller(self, agent='reply-agent', pr=PR + '#' + REVIEW):
        return passes.Controller(guard.read(self.record), self.record, pr, self.folder, agent)

    def authorize(self):
        record = guard.read(self.record)
        record['resolution_authorization'] = {'pr': PR, 'selection': REVIEW,
                                               'source': 'User requested resolving this review.'}
        guard.write(self.record, record)

    def existing_answer(self):
        self.github.threads[0]['comments'].append(dict(reference='C2', text='Already covered by the remote test.',
                                                       reviewReference=None, url=PR + '#discussion_r2'))

    def assessment(self, step=None):
        step = step or self.controller().next()
        thread = self.github.threads[0]
        value = dict(schemaVersion=1, threadReference='T1', head=step['head'], snapshot=step['snapshot'],
                     scopeFingerprint=step['scopeFingerprint'],
                     conversationFingerprint=step['unresolved_threads'][0]['conversationFingerprint'],
                     reply={k: thread['comments'][-1][k] for k in ('url', 'text')},
                     fullyAddressed=True, pendingDecision=False, unpublishedFix=False, unresolvedObjections=False,
                     requests=[dict(source=c['reference'], reason='Verified against published regression test.')
                               for c in thread['comments'][:-1]],
                     evidence=['Remote test at pinned head covers the requested case.'])
        path = self.folder / 'assessment.json'
        guard.write(path, value)
        return path

    def test_empty_review_selects_multiple_threads_and_excludes_unrelated_mutations(self):
        self.github.reviews[0]['body'] = ''
        second = copy.deepcopy(self.github.threads[0])
        second.update(id='T2', isResolved=True)
        second['comments'][0].update(reference='C3', url=PR + '#discussion_r3')
        other = copy.deepcopy(second)
        other.update(id='T3', isResolved=False)
        other['comments'][0].update(reference='C4', reviewReference='pullrequestreview-99')
        self.github.threads.extend([second, other])
        self.existing_answer()
        self.authorize()
        step = self.controller().next()
        self.assertEqual([t['reference'] for t in step['unresolved_threads']], ['T1'])
        snapshot = guard.read(step['snapshot'])
        self.assertEqual(snapshot['selection']['threadReferences'], ['T1', 'T2'])
        self.assertEqual(len(snapshot['threads']), 3)
        path = self.assessment(step)
        self.assertEqual(self.controller().resolve(path)['status'], 'resolved')
        self.assertEqual(self.github.resolves, ['T1'])
        self.assertEqual(self.github.posts, [])
        self.assertFalse(self.github.threads[2]['isResolved'])
        bad = dict(schemaVersion=1, snapshot=snapshot, conversation=guard.read(step['conversation']),
                   requests=[request('other', source='C4', thread='T3')],
                   replies=[dict(id='r', requestIds=['other'], body='fixed.',
                                 destination=dict(kind='inline', reference='T3'))])
        with self.assertRaisesRegex(guard.GuardError, 'outside selected review'):
            guard.validate(bad)

    def test_answered_threads_require_resolution_even_when_review_and_ci_are_incomplete(self):
        self.existing_answer()
        self.authorize()
        record = guard.read(self.record)
        record.update(review_results=[{'complete': False}], ci={'result': 'pending'})
        guard.write(self.record, record)
        for _ in range(2):
            step = self.controller().next()
            mapped = request('existing', source='C1', thread='T1', disposition='skip',
                             reason='Answered at ' + PR + '#discussion_r2')
            self.publish(self.batch(step, [mapped], []))
        step = self.controller().next()
        self.assertEqual(step['action'], 'resolve')
        self.assertEqual([t['reference'] for t in step['resolution_candidates']], ['T1'])
        self.assertEqual(step['unreplied_threads'], [])
        self.assertEqual(len(step['unresolved_threads']), 1)
        self.assertFalse(step['cleanup_complete'])
        self.controller().resolve(self.assessment(step))
        self.assertTrue(self.controller().next()['cleanup_complete'])
        self.assertEqual(self.github.posts, [])

    def test_reply_only_permission_does_not_direct_resolution(self):
        self.existing_answer()
        for _ in range(2):
            step = self.controller().next()
            mapped = request('existing', source='C1', thread='T1', disposition='skip',
                             reason='Answered at ' + PR + '#discussion_r2')
            self.publish(self.batch(step, [mapped], []))
        step = self.controller().next()
        self.assertEqual(step['action'], 'done')
        self.assertEqual(step['resolution_candidates'], [])
        self.assertFalse(step['cleanup_complete'])

    def test_publication_is_verified_before_resolution(self):
        self.authorize()
        step = self.controller().next()
        from test_reply_passes import response
        batch = self.batch(step, [request('root', source='C1', thread='T1')], [response('r', ['root'])])
        self.publish(batch)
        self.assertEqual(len(self.github.posts), 1)
        path = self.assessment()
        result = self.controller().resolve(path)
        self.assertEqual(result['status'], 'resolved')
        self.assertEqual(self.github.resolves, ['T1'])
        self.assertEqual(len(self.github.posts), 1)

    def test_resume_rejects_selection_changes(self):
        self.controller().next()
        for pr in (PR, PR + '#pullrequestreview-99'):
            with self.assertRaisesRegex(guard.GuardError, 'selection changed'):
                self.controller(pr=pr)

    def test_authorization_and_assessment_blockers(self):
        self.existing_answer()
        path = self.assessment()
        with self.assertRaisesRegex(guard.GuardError, 'authorization'):
            self.controller().resolve(path)
        self.authorize()
        original = guard.read(path)
        for field, value in [('head', 'b' * 40), ('scopeFingerprint', 'changed'),
                             ('conversationFingerprint', 'changed'), ('pendingDecision', True),
                             ('unpublishedFix', True), ('unresolvedObjections', True),
                             ('requests', []), ('evidence', []), ('reply', {'url': 'missing', 'text': 'answer'}),
                             ('threadReference', REVIEW)]:
            with self.subTest(field=field):
                guard.write(path, dict(original, **{field: value}))
                with self.assertRaises(guard.GuardError):
                    self.controller().resolve(path)
        self.assertEqual(self.github.resolves, [])

    def test_live_head_scope_conversation_and_permissions_block(self):
        self.existing_answer()
        self.authorize()
        path = self.assessment()
        self.github.head = 'b' * 40
        with self.assertRaisesRegex(guard.GuardError, 'head changed'):
            self.controller().resolve(path)
        self.github.head = guard.read(path)['head']
        self.github.reviews[0]['body'] += ' Still broken.'
        with self.assertRaisesRegex(guard.GuardError, 'conversation changed'):
            self.controller().resolve(path)
        self.github.reviews[0]['body'] = self.github.reviews[0]['body'].removesuffix(' Still broken.')
        with patch.object(self.controller().__class__, 'scope_fingerprint', return_value='changed'):
            with self.assertRaisesRegex(guard.GuardError, 'scope changed'):
                self.controller().resolve(path)
        self.github.threads[0]['viewerCanResolve'] = False
        with self.assertRaisesRegex(guard.GuardError, 'permission'):
            self.controller().resolve(path)
        self.assertEqual(self.github.resolves, [])

    def test_later_objection_and_held_mapping_block(self):
        self.existing_answer()
        self.authorize()
        step = self.controller().next()
        held = request('hold', source='C1', thread='T1', disposition='held', reason='Pending user choice')
        self.publish(self.batch(step, [held], []))
        path = self.assessment()
        with self.assertRaisesRegex(guard.GuardError, 'held'):
            self.controller().resolve(path)
        record = guard.read(self.record)
        record['reply_workflow']['requests'] = []
        guard.write(self.record, record)
        path = self.assessment()
        self.github.threads[0]['comments'].append(dict(reference='C3', text='Still broken', url=PR + '#discussion_r3'))
        with self.assertRaisesRegex(guard.GuardError, 'conversation changed'):
            self.controller().resolve(path)
        step = self.controller().next()
        old = guard.read(path)
        old.update(snapshot=step['snapshot'], conversationFingerprint=step['unresolved_threads'][0]['conversationFingerprint'])
        guard.write(path, old)
        with self.assertRaisesRegex(guard.GuardError, 'every request'):
            self.controller().resolve(path)
        old['requests'].append(dict(source='C3', reason='Reviewer reports another failure'))
        guard.write(path, old)
        with self.assertRaisesRegex(guard.GuardError, 'later responses'):
            self.controller().resolve(path)

    def test_later_acknowledgement_reuses_existing_answer(self):
        self.existing_answer()
        self.authorize()
        path = self.assessment()
        old = guard.read(path)
        self.github.threads[0]['comments'].append(dict(reference='C3', text='Thanks, this covers it.',
                                                      url=PR + '#discussion_r3'))
        step = self.controller().next()
        old.update(snapshot=step['snapshot'], conversationFingerprint=step['unresolved_threads'][0]['conversationFingerprint'])
        old['requests'].append(dict(source='C3', reason='Reviewer confirms the answer is sufficient.',
                                    disposition='no_objection'))
        guard.write(path, old)
        self.assertEqual(self.controller().resolve(path)['status'], 'resolved')
        self.assertEqual(self.github.posts, [])
        self.assertEqual(self.github.resolves, ['T1'])

    def test_already_resolved_needs_no_mutation(self):
        self.existing_answer()
        self.authorize()
        path = self.assessment()
        self.github.threads[0]['isResolved'] = True
        self.assertEqual(self.controller().resolve(path)['status'], 'already_resolved')
        self.assertEqual(self.github.resolves, [])

    def test_uncertain_accepted_resolution_reconciles_without_replay(self):
        self.existing_answer()
        self.authorize()
        path = self.assessment()
        self.github.uncertain = True
        self.assertEqual(self.controller().resolve(path)['action'], 'reconcile')
        self.assertEqual(self.controller().next()['action'], 'reconcile')
        self.controller('resumed-agent').reconcile()
        record = guard.read(self.record)['reply_workflow']
        self.assertTrue(record['resolutions'][0]['receipt']['isResolved'])
        self.controller('resumed-agent').next()
        self.assertEqual(self.github.resolves, ['T1'])
        self.assertEqual(self.github.posts, [])

    def test_missing_reply_and_permission_failure_never_retry_mutation(self):
        self.existing_answer()
        self.authorize()
        path = self.assessment()
        reply = self.github.threads[0]['comments'].pop()
        with self.assertRaisesRegex(guard.GuardError, 'conversation changed'):
            self.controller().resolve(path)
        self.github.threads[0]['comments'].append(reply)
        with patch.object(guard.inline, 'graphql', side_effect=guard.inline.FetchError('permission denied')):
            self.assertEqual(self.controller().resolve(path)['action'], 'reconcile')
        self.controller().reconcile()
        self.assertEqual(self.github.resolves, [])
        self.assertEqual(self.github.posts, [])
        with self.assertRaisesRegex(guard.GuardError, 'already attempted'):
            self.controller().resolve(path)

    def test_interruption_before_mutation_requires_reconciliation_and_fresh_assessment(self):
        self.existing_answer()
        self.authorize()
        path = self.assessment()
        with patch.object(guard.inline, 'graphql', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.controller().resolve(path)
        self.assertEqual(self.controller().next()['action'], 'reconcile')
        self.controller().reconcile()
        with self.assertRaisesRegex(guard.GuardError, 'already attempted'):
            self.controller().resolve(path)
        self.assertFalse(guard.read(self.record)['reply_workflow']['resolutions'][0]['receipt']['isResolved'])
        path = self.assessment()
        self.controller().resolve(path)
        self.assertEqual(self.github.resolves, ['T1'])
