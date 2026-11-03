"""Pinned prerequisite tables and frozen commit prefixes shared by both paths."""
import re
import subprocess

HEADER = '## Dependencies\n\n| Source PR | Pinned source head | Cherry-picked commits, in order |\n| --- | --- | --- |'
SHA = r'[0-9a-f]{40,64}'


def table(rows):
    if not rows:
        return ''
    return HEADER + '\n' + '\n'.join(
        '| ' + row['url'] + ' | `' + row['head'] + '` | ' + '; '.join(
            '`' + pair['source'] + '` → `' + pair['copied'] + '`' for pair in row['commits']) + ' |'
        for row in rows)


def parse(body):
    if not re.search(r'(?m)^## Dependencies\s*$', body):
        return []
    if body.count('## Dependencies') != 1 or HEADER not in body:
        raise ValueError('Dependencies must use the fixed prerequisite table')
    section = body.split(HEADER, 1)[1].split('\n## ', 1)[0].strip()
    rows = []
    for line in section.splitlines():
        if not line.strip():
            break
        if rows and not line.startswith('|'):
            break
        match = re.fullmatch(r'\| (https://[^\s|]+/pull/[1-9][0-9]*) \| `(' + SHA + r')` \| (.+) \|', line)
        if not match:
            raise ValueError('invalid prerequisite table row')
        pairs = []
        for pair in match[3].split('; '):
            item = re.fullmatch(r'`(' + SHA + r')` → `(' + SHA + r')`', pair)
            if not item:
                raise ValueError('invalid prerequisite source/copied pair')
            pairs.append({'source': item[1], 'copied': item[2]})
        rows.append({'url': match[1], 'head': match[2], 'commits': pairs})
    if not rows:
        raise ValueError('empty Dependencies table')
    return rows


def validate_prefix(cwd, base, head, rows):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(cwd), *args], text=True).strip()
    commits = git('rev-list', '--reverse', '--first-parent', base + '..' + head).splitlines()
    pairs = [pair for row in rows for pair in row['commits']]
    copied = [pair['copied'] for pair in pairs]
    if len(set(pair['source'] for pair in pairs)) != len(pairs) or commits[:len(pairs)] != copied:
        raise ValueError('Dependencies table does not match unique leading commits')
    for row in rows:
        for pair in row['commits']:
            if subprocess.run(['git', '-C', str(cwd), 'merge-base', '--is-ancestor', pair['source'], row['head']],
                              capture_output=True).returncode:
                raise ValueError('prerequisite source is not in its pinned source head')
            message = git('show', '-s', '--format=%B', pair['copied'])
            if '(cherry picked from commit ' + pair['source'] + ')' not in message:
                raise ValueError('prerequisite commit is missing cherry-pick provenance')
            if git('show', '-s', '--format=%an <%ae> %aI', pair['source']) != git(
                    'show', '-s', '--format=%an <%ae> %aI', pair['copied']):
                raise ValueError('prerequisite authorship changed')
    return copied
