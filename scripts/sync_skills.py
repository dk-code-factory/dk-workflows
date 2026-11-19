#!/usr/bin/env python3
"""Copy maintained skill prompts, defaults and schemas into Claude's distribution."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    for directory in sorted((ROOT/'codex/skills').iterdir()):
        if not directory.is_dir():
            continue
        target_prompts = ROOT/'claude/skills'/directory.name/'prompts'
        if target_prompts.exists():
            for previous in target_prompts.glob('*.md'):
                if not (directory/'prompts'/previous.name).is_file():
                    previous.unlink()
        for name in ['SKILL.md', 'config.json', 'config.schema.json',
                     *[str(path.relative_to(directory)) for path in sorted((directory/'prompts').glob('*.md'))]]:
            source = directory/name
            if source.is_file():
                target = ROOT/'claude/skills'/directory.name/name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())


if __name__ == '__main__':
    main()
