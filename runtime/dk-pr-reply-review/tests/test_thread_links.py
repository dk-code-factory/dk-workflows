import copy
import unittest

import test_reply_passes as helpers
import test_reply_resolution as resolution_tests
from test_reply_passes import guard, passes, PR, REVIEW, request, response


ANCHOR = 'discussion_r4365784024'
FINDING = 'Except block directly handles BaseException.'


class ThreadLinkTest(unittest.TestCase):
    batch = helpers.ReplyPassesTest.batch
    publish = helpers.ReplyPassesTest.publish
    assessment = resolution_tests.ResolutionTest.assessment

    def setUp(self):
        helpers.ReplyPassesTest.setUp(self)
        self.github.reviews[0]['body'] = ''
        self.github.threads[0]['comments'][0].update(
            databaseId=4365784024, author='github-code-quality',
            text="## Except block handles 'BaseException'\n\n" + FINDING +
                 '\n\nUse <code>except Exception as exc:</code> in the worker.',
            url=PR + '#' + ANCHOR)
        other = copy.deepcopy(self.github.threads[0])
        other['id'] = 'T2'
        other['comments'][0].update(reference='C9', databaseId=9, url=PR + '#discussion_r9')
        self.github.threads.append(other)

    def controller(self, agent='reply-agent', pr=PR + '#' + ANCHOR):
        return passes.Controller(guard.read(self.record), self.record, pr, self.folder, agent)

    def authorize(self):
        record = guard.read(self.record)
        record['resolution_authorization'] = {'pr': PR, 'selection': ANCHOR,
                                               'source': 'User requested resolving this thread.'}
        guard.write(self.record, record)

    def test_bot_thread_reply_and_resolution_leave_other_threads_untouched(self):
        self.authorize()
        step = self.controller().next()
        self.assertEqual(step['action'], 'inline')
        self.assertEqual(step['unclassified_sources'], ['C1'])
        self.assertEqual([t['reference'] for t in step['unresolved_threads']], ['T1'])
        self.assertEqual(len(guard.read(step['snapshot'])['threads']), 2)
        batch = self.batch(step, [request('root', source='C1', excerpt=FINDING, thread='T1')],
                           [response('answer', ['root'])])
        self.publish(batch)
        self.assertEqual(len(self.github.posts), 1)
        step = self.controller().next()
        self.assertEqual(step['action'], 'resolve')
        result = self.controller().resolve(self.assessment(step))
        self.assertEqual(result['status'], 'resolved')
        self.assertEqual(self.github.resolves, ['T1'])
        self.assertFalse(self.github.threads[1]['isResolved'])
        self.assertTrue(self.controller().next()['cleanup_complete'])
        self.controller('resumed-agent').reconcile()
        self.assertTrue(self.controller('resumed-agent').next()['cleanup_complete'])
        self.assertEqual(len(self.github.posts), 1)
        self.assertEqual(self.github.resolves, ['T1'])

    def test_existing_answer_requires_no_duplicate_reply(self):
        self.authorize()
        self.github.threads[0]['comments'].append(dict(
            reference='C2', databaseId=2, text='fixed.', reviewReference=None,
            url=PR + '#discussion_r2'))
        step = self.controller().next()
        self.publish(self.batch(step, [request('root', source='C1', excerpt=FINDING, thread='T1',
            disposition='skip', reason='Verified published fix and answer at ' + PR + '#discussion_r2')], []))
        step = self.controller().next()
        self.assertEqual(step['action'], 'resolve')
        self.assertEqual(self.controller().resolve(self.assessment(step))['status'], 'resolved')
        self.assertEqual(self.github.posts, [])

    def test_scope_cannot_expand_through_mappings_or_resume(self):
        step = self.controller().next()
        for source, thread in [('C9', 'T2'), (REVIEW, None)]:
            with self.subTest(source=source):
                self.github.reviews[0]['body'] = 'Add a test.'
                step = self.controller().next()
                with self.assertRaisesRegex(guard.GuardError, 'outside selected review or thread'):
                    self.batch(step, [request('other', source=source, thread=thread,
                        disposition='held', reason='Not selected')], [])
        for pr in (PR, PR + '#' + REVIEW, PR + '#discussion_r9'):
            with self.subTest(pr=pr), self.assertRaisesRegex(guard.GuardError, 'selection changed'):
                self.controller(pr=pr)
        self.assertEqual(self.github.posts, [])
        self.assertEqual(self.github.resolves, [])

    def test_permission_and_changed_head_still_block_resolution(self):
        self.authorize()
        self.github.threads[0]['comments'].append(dict(
            reference='C2', databaseId=2, text='fixed.', url=PR + '#discussion_r2'))
        path = self.assessment()
        self.github.threads[0]['viewerCanResolve'] = False
        with self.assertRaisesRegex(guard.GuardError, 'permission'):
            self.controller().resolve(path)
        self.github.threads[0]['viewerCanResolve'] = True
        self.github.head = 'b' * 40
        with self.assertRaisesRegex(guard.GuardError, 'head changed'):
            self.controller().resolve(path)
        self.assertEqual(self.github.resolves, [])
