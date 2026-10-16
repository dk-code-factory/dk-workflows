"""Prompt discovery and the prepared instruction shared by reviewer adapters."""
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import hashlib
from pathlib import Path
import re


def preparation_instructions():
    return (
        ' Read preparation.analysis and reviewer_addendum in the handoff. Apply identical shared '
        'boundaries and Decisions to your assigned role; forward both fields and these instructions '
        'unchanged to every nested reviewer, including correction retries. Check actionable correctness '
        'cases, performance workloads/limits, supported compatibility, settled/open placement, and '
        'required test evidence against preparation. Existing coverage may already prove the boundary; '
        'a missing integration test qualifies only when a concrete required interaction remains unproved. '
        'Distinguish defect validity from excluded or decision-blocked remediation. Flag remedies beyond '
        'permitted depth/complexity without inventing architectural obligations. Keep your original role, '
        'finding threshold, read-only restriction and JSON output contract. The fix field states the '
        'required outcome. If material instructions or evidence are unavailable, report incomplete.')


def source_files(initial):
    """Follow accessible local Markdown procedures; retain unresolved references as blockers."""
    pending = [Path(p).resolve() for p in initial]
    seen, missing = {}, []
    while pending:
        path = pending.pop()
        if str(path) in seen:
            continue
        if not path.is_file():
            missing.append(str(path))
            continue
        seen[str(path)] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        if path.suffix.lower() != '.md':
            continue
        content = path.read_text()
        # Markdown references, backticked procedures, and Claude @file imports.
        refs = re.findall(r'\]\(([^)]+)\)', content)
        # Bare paths include optional user guidance and repository search targets.
        # Follow existing files; a required unavailable procedure must be an
        # explicit link/import, which remains a blocker below.
        refs += [ref for ref in re.findall(r'`([^`\n]+\.md)`', content)
                 if (Path(ref).expanduser() if Path(ref).expanduser().is_absolute()
                     else path.parent / ref).is_file()]
        refs += re.findall(r'(?m)(?:^|\s)@([^\s]+\.md)', content)
        for ref in refs:
            ref = ref.strip('<>').split('#', 1)[0]
            if not ref or '://' in ref or not ref.endswith('.md'):
                continue
            if ref.startswith('${CLAUDE_PLUGIN_ROOT}/'):
                plugin_root = next((parent for parent in path.parents
                                    if (parent / '.claude-plugin').is_dir()), None)
                if plugin_root is not None:
                    ref = str(plugin_root / ref[len('${CLAUDE_PLUGIN_ROOT}/'):])
            candidate = Path(ref).expanduser()
            if not candidate.is_absolute():
                candidate = path.parent / candidate
            candidate = candidate.resolve()
            if str(candidate) not in seen and str(candidate) not in missing:
                pending.append(candidate)
        # Claude plugin agents/procedures are dispatched by name rather than linked.
        if path.parent.name in {'commands', 'skills'}:
            plugin = path.parent.parent
            for folder in ('agents', 'skills'):
                if (plugin / folder).is_dir():
                    pending.extend(p for p in (plugin / folder).rglob('*.md') if str(p.resolve()) not in seen)
    return sorted(seen.values(), key=lambda item: item['path']), sorted(set(missing))
