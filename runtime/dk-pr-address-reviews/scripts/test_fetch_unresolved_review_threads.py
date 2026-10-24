
import unittest
from pathlib import Path
from unittest.mock import patch

import fetch_unresolved_review_threads as fetcher


def comment(reference: str, created_at: str, text: str) -> dict:
    return {
        "id": reference,
        "databaseId": "1",
        "body": text,
        "url": f"https://example.test/{reference}",
        "createdAt": created_at,
        "author": {"login": "reviewer"},
        "updatedAt": created_at,
        "pullRequestReview": {"id": "REVIEW_NODE", "databaseId": "42"},
    }


def thread(
    reference: str,
    path: str,
    line: int,
    comments: list[dict],
    *,
    resolved: bool = False,
    comments_have_next_page: bool = False,
    comments_cursor: str | None = None,
) -> dict:
    return {
        "id": reference,
        "isResolved": resolved,
        "isOutdated": False,
        "path": path,
        "line": line,
        "originalLine": line,
        "diffSide": "RIGHT",
        "startLine": None,
        "comments": {
            "nodes": comments,
            "pageInfo": {
                "hasNextPage": comments_have_next_page,
                "endCursor": comments_cursor,
            },
        },
    }


class FetchUnresolvedReviewThreadsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.metadata = {
            "owner": "owner",
            "repo": "repo",
            "hostname": "github.com",
            "number": 7,
            "url": "https://github.com/owner/repo/pull/7",
            "state": "OPEN",
            "title": "Fix retry shutdown",
            "description": "Scope: retry shutdown. Deferred: metrics cleanup.",
            "headRefName": "fix",
            "headRefOid": "abc",
            "headRepository": {"nameWithOwner": "contributor/repo"},
            "baseRefName": "main",
            "baseRefOid": "def",
            "closingIssuesReferences": [{"number": 12, "url": "https://github.com/owner/repo/issues/12"}],
            "isCrossRepository": True,
        }

    def test_review_url_is_canonicalized_without_losing_selection(self):
        raw = dict(self.metadata, body='', url=self.metadata['url'])
        with patch.object(fetcher, 'run_gh', return_value=raw) as gh:
            result = fetcher.resolve_pull_request(raw['url'] + '#pullrequestreview-42', Path('.'))
        self.assertEqual(gh.call_args.args[0][2], raw['url'])
        self.assertEqual(result['reviewReference'], 'pullrequestreview-42')
        comments = fetcher.normalize_comments([comment('C1', '2026-01-01', 'Fix')])
        threads = [{'id': 'T1', 'isResolved': False, 'comments': comments},
                   {'id': 'T2', 'isResolved': True, 'comments': comments},
                   {'id': 'T3', 'isResolved': False, 'comments': [dict(comments[0], reviewReference=None)]}]
        output = fetcher.build_output(result, threads)
        self.assertEqual(output['selection']['threadReferences'], ['T1', 'T2'])
        self.assertEqual(output['threadCount'], 3)
        self.assertEqual(output['itemCount'], 2)  # Full PR evidence remains available.

    def test_comment_url_selects_only_its_thread_and_keeps_full_evidence(self):
        raw = dict(self.metadata, body='')
        anchor = 'discussion_r4365784024'
        with patch.object(fetcher, 'run_gh', return_value=raw) as gh:
            metadata = fetcher.resolve_pull_request(raw['url'] + '#' + anchor, Path('.'))
        self.assertEqual(gh.call_args.args[0][2], raw['url'])
        selected = fetcher.normalize_comments([dict(comment('C1', '2026-01-01', 'Fix'),
                                                   databaseId="4365784024")])
        self.assertEqual(selected[0]['databaseId'], 4365784024)
        other = [dict(selected[0], reference='C2', databaseId=2)]
        threads = [{'id': 'T1', 'isResolved': False, 'comments': selected},
                   {'id': 'T2', 'isResolved': False, 'comments': other}]
        output = fetcher.build_output(metadata, threads)
        self.assertEqual(output['selection'], {'commentReference': anchor, 'threadReferences': ['T1']})
        self.assertEqual(output['threadCount'], 2)
        self.assertEqual(output['itemCount'], 2)
        threads[0]['isResolved'] = True
        self.assertEqual(fetcher.build_output(metadata, threads)['selection'], output['selection'])
        # A link to a later reply must select the same complete thread.
        threads[0]['comments'].append(dict(selected[0], reference='C3', databaseId=3))
        self.assertEqual(fetcher.selection_for('discussion_r3', threads)['threadReferences'], ['T1'])

    def test_missing_or_ambiguous_comment_selection_never_expands_scope(self):
        selected = [dict(reference='C1', databaseId=4365784024)]
        for threads in ([], [{'id': 'T1', 'comments': selected}, {'id': 'T2', 'comments': selected}]):
            with self.subTest(threads=threads):
                with self.assertRaises(fetcher.FetchError):
                    fetcher.selection_for('discussion_r4365784024', threads)
        for anchor in ('discussion_r', 'discussion_rbad', 'issuecomment-1'):
            with self.subTest(anchor=anchor), self.assertRaises(fetcher.FetchError):
                fetcher.split_pr_reference(self.metadata['url'] + '#' + anchor)

    def test_normalizes_large_comment_and_review_ids_without_changing_output_types(self):
        for scalar in (int, str):
            with self.subTest(scalar=scalar.__name__):
                raw = comment('C1', '2026-01-01', 'Fix')
                raw['databaseId'] = scalar(4365784024)
                raw['pullRequestReview']['databaseId'] = scalar(5636543336)
                normalized = fetcher.normalize_comments([raw])[0]
                self.assertEqual(normalized['databaseId'], 4365784024)
                self.assertIsInstance(normalized['databaseId'], int)
                self.assertEqual(normalized['reviewReference'], 'pullrequestreview-5636543336')

    def test_paginates_threads_and_nested_comments(self) -> None:
        first_thread = thread(
            "THREAD_B",
            "z.py",
            20,
            [comment("COMMENT_1", "2026-01-01T00:00:00Z", "root")],
            comments_have_next_page=True,
            comments_cursor="comments-1",
        )
        second_thread = thread(
            "THREAD_A",
            "a.py",
            10,
            [comment("COMMENT_3", "2026-01-01T00:00:02Z", "other")],
        )
        responses = [
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [first_thread],
                                "pageInfo": {"hasNextPage": True, "endCursor": "threads-1"},
                            }
                        }
                    }
                }
            },
            {
                "data": {
                    "node": {
                        "comments": {
                            "nodes": [dict(comment("COMMENT_2", "2026-01-01T00:00:01Z", "reply"),
                                           databaseId="4365784024")],
                            "pageInfo": {"hasNextPage": False, "endCursor": "comments-2"},
                        }
                    }
                }
            },
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [second_thread],
                                "pageInfo": {"hasNextPage": False, "endCursor": "threads-2"},
                            }
                        }
                    }
                }
            },
        ]
        with patch.object(fetcher, "graphql", side_effect=responses) as graphql:
            threads = fetcher.fetch_all_threads(self.metadata, Path("."))

        self.assertEqual(graphql.call_count, 3)
        for value in threads:
            for entry in value["comments"]:
                self.assertEqual(entry["reviewReference"], "pullrequestreview-42")
                self.assertEqual(entry["reviewNodeId"], "REVIEW_NODE")
                self.assertEqual(entry["updatedAt"], entry["createdAt"])
        output = fetcher.build_output(self.metadata, threads)
        self.assertEqual([item["reference"] for item in output["items"]], ["THREAD_A", "THREAD_B"])
        self.assertEqual(
            [entry["reference"] for entry in output["items"][1]["conversation"]],
            ["COMMENT_1", "COMMENT_2"],
        )
        selected = fetcher.build_output(dict(self.metadata, commentReference='discussion_r4365784024'), threads)
        self.assertEqual(selected['selection']['threadReferences'], ['THREAD_B'])

    def test_filters_resolved_threads_and_preserves_reference_and_text(self) -> None:
        unresolved = thread(
            "THREAD_OPEN",
            "a.py",
            1,
            [comment("COMMENT_OPEN", "2026-01-01T00:00:00Z", "fix this")],
        )
        resolved = thread(
            "THREAD_DONE",
            "b.py",
            2,
            [comment("COMMENT_DONE", "2026-01-01T00:00:00Z", "done")],
            resolved=True,
        )
        for value in (unresolved, resolved):
            connection = value["comments"]
            value["comments"] = fetcher.normalize_comments(connection["nodes"])

        output = fetcher.build_output(self.metadata, [resolved, unresolved])
        self.assertEqual(output["itemCount"], 1)
        self.assertEqual(output["items"][0]["reference"], "THREAD_OPEN")
        self.assertEqual(output["items"][0]["text"], "fix this")
        self.assertEqual(output["threadCount"], 2)
        self.assertEqual({t["id"] for t in output["threads"]}, {"THREAD_OPEN", "THREAD_DONE"})
        self.assertRegex(output["itemsSha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(output["pullRequest"]["headRepository"]["nameWithOwner"],
                         "contributor/repo")
        self.assertEqual(output["pullRequest"]["baseRefOid"], "def")
        self.assertEqual(output["pullRequest"]["closingIssuesReferences"][0]["number"], 12)

    def test_stable_fetch_requires_two_matching_snapshots(self) -> None:
        unresolved = thread(
            "THREAD_OPEN",
            "a.py",
            1,
            [comment("COMMENT_OPEN", "2026-01-01T00:00:00Z", "fix this")],
        )
        connection = unresolved["comments"]
        unresolved["comments"] = fetcher.normalize_comments(connection["nodes"])
        with (
            patch.object(fetcher, "resolve_pull_request", return_value=self.metadata) as resolve,
            patch.object(fetcher, "fetch_all_threads", return_value=[unresolved]) as fetch,
        ):
            output = fetcher.fetch_stable_snapshot("7", Path("."))

        self.assertEqual(output["itemCount"], 1)
        self.assertEqual(resolve.call_count, 2)
        self.assertEqual(fetch.call_count, 2)


if __name__ == "__main__":
    unittest.main()
