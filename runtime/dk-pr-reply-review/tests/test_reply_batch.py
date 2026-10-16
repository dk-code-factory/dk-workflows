import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('reply_batch', Path(__file__).parents[1] / 'scripts/reply_batch.py')
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)
PR = 'https://github.com/owner/repo/pull/7'
REVIEW = 'pullrequestreview-42'


def fixture(with_thread=False, resolved=False):
    metadata = {'owner': 'owner', 'repo': 'repo', 'number': 7, 'url': PR,
                'title': 'Fix', 'description': '', 'headRefOid': 'abc'}
    threads = []
    if with_thread:
        threads = [{'id': 'T1', 'isResolved': resolved, 'path': 'a.py', 'line': 1,
                    'comments': [{'reference': 'C1', 'reviewReference': REVIEW,
                                  'text': 'Add a test.', 'url': PR + '#discussion_r1',
                                  'createdAt': '2026-01-01', 'updatedAt': '2026-01-01'}]}]
    snapshot = guard.inline.build_output(metadata, threads)
    reviews = guard.top.normalize([{'id': 42, 'body': 'Add a test. Document the timeout. Thanks.',
                                    'html_url': PR + '#' + REVIEW}], review=True)
    conversation = {'complete': True, 'pullRequest': {'url': PR, 'headRefOid': 'abc'},
                    'reviews': reviews, 'comments': [], 'items': reviews,
                    'reviewCount': 1, 'commentCount': 0, 'itemCount': 1}
    conversation['sha256'] = guard.digest(conversation)
    return {'schemaVersion': 1, 'snapshot': snapshot, 'conversation': conversation,
            'requests': [], 'replies': []}


def request(key, source=REVIEW, excerpt='Add a test.', thread=None, disposition='reply'):
    return {'id': key, 'source': source, 'excerpt': excerpt, 'threadReference': thread,
            'disposition': disposition}


def reply(key, ids, source=REVIEW, excerpts=('Add a test.',), kind='issue_comment'):
    body = 'fixed.' if kind == 'inline' else f'[In reply to]({PR}#{source})\n\n' + '\n\n'.join(
        guard.quote(excerpt) + '\n\nfixed.' for excerpt in excerpts)
    return {'id': key, 'requestIds': ids, 'destination': {'kind': kind, 'reference': source}, 'body': body}


def body_batch():
    batch = fixture()
    batch['requests'] = [request('r1'), request('r2', excerpt='Document the timeout.')]
    batch['replies'] = [reply('p1', ['r1', 'r2'], excerpts=('Add a test.', 'Document the timeout.'))]
    return batch


def approval(batch, **changes):
    return dict({'batchSha256': guard.digest(batch), 'validatorAgent': 'independent-agent',
                 'pass': 1, 'status': 'approved', 'corrections': []}, **changes)


class ReplyBatchTest(unittest.TestCase):
    def test_body_only_quotes_and_answers_both_requests_in_one_comment(self):
        batch = body_batch()
        guard.validate(batch)
        self.assertEqual(len(batch['replies']), 1)
        self.assertEqual(batch['replies'][0]['body'].count('fixed.'), 2)

    def test_inline_only_and_body_duplicate_share_one_inline_reply(self):
        batch = fixture(True)
        batch['requests'] = [request('r1', source='C1', thread='T1'), request('r2', thread='T1')]
        batch['replies'] = [reply('p1', ['r1', 'r2'], source='T1', kind='inline')]
        guard.validate(batch)

    def test_mixed_review_routes_only_unmatched_request_to_pr(self):
        batch = fixture(True)
        batch['requests'] = [request('r1', thread='T1'), request('r2', excerpt='Document the timeout.')]
        batch['replies'] = [reply('p1', ['r1'], source='T1', kind='inline'),
                            reply('p2', ['r2'], excerpts=('Document the timeout.',))]
        guard.validate(batch)
        self.assertNotIn('Add a test.', batch['replies'][1]['body'])

    def test_resolved_inline_answer_and_informational_text_skip_without_validator(self):
        batch = fixture(True, resolved=True)
        batch['requests'] = [dict(request('r1', thread='T1', disposition='skip'), reason='Answered at URL'),
                             dict(request('r2', excerpt='Thanks.', disposition='skip'),
                                  reason='Informational acknowledgement')]
        with tempfile.TemporaryDirectory() as folder, patch.object(guard, 'fetch_live') as fetch:
            self.assertEqual(guard.publish(batch, {}, Path(folder) / 'journal', Path('.'))['status'], 'empty')
            fetch.assert_not_called()

    def test_bare_acknowledgement_and_link_only_acknowledgement_rejected(self):
        for body in ('fixed.', f'[In reply to]({PR}#{REVIEW})\n\nfixed.'):
            batch = body_batch()
            batch['replies'][0]['body'] = body
            with self.assertRaises(guard.GuardError):
                guard.validate(batch)

    def test_each_excerpt_must_be_exact_and_have_its_own_answer(self):
        for transform in (lambda b: b.replace('Add a test.', 'Add tests.'),
                          lambda b: b.replace('> Add a test.\n\nfixed.', '> Add a test.')):
            batch = body_batch()
            batch['replies'][0]['body'] = transform(batch['replies'][0]['body'])
            with self.assertRaises(guard.GuardError):
                guard.validate(batch)

    def test_pr_reply_cannot_duplicate_inline_request_even_without_mapping(self):
        for target in (None, 'T1'):
            batch = fixture(True)
            batch['requests'] = [request('r1', thread=target)]
            batch['replies'] = [reply('p1', ['r1'])]
            with self.assertRaises(guard.GuardError):
                guard.validate(batch)

    def test_cross_review_mapping_requires_matching_comment_evidence(self):
        batch = fixture(True)
        batch['requests'] = [request('r1', thread='T1')]
        batch['replies'] = [reply('p1', ['r1'], source='T1', kind='inline')]
        batch['snapshot']['threads'][0]['comments'][0]['reviewReference'] = 'pullrequestreview-99'
        batch['snapshot'] = guard.inline.build_output(
            dict(owner='owner', repo='repo', **batch['snapshot']['pullRequest']), batch['snapshot']['threads'])
        with self.assertRaisesRegex(guard.GuardError, 'matching comment'):
            guard.validate(batch)

    def test_cross_source_mapping_supports_replies_and_answered_duplicates(self):
        for kind in ('review', 'issue_comment'):
            for answered in (False, True):
                with self.subTest(kind=kind, answered=answered):
                    batch = fixture(True, resolved=answered)
                    source = guard.top.normalize([{'id': 99, 'body': 'Please add a regression test.',
                                                  'html_url': PR + '#source99'}], review=kind == 'review')[0]
                    conversation = batch['conversation']
                    conversation['reviews' if kind == 'review' else 'comments'].append(source)
                    conversation['items'] = conversation['reviews'] + conversation['comments']
                    conversation.update(reviewCount=len(conversation['reviews']),
                                        commentCount=len(conversation['comments']), itemCount=len(conversation['items']))
                    conversation['sha256'] = guard.digest({k: v for k, v in conversation.items() if k != 'sha256'})
                    mapped = request('r1', source=source['reference'], excerpt=source['text'], thread='T1',
                                     disposition='skip' if answered else 'reply')
                    mapped.update(matchedCommentReference='C1', matchedExcerpt='Add a test.')
                    if answered:
                        mapped['reason'] = 'Already answered at ' + PR + '#discussion_r2'
                    batch['requests'] = [mapped]
                    batch['replies'] = [] if answered else [reply('p1', ['r1'], source='T1', kind='inline')]
                    guard.validate(batch)
                    for field, bad_value in (('matchedCommentReference', source['reference']),
                                             ('matchedExcerpt', 'Invented request')):
                        bad = copy.deepcopy(batch)
                        bad['requests'][0][field] = bad_value
                        with self.assertRaises(guard.GuardError):
                            guard.validate(bad)
                    if not answered:
                        bad = copy.deepcopy(batch)
                        bad['replies'] = [reply('p1', ['r1'], source=source['reference'], excerpts=(source['text'],))]
                        with self.assertRaisesRegex(guard.GuardError, 'duplicates inline'):
                            guard.validate(bad)

    def test_closed_thread_cannot_receive_reply(self):
        batch = fixture(True, resolved=True)
        batch['requests'] = [request('r1', thread='T1')]
        batch['replies'] = [reply('p1', ['r1'], source='T1', kind='inline')]
        with self.assertRaisesRegex(guard.GuardError, 'open thread'):
            guard.validate(batch)

    def test_duplicate_reply_or_held_request_rejected(self):
        for change in ('duplicate', 'held'):
            batch = body_batch()
            if change == 'duplicate':
                batch['replies'].append(copy.deepcopy(batch['replies'][0]))
            else:
                batch['requests'][0].update(disposition='held', reason='User choice pending')
            with self.assertRaises(guard.GuardError):
                guard.validate(batch)

    def test_approval_rejects_changed_batch_rejection_corrections_and_third_pass(self):
        batch = body_batch()
        verdict = approval(batch)
        changed = copy.deepcopy(batch)
        changed['replies'][0]['body'] += '\nEdited.'
        for candidate, decision in [(changed, verdict), (batch, approval(batch, status='rejected')),
                                    (batch, approval(batch, corrections=['Fix routing'])),
                                    (batch, approval(batch, **{'pass': 3}))]:
            with self.assertRaises(guard.GuardError):
                guard.require_approval(candidate, decision)
        guard.require_approval(batch, approval(batch, **{'pass': 2}))

    def test_head_source_edit_later_reply_or_resolution_invalidates_evidence(self):
        batch = body_batch()
        expected = guard.validate(batch)
        for change in ('head', 'text', 'later', 'threads'):
            live = copy.deepcopy(expected)
            if change == 'head':
                live['head'] = 'new'
            elif change == 'text':
                live['items'][0]['text'] += ' Edited.'
            elif change == 'later':
                live['items'].append({'reference': 'later', 'text': 'Already answered'})
            else:
                live['threads'].append({'id': 'new-thread'})
            with self.assertRaisesRegex(guard.GuardError, 'changed'):
                guard.unchanged(expected, live, [])

    def test_rejected_batch_never_reads_live_or_posts(self):
        batch = body_batch()
        with tempfile.TemporaryDirectory() as folder, patch.object(guard, 'post') as post:
            with self.assertRaises(guard.GuardError):
                guard.publish(batch, approval(batch, status='rejected'), Path(folder) / 'journal', Path('.'))
            post.assert_not_called()

    def test_successful_publication_verified_and_journal_cannot_replay(self):
        batch = body_batch()
        expected = guard.validate(batch)
        response = batch['replies'][0]
        receipt = dict(response['destination'], id='p1', url=PR + '#issuecomment-99', body=response['body'])
        after = copy.deepcopy(expected)
        after['items'].append({'url': receipt['url'], 'text': receipt['body']})
        with tempfile.TemporaryDirectory() as folder:
            journal = Path(folder) / 'journal.json'
            with patch.object(guard, 'fetch_live', side_effect=[expected, after]), patch.object(
                    guard, 'post', return_value=receipt) as post:
                result = guard.publish(batch, approval(batch), journal, Path('.'))
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(json.loads(journal.read_text())['replies'][0]['url'], receipt['url'])
            self.assertEqual(post.call_count, 1)
            with self.assertRaises(FileExistsError):
                guard.publish(batch, approval(batch), journal, Path('.'))

    def test_uncertain_mutation_leaves_in_flight_journal(self):
        batch = body_batch()
        with tempfile.TemporaryDirectory() as folder:
            journal = Path(folder) / 'journal.json'
            with patch.object(guard, 'fetch_live', return_value=guard.validate(batch)), patch.object(
                    guard, 'post', side_effect=guard.GuardError('uncertain')):
                with self.assertRaisesRegex(guard.GuardError, 'uncertain'):
                    guard.publish(batch, approval(batch), journal, Path('.'))
            self.assertEqual(json.loads(journal.read_text())['status'], 'in_flight')

    def test_live_head_change_prevents_mutation(self):
        batch = body_batch()
        live = guard.validate(batch)
        live['head'] = 'changed'
        with tempfile.TemporaryDirectory() as folder, patch.object(guard, 'fetch_live', return_value=live), patch.object(guard, 'post') as post:
            with self.assertRaisesRegex(guard.GuardError, 'changed'):
                guard.publish(batch, approval(batch), Path(folder) / 'journal', Path('.'))
            post.assert_not_called()

    def test_mixed_batch_can_publish_both_destinations_with_fresh_checks(self):
        batch = fixture(True)
        batch['requests'] = [request('r1', thread='T1'), request('r2', excerpt='Document the timeout.')]
        batch['replies'] = [reply('p1', ['r1'], source='T1', kind='inline'),
                            reply('p2', ['r2'], excerpts=('Document the timeout.',))]
        expected = guard.validate(batch)
        receipts = [dict(r['destination'], id=r['id'], url=PR + '#' + r['id'], body=r['body'])
                    for r in batch['replies']]
        after_inline = copy.deepcopy(expected)
        after_inline['threads'][0]['comments'].append({'url': receipts[0]['url'], 'text': receipts[0]['body']})
        after_both = copy.deepcopy(after_inline)
        after_both['items'].append({'url': receipts[1]['url'], 'text': receipts[1]['body']})
        with tempfile.TemporaryDirectory() as folder, patch.object(
                guard, 'fetch_live', side_effect=[expected, after_inline, after_both]) as fetch, patch.object(
                guard, 'post', side_effect=receipts) as post:
            result = guard.publish(batch, approval(batch), Path(folder) / 'journal', Path('.'))
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(post.call_count, 2)

    def test_issue_comment_source_also_requires_a_quote(self):
        batch = fixture()
        comments = guard.top.normalize([{'id': 7, 'body': 'Explain the timeout.',
                                         'html_url': PR + '#issuecomment-7'}], review=False)
        conversation = batch['conversation']
        conversation.update(comments=comments, items=conversation['reviews'] + comments,
                            commentCount=1, itemCount=2)
        conversation['sha256'] = guard.digest({k: v for k, v in conversation.items() if k != 'sha256'})
        batch['requests'] = [request('r1', source='issuecomment-7', excerpt='Explain the timeout.')]
        batch['replies'] = [reply('p1', ['r1'], source='issuecomment-7', excerpts=('Explain the timeout.',))]
        guard.validate(batch)
        batch['replies'][0]['body'] = 'fixed.'
        with self.assertRaises(guard.GuardError):
            guard.validate(batch)

    def test_post_uses_correct_endpoints_and_exact_body(self):
        for response in (reply('p', ['r']), reply('p', ['r'], source='T1', kind='inline')):
            inline = response['destination']['kind'] == 'inline'
            comment = {'body': response['body'], 'url' if inline else 'html_url': PR + '#reply'}
            payload = {'data': {'addPullRequestReviewThreadReply': {'comment': comment}}} if inline else comment
            with patch.object(guard.subprocess, 'run') as run:
                run.return_value.returncode = 0
                run.return_value.stdout = json.dumps(payload)
                receipt = guard.post(response, PR, Path('.'))
            args = run.call_args.args[0]
            self.assertIn('graphql' if inline else 'repos/owner/repo/issues/7/comments', args)
            sent = json.loads(run.call_args.kwargs['input'])
            self.assertEqual((sent['variables'] if inline else sent)['body'], response['body'])
            self.assertEqual(receipt['url'], PR + '#reply')


if __name__ == '__main__':
    unittest.main()
