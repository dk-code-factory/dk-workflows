from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from reviewer_prompts import source_files


class ReviewerSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_plugin_root_reference_finds_existing_procedure(self):
        (self.root / '.claude-plugin').mkdir()
        skill = self.write('skills/review/SKILL.md',
                           '[Review](${CLAUDE_PLUGIN_ROOT}/skills/review/review.md)')
        procedure = self.write('skills/review/review.md', 'Review read-only.')

        sources, missing = source_files([skill])

        self.assertEqual(missing, [])
        self.assertIn(str(procedure), [source['path'] for source in sources])

    def test_repository_guidance_examples_are_not_missing_procedures(self):
        skill = self.write('SKILL.md',
                           'Find repository `AGENTS.md`, `CONTRIBUTING.md`, '
                           'and `CODING_STANDARDS.md`.')

        sources, missing = source_files([skill])

        self.assertEqual(missing, [])
        self.assertEqual([source['path'] for source in sources], [str(skill)])

    def test_existing_local_guidance_remains_part_of_sources(self):
        skill = self.write('SKILL.md', 'Read `CONTRIBUTING.md`.')
        guidance = self.write('CONTRIBUTING.md', 'Review local rules.')

        sources, missing = source_files([skill])

        self.assertEqual(missing, [])
        self.assertIn(str(guidance), [source['path'] for source in sources])

    def test_explicit_missing_guidance_link_still_blocks_discovery(self):
        skill = self.write('SKILL.md', '[Required rules](CONTRIBUTING.md)')

        _, missing = source_files([skill])

        self.assertEqual(missing, [str(self.root / 'CONTRIBUTING.md')])

    def test_missing_plugin_procedure_still_blocks_discovery(self):
        (self.root / '.claude-plugin').mkdir()
        skill = self.write('skills/review/SKILL.md',
                           '[Required](${CLAUDE_PLUGIN_ROOT}/skills/review/missing.md)')

        _, missing = source_files([skill])

        self.assertEqual(missing, [str(self.root / 'skills/review/missing.md')])


if __name__ == '__main__':
    unittest.main()


def test_absent_optional_home_guidance_is_not_a_missing_procedure(tmp_path):
    skill=tmp_path/'SKILL.md';skill.write_text('When present, read `~/.claude/CLAUDE.md` and `README.md`.')
    _,missing=source_files([skill])
    assert missing==[]
