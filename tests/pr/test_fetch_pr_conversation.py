
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import fetch_pr_conversation as fetcher


def snapshot() -> dict:
    items: list[dict] = []
    canonical = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"complete": True, "itemCount": 0, "items": items,
            "itemsSha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "pullRequest": {"url": "https://github.com/owner/repo/pull/7",
                            "number": 7, "headRefOid": "abc"}}


class FetchPrConversationTest(unittest.TestCase):
    def test_fetches_stable_paginated_review_and_comment_context(self) -> None:
        responses = [
            [[{"id": 2, "body": "review", "state": "COMMENTED",
               "user": {"login": "alice"}, "html_url": "https://example.test/review/2"}]],
            [[{"id": 3, "body": "general comment", "user": {"login": "bob"}}]],
            {"headRefOid": "abc"},
        ] * 2
        with patch.object(fetcher, "gh_json", side_effect=responses) as gh:
            result = fetcher.fetch(snapshot(), Path("."))
        self.assertEqual(gh.call_count, 6)
        self.assertTrue(result["complete"])
        self.assertEqual(result["reviews"][0]["text"], "review")
        self.assertEqual(result["comments"][0]["text"], "general comment")
        self.assertEqual(result["reviewCount"], 1)
        self.assertEqual(result["commentCount"], 1)

    def test_top_level_request_is_work_item_with_no_inline_threads(self) -> None:
        source = {
            "id": 6148373154,
            "body": "Please update build.rs with alternator version reporting.",
            "html_url": "https://github.com/owner/repo/pull/7#issuecomment-6148373154",
            "user": {"login": "reviewer"},
            "created_at": "2026-10-13T14:53:36Z",
            "updated_at": "2026-10-13T14:53:36Z",
        }
        responses = [[[]], [[source]], {"headRefOid": "abc"}] * 2
        with patch.object(fetcher, "gh_json", side_effect=responses):
            result = fetcher.fetch(snapshot(), Path("."))
        self.assertEqual(result["itemCount"], 1)
        item = result["items"][0]
        self.assertEqual(item["reference"], "issuecomment-6148373154")
        self.assertEqual(item["kind"], "issue_comment")
        self.assertEqual(item["replyKind"], "issue_comment")
        self.assertFalse(item["resolvable"])
        self.assertEqual(item["url"], source["html_url"])
        self.assertEqual(item["text"], source["body"])

    def test_review_and_comment_ids_do_not_collide_and_later_answers_are_retained(self) -> None:
        review = {"id": 3, "body": "Please fix version output", "state": "COMMENTED"}
        comment = {"id": 3, "body": "Separate request"}
        answer = {"id": 4, "body": "[In reply to](https://github.com/owner/repo/pull/7#pullrequestreview-3)\n\nfixed."}
        responses = [[[review]], [[comment], [answer]], {"headRefOid": "abc"}] * 2
        with patch.object(fetcher, "gh_json", side_effect=responses):
            result = fetcher.fetch(snapshot(), Path("."))
        self.assertEqual([item["reference"] for item in result["items"]],
                         ["pullrequestreview-3", "issuecomment-3", "issuecomment-4"])
        self.assertEqual(result["comments"][-1]["text"], answer["body"])
        self.assertEqual(result["reviews"][0]["replyKind"], "unclassified")
        self.assertTrue(all(item["replyKind"] == "issue_comment" and not item["resolvable"]
                            for item in result["comments"]))

    def test_edited_comment_during_fetch_requires_matching_snapshots(self) -> None:
        old = {"id": 3, "body": "First request"}
        edited = {"id": 3, "body": "Revised request"}
        responses = [[[]], [[old]], {"headRefOid": "abc"},
                     [[]], [[edited]], {"headRefOid": "abc"},
                     [[]], [[edited]], {"headRefOid": "abc"}]
        with patch.object(fetcher, "gh_json", side_effect=responses) as gh:
            result = fetcher.fetch(snapshot(), Path("."))
        self.assertEqual(gh.call_count, 9)
        self.assertEqual(result["items"][0]["text"], "Revised request")

    def test_rejects_duplicate_page_items(self) -> None:
        with patch.object(fetcher, "gh_json", return_value=[[{"id": 1}], [{"id": 1}]]):
            with self.assertRaisesRegex(fetcher.FetchError, "duplicate item"):
                fetcher.pages("repos/owner/repo/pulls/7/reviews", "github.com", Path("."))

    def test_rejects_changed_head(self) -> None:
        with patch.object(fetcher, "gh_json", side_effect=[[[]], [[]], {"headRefOid": "new"}]):
            with self.assertRaisesRegex(fetcher.FetchError, "head changed"):
                fetcher.fetch(snapshot(), Path("."))


if __name__ == "__main__":
    unittest.main()
