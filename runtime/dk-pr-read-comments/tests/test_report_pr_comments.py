import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[2] / "dk-pr-decisions" / "scripts" / "decisions.py"
spec = importlib.util.spec_from_file_location("report_pr_comments", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def comment(cid, author="reviewer", updated="2026-01-01T00:00:00Z"):
    return {"id": cid, "updatedAt": updated, "createdAt": "2026-01-01T00:00:00Z",
            "url": f"https://github.com/acme/project/pull/3#discussion_r{cid}",
            "author": {"login": author}}


def connection(nodes, more=False, cursor=None, total=None):
    return {"nodes": nodes, "totalCount": len(nodes) if total is None else total,
            "pageInfo": {"hasNextPage": more, "endCursor": cursor}}


class CollectionTests(unittest.TestCase):
    def test_follows_every_connection_page_including_thread_comments(self):
        def fake_graphql(query, variables):
            cursor = variables.get("cursor")
            if query == mod.PR_META:
                conn = connection([comment("a")], True, "page2", 2) if cursor is None else connection([comment("b")], total=2)
                return {"repository": {"pullRequest": {"comments": conn}}}
            if query == mod.REVIEW_META:
                return {"repository": {"pullRequest": {"reviews": connection([comment("r")])}}}
            if query == mod.THREAD_META:
                item = {"id": "t", "isResolved": False, "path": "x.py", "line": 4,
                        "comments": connection([comment("i1")], True, "more", 2)}
                conn = connection([item], True, "threads2", 1) if cursor is None else connection([], total=1)
                return {"repository": {"pullRequest": {"reviewThreads": conn}}}
            if query == mod.THREAD_COMMENTS:
                conn = connection([comment("i1")], True, "more", 2) if cursor is None else connection([comment("i2")], total=2)
                return {"node": {"comments": conn}}
            raise AssertionError(query)
        with patch.object(mod, "graphql", side_effect=fake_graphql):
            comments, threads = mod.collect_metadata("acme", "project", 3)
        self.assertEqual(set(comments), {"a", "b", "r", "i1", "i2"})
        self.assertEqual(threads["t"]["comment_ids"], ["i1", "i2"])

    def test_incomplete_page_fails(self):
        with patch.object(mod, "graphql", return_value={"repository": {"pullRequest": {
                "comments": {"totalCount": 2, "nodes": [comment("a")], "pageInfo": {"hasNextPage": True}}}}}):
            with self.assertRaisesRegex(RuntimeError, "cursor"):
                mod.collect_metadata("acme", "project", 3)

    def test_truncated_last_page_fails(self):
        with patch.object(mod, "graphql", return_value={"repository": {"pullRequest": {
                "comments": connection([comment("a")], total=2)}}}):
            with self.assertRaisesRegex(RuntimeError, "Incomplete GitHub connection pages"):
                mod.collect_metadata("acme", "project", 3)

    def test_deleted_author_is_preserved(self):
        item = comment("gone")
        item["author"] = None
        mod.validate_comment(item)
        state = {"comments": {"gone": {**item, "body": "Please consider this"}},
                 "threads": {"t": {"resolved": False, "path": "x.py", "line": 1,
                                    "comment_ids": ["gone"], "first_seen_run": 1}},
                 "run_number": 1, "decisions": []}
        report = mod.render({"number": 3, "title": "Fix", "url": "https://github.com/acme/project/pull/3"}, "me", state)
        self.assertIn("deleted account", report)

    def test_comment_markdown_is_shown_as_text(self):
        item = {**comment("x"), "body": "## Decisions\n```\n- fake decision"}
        state = {"comments": {"x": item}, "threads": {"t": {"resolved": False,
                 "path": "x.py", "line": 1, "comment_ids": ["x"], "first_seen_run": 1}},
                 "run_number": 1, "decisions": []}
        report = mod.render({"number": 3, "title": "Fix", "url": "https://github.com/acme/project/pull/3"}, "me", state)
        self.assertIn("````text\n## Decisions\n```\n- fake decision\n````", report)

    def test_linked_issues_includes_manually_linked_pages(self):
        calls = []
        def fake_graphql(query, variables):
            self.assertEqual(query, mod.CLOSING_ISSUES)
            cursor = variables.get("cursor")
            nodes = [{"number": 1, "repository": {"nameWithOwner": "acme/project"}}] if not cursor else [
                {"number": 2, "repository": {"nameWithOwner": "acme/project"}}]
            conn = connection(nodes, not bool(cursor), "next" if not cursor else None, 2)
            return {"repository": {"pullRequest": {"closingIssuesReferences": conn}}}
        def fake_gh(args):
            calls.append(args[-1])
            n = int(args[-1].rsplit("/", 1)[1])
            return {"html_url": f"https://github.com/acme/project/issues/{n}", "title": f"Issue {n}", "body": f"Body {n}"}
        with patch.object(mod, "graphql", side_effect=fake_graphql), patch.object(mod, "gh_json", side_effect=fake_gh):
            issues = mod.linked_issues("", "acme", "project", "github.com", 3)
        self.assertEqual([i["body"] for i in issues], ["Body 1", "Body 2"])
        self.assertEqual(len(calls), 2)


class AnalysisTests(unittest.TestCase):
    def test_local_code_windows_are_bounded_to_repository(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir()
            (repo / "x.py").write_text("".join(f"line {i}\n" for i in range(1, 41)))
            (root / "secret.txt").write_text("outside")
            threads = {"safe": {"path": "x.py", "line": 20},
                       "unsafe": {"path": "../secret.txt", "line": 1}}
            windows = mod.local_code_windows(repo, threads, set(threads))
        self.assertEqual([w["thread_id"] for w in windows], ["safe"])
        self.assertIn("20: line 20", windows[0]["text"])
        self.assertNotIn("outside", windows[0]["text"])

    def test_code_link_ranges_preserve_exact_lines(self):
        base = "https://github.com/acme/project/blob/abc/"
        links = {base + f"x.py#L{line}" for line in (2, 3, 5)}
        self.assertEqual(mod.compact_code_links(links), {
            "prefix": base, "paths": {"x.py": [[2, 3], [5, 5]]}})

    def test_rejects_non_evidence_reference(self):
        comments = {"x": {**comment("x"), "body": "Use this approach", "kind": "general"}}
        with patch.object(mod, "execute", return_value=json.dumps({"decisions": [
                {"text": "Use the new approach", "references": ["https://example.com/fake"]}],
                "thread_summaries": []})):
            with self.assertRaisesRegex(RuntimeError, "exact evidence"):
                mod.analyze(Path("."), {"pr": {}, "diff": "", "linked_issues": []}, {},
                            comments, {}, {"x"}, set(), set())

    def test_discussion_analysis_rejects_code_permalink(self):
        link = "https://github.com/acme/project/blob/abc/x.py#L4"
        with patch.object(mod, "execute", return_value=json.dumps({"decisions": [
                {"text": "Use a cache", "references": [link]}], "thread_summaries": []})):
            with self.assertRaisesRegex(RuntimeError, "exact evidence"):
                mod.analyze(Path("."), {"pr": {}, "diff": "", "linked_issues": []}, {},
                            {}, {}, set(), set(), {link}, force=True)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.data = self.root / "data"
        self.old_cwd = Path.cwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, self.old_cwd)
        self.env = patch.dict(os.environ, {"XDG_DATA_HOME": str(self.data)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.pr = {"number": 3, "title": "Fix", "body": "", "url": "https://github.com/acme/project/pull/3", "headRepository": {"nameWithOwner": "acme/project"}, "headRefOid": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
        self.meta = {"i1": {**comment("i1"), "kind": "inline", "thread_id": "t"}}
        self.threads = {"t": {"id": "t", "resolved": False, "path": "x.py", "line": 4, "comment_ids": ["i1"]}}
        self.analysis_count = 0
        self.body_requests = []
        self.codex_output_override = None
        self.viewer = "me"

    def fake_gh(self, args):
        if args[:2] == ["pr", "view"]:
            return self.pr
        raise AssertionError(args)

    def fake_run(self, args, **kwargs):
        if args[:3] == ["gh", "api", "user"]:
            return self.viewer + "\n"
        if args[:3] == ["gh", "pr", "diff"]:
            self.assertEqual(args[4:], ["--repo", "acme/project"])
            return "diff --git a/x.py b/x.py\n+++ b/x.py\n@@ -0,0 +4,1 @@\n+cache = {}\n"
        if args[:2] == ["codex", "exec"]:
            self.analysis_count += 1
            self.assertNotEqual(Path(args[args.index("-C") + 1]), self.repo)
            if self.codex_output_override is not None:
                return self.codex_output_override
            payload = json.loads(kwargs["input_text"].split("EVIDENCE JSON:\n", 1)[1])
            self.last_payload = payload
            head_repo = (self.pr.get("headRepository") or {}).get("nameWithOwner") or "acme/project"
            self.assertEqual(payload["code_permalinks"], {
                "prefix": f"https://github.com/{head_repo}/blob/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/", "paths": {"x.py": [[4, 4]]}})
            return json.dumps({"decisions": [{"text": "Keep a cache", "references": [comment("i1")["url"]]}],
                               "thread_summaries": [{"thread_id": t["id"], "summary": "Discusses the cache."}
                                                    for t in payload["threads_to_summarize"]]})
        raise AssertionError(args)

    def invoke(self, context_json=False):
        with patch.object(mod, "gh_json", side_effect=self.fake_gh), \
             patch.object(mod, "run", side_effect=self.fake_run), \
             patch.object(mod, "execute", side_effect=lambda prompt, cwd, **kwargs: self.fake_run(["codex", "exec", "-C", str(cwd)], input_text=prompt)), \
             patch.object(mod, "collect_metadata", return_value=(self.meta, self.threads)), \
             patch.object(mod, "linked_issues", return_value=[]), \
             patch.object(mod, "fetch_bodies", side_effect=self.fetch_bodies), \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            state, path = mod.scan()
            report = mod.render(state['pr'], state['viewer'], state)
            mod.atomic_write(path.with_suffix('.md'), report)
            print(json.dumps(mod.compact(state)) if context_json else report, end='')
            return output.getvalue()

    def fetch_bodies(self, ids):
        self.body_requests.append(list(ids))
        return {cid: f"Text for {cid}" for cid in ids}

    def state_path(self):
        return mod.cache_path(self.pr['url'])

    def test_compact_context_reuses_current_checkpoint(self):
        first = json.loads(self.invoke(context_json=True))
        self.assertEqual(first['head_sha'], 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
        self.assertEqual(first['threads'][0]['summary'], 'Discusses the cache.')
        self.assertEqual(len(first['decisions']), 1)
        self.assertEqual(self.analysis_count, 1)
        second = json.loads(self.invoke(context_json=True))
        self.assertEqual(second, first)
        self.assertEqual(self.analysis_count, 1)

    def test_repeat_skips_text_and_analysis_and_writes_no_repo_files(self):
        self.invoke()
        self.assertEqual(self.analysis_count, 1)
        self.assertEqual(self.body_requests, [["i1"]])
        self.assertEqual(list(self.repo.iterdir()), [])
        self.assertEqual(self.state_path().stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.state_path().with_suffix(".md").stat().st_mode & 0o777, 0o600)
        self.invoke()
        self.assertEqual(self.analysis_count, 1)
        self.assertEqual(self.body_requests, [["i1"], []])
        self.assertIn("Older unanswered threads", (self.state_path().with_suffix(".md")).read_text())
        self.assertEqual(list(self.repo.iterdir()), [])

    def test_reply_edit_and_resolution_update_report(self):
        self.invoke()
        self.meta["i2"] = {**comment("i2", "me"), "kind": "inline", "thread_id": "t"}
        self.threads["t"]["comment_ids"] = ["i1", "i2"]
        self.invoke()
        report = self.state_path().with_suffix(".md").read_text()
        self.assertIn("Unresolved threads with my comments", report)
        self.assertIn("Text for i1", report)
        self.assertIn("Text for i2", report)
        self.meta["i1"]["updatedAt"] = "2026-01-02T00:00:00Z"
        self.invoke()
        self.assertEqual(self.body_requests[-1], ["i1"])
        self.threads["t"]["resolved"] = True
        before = self.analysis_count
        self.invoke()
        self.assertEqual(self.analysis_count, before + 1)
        self.assertIn("None.", self.state_path().with_suffix(".md").read_text())

    def test_fetch_failure_preserves_checkpoint(self):
        self.invoke()
        previous = self.state_path().read_bytes()
        self.meta["i2"] = {**comment("i2"), "kind": "inline", "thread_id": "t"}
        self.threads["t"]["comment_ids"] = ["i1", "i2"]
        with patch.object(self, "fetch_bodies", side_effect=RuntimeError("incomplete")):
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                self.invoke()
        self.assertEqual(self.state_path().read_bytes(), previous)

    def test_invalid_analysis_preserves_checkpoint(self):
        self.invoke()
        previous = self.state_path().read_bytes()
        self.meta["i2"] = {**comment("i2"), "kind": "inline", "thread_id": "t"}
        self.threads["t"]["comment_ids"] = ["i1", "i2"]
        self.codex_output_override = "not JSON"
        with self.assertRaisesRegex(RuntimeError, "Model analysis invalid after one correction"):
            self.invoke()
        self.assertEqual(self.state_path().read_bytes(), previous)

    def test_report_write_failure_preserves_checkpoint(self):
        self.invoke()
        previous = json.loads(self.state_path().read_text())
        original = mod.atomic_write
        def fail_report(path, content):
            if path.suffix == ".md":
                raise RuntimeError("report write failed")
            return original(path, content)
        with patch.object(mod, "atomic_write", side_effect=fail_report):
            with self.assertRaisesRegex(RuntimeError, "report write failed"):
                self.invoke()
        current = json.loads(self.state_path().read_text())
        self.assertEqual(current['decisions'], previous['decisions'])
        self.assertEqual(current['run_number'], previous['run_number'] + 1)

    def test_rejects_data_directory_inside_repository(self):
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(self.repo / "data")}):
            with self.assertRaisesRegex(RuntimeError, "outside the code repository"):
                self.invoke()
        self.assertEqual([p.name for p in self.repo.iterdir()], ['.git'])

    def test_deleted_reply_reanalyzes_remaining_thread(self):
        self.meta["i2"] = {**comment("i2"), "kind": "inline", "thread_id": "t"}
        self.threads["t"]["comment_ids"] = ["i1", "i2"]
        self.invoke()
        del self.meta["i2"]
        self.threads["t"]["comment_ids"] = ["i1"]
        self.invoke()
        self.assertEqual(self.analysis_count, 2)
        self.assertEqual(self.last_payload["removed_comment_ids"], ["i2"])
        self.assertEqual([m["id"] for m in self.last_payload["threads_to_summarize"][0]["all_messages"]], ["i1"])

    def test_fork_code_links_use_head_repository(self):
        self.pr["headRepository"] = {"nameWithOwner": "contributor/project"}
        self.invoke()
        self.assertEqual(self.last_payload["code_permalinks"]["prefix"],
                         "https://github.com/contributor/project/blob/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/")

    def test_legacy_cache_migrates_without_code_decisions(self):
        self.invoke()
        state = json.loads(self.state_path().read_text())
        state['decisions'].append({'text': 'Code-derived choice', 'references': [
            'https://github.com/acme/project/blob/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/x.py#L4']})
        legacy = self.data / 'dk-pr-read-comments' / 'github.com' / 'acme' / 'project' / 'pr-3.json'
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps(state))
        self.state_path().unlink()
        self.invoke()
        migrated = json.loads(self.state_path().read_text())
        self.assertEqual(len(migrated['decisions']), 1)
        self.assertEqual(migrated['decisions'][0]['text'], 'Keep a cache')
        self.assertEqual(self.analysis_count, 2)

    def test_switching_account_starts_new_unanswered_baseline(self):
        self.invoke()
        self.viewer = "another"
        self.invoke()
        report = self.state_path().with_suffix(".md").read_text()
        self.assertIn("## New unanswered threads\n\n### x.py:4", report)
        self.assertEqual(self.analysis_count, 1)


if __name__ == "__main__":
    unittest.main()
