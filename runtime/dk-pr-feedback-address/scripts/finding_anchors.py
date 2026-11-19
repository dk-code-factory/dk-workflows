"""Resolve supported finding identities and report anchors from original sources.

No finding registry, model calls or writes. Full revisions stay in transient
calculations; only finding-specific provenance enters follow-up inspection.
"""
from __future__ import annotations

from datetime import datetime
import html
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

import fetch_pr_conversation as conversation

SHA = r'[0-9a-f]{40}'
UUID = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
FINDING = re.compile(r'https://[\w.-]+\.qodo\.ai/findings/(' + UUID + r')\b')
EDIT_FIELDS = '''id body createdAt lastEditedAt includesCreatedEdit
  userContentEdits(first:100,after:$cursor) {
    pageInfo { hasNextPage endCursor } totalCount
    nodes { id editedAt deletedAt diff }
  }'''
CODE = re.compile(
    r'^[ \t]{0,3}(?:>[ \t]?)?(?P<fence>`{3,}|~{3,})[^\n]*\n.*?'
    r'^[ \t]{0,3}(?:>[ \t]?)?(?P=fence)[ \t]*(?=\n|$)'
    r'''|<(?P<raw>pre|code|script|style|textarea|xmp)\b(?:[^"'<>]|"[^"]*"|'[^']*')*>.*?</(?P=raw)>'''
    r'|(?<!`)(?P<ticks>`+)(?!`).*?(?<!`)(?P=ticks)(?!`)'
    r'|^(?: {4}| *\t)[^\n]*(?:\n|$)', re.I | re.M | re.S)
BADGES = re.compile(r'\s*<code>\s*(?:🐞 Bug|🔗 Cross-repo conflict)\s*</code>'
                    r'(?:\s*<code>\s*(?:⭐ New|👈 Shift left)\s*</code>)?\s*\Z')
UNCERTAIN_CODE = re.compile(
    r'[`~\t\\]|</?(?:pre|code|script|style|textarea|xmp|plaintext)\b'
    r'|^[ \t]{0,3}(?:>[ \t]?)+(?:[ \t]{2,}|\t)'
    r'|^[ \t]*(?:[-+*]|\d+[.)])(?:[ \t]{2,}|\t)', re.I | re.M)


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('missing source timestamp')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('source timestamp needs timezone')
    return result


def canonical(body, *, numbered=False):
    """Only supported presentation changes; never infer semantic equivalence."""
    # Code literals and indentation are substantive. Keep typed fragments so
    # prose cannot impersonate a placeholder for protected code. Leftover code
    # delimiters, quotes, tabs and indented quote/list content stay verbatim too.
    if numbered:
        body = re.sub(r'\A(<details\b[^>]*>\s*<summary\b[^>]*>)\s*\d+[.)]\s*', r'\1', body, flags=re.I)
        headings = [(closing, begin, end) for tag, closing, begin, end in markup(body)[0] if tag == 'summary']
        if len(headings) < 2 or headings[0][0] or not headings[1][0]:
            raise ValueError('ambiguous finding summary')
        begin, end = headings[0][2], headings[1][1]
        badges = BADGES.search(body[begin:end])
        if badges:
            body = body[:begin + badges.start()] + body[end:]
    outside = html.unescape(CODE.sub('', body))
    if UNCERTAIN_CODE.search(outside) or any(char in outside for char in ("'", '"', '\\')):
        return (('verbatim', body),)
    result, start = [], 0

    def prose(text):
        text = re.sub(r'</?(?:details|summary|br|hr|b|i|strong|ins)\b[^>]*>', '', text, flags=re.I)
        text = re.sub(r'(?m)^[ \t]*>[ \t]?', '', text)
        text = re.sub(r'(?<!\*)\*\*([^\s*](?:[^*\n]*?[^\s*])?)\*\*(?!\*)', r'\1', text)
        text = ' '.join(html.unescape(text).split())
        if text:
            if result and result[-1][0] == 'prose':
                result[-1] = ('prose', result[-1][1] + ' ' + text)
            else:
                result.append(('prose', text))

    for match in CODE.finditer(body):
        prose(body[start:match.start()])
        result.append(('code', match[0]))
        start = match.end()
    prose(body[start:])
    return tuple(result)


def markup(body):
    """Read HTML structure with recognized code masked at its original offsets."""
    masked = CODE.sub(lambda match: re.sub(r'[^\n]', ' ', match[0]), body)
    offsets = [0] + [match.end() for match in re.finditer('\n', body)]
    tokens, comments, blocked = [], set(), []

    class Parser(HTMLParser):
        def position(self):
            line, column = self.getpos()
            return offsets[line - 1] + column

        def handle_starttag(self, tag, attrs):
            if tag in {'blockquote', 'pre', 'code', 'script', 'style', 'textarea', 'xmp', 'plaintext'}:
                blocked.append(tag)
            if tag in {'details', 'summary'} and not blocked:
                start = self.position()
                tokens.append((tag, False, start, start + len(self.get_starttag_text())))

        def handle_endtag(self, tag):
            if blocked:
                if tag == blocked[-1]:
                    blocked.pop()
                return
            if tag in {'details', 'summary'}:
                start = self.position()
                closing = re.match(r'</' + tag + r'\s*>', body[start:], re.I)
                if closing is None:
                    raise ValueError('unsupported structure closing tag')
                tokens.append((tag, True, start, start + closing.end()))

        def handle_startendtag(self, tag, attrs):
            if tag in {'details', 'summary', 'blockquote', 'pre', 'code',
                       'script', 'style', 'textarea', 'xmp', 'plaintext'}:
                raise ValueError('unsupported self-closing structure')

        def handle_comment(self, data):
            if not body.startswith('<!--', self.position()):
                raise ValueError('unsupported HTML declaration')
            if not blocked:
                comments.add(self.position())

        def handle_decl(self, data):
            raise ValueError('unsupported HTML declaration')

        def unknown_decl(self, data):
            self.handle_decl(data)

    parser = Parser(convert_charrefs=False)
    try:
        parser.feed(masked)
        parser.close()
    except AssertionError as error:
        raise ValueError('unsupported HTML structure') from error
    return tokens, comments


def qodo_findings(body):
    """Recognize numbered Qodo details blocks containing one stable UUID."""
    if not re.search(r'Code Review by Qodo', body):
        return {}
    if UNCERTAIN_CODE.search(html.unescape(CODE.sub('', body))):
        raise ValueError('unsupported code markup in Qodo report')
    stack, blocks = [], []
    for tag, closing, begin, end in markup(body)[0]:
        if tag != 'details':
            continue
        if closing:
            if not stack:
                raise ValueError('unbalanced Qodo details')
            start = stack.pop()
            block = body[start:end]
            if re.match(r'<details\b[^>]*>\s*<summary\b[^>]*>\s*\d+[.)]', block, re.I):
                ids = set(FINDING.findall(block))
                if len(ids) != 1:
                    raise ValueError('ambiguous Qodo finding block')
                blocks.append((next(iter(ids)), canonical(block, numbered=True)))
        else:
            stack.append(begin)
    if stack or len({key for key, _ in blocks}) != len(blocks):
        raise ValueError('incomplete or duplicate Qodo finding blocks')
    if {key for key, _ in blocks} != set(FINDING.findall(body)):
        raise ValueError('unsupported Qodo finding layout')
    return {'qodo:' + key: text for key, text in blocks}


def revisions(node, text):
    """Validate a complete original-to-current chain; deleted edits are unknown."""
    if not isinstance(node, dict) or node.get('body') != text or not node.get('id'):
        raise ValueError('source history unavailable or changed')
    created = timestamp(node.get('createdAt'))
    edits = node.get('edits')
    if node.get('lastEditedAt') is None and node.get('includesCreatedEdit') is False and edits == []:
        return [{'id': node['id'], 'editedAt': node['createdAt'], 'diff': text}]
    if node.get('includesCreatedEdit') is not True or not edits:
        raise ValueError('original revision unavailable')
    if any(not isinstance(edit, dict) or not edit.get('id') or edit.get('deletedAt')
           or not isinstance(edit.get('diff'), str) for edit in edits):
        raise ValueError('missing or deleted revision')
    result = sorted(edits, key=lambda edit: timestamp(edit.get('editedAt')))
    times = [timestamp(edit['editedAt']) for edit in result]
    if (len(set(times)) != len(times) or len({edit['id'] for edit in result}) != len(result)
            or times[0] != created or times[-1] != timestamp(node.get('lastEditedAt'))
            or result[-1]['diff'] != text):
        raise ValueError('incomplete or ambiguous revision history')
    return result


def report_head(pr, revision, pushes):
    """A Qodo footer is report metadata only with independently proved arrival."""
    parsed = urlsplit(pr)
    owner, repo, _, _ = parsed.path.strip('/').split('/')
    pattern = (r'<!--\s*(https://' + re.escape(parsed.netloc) + '/' + re.escape(owner + '/' + repo)
               + r'/commit/(' + SHA + r'))\s*-->')
    body = revision['diff']
    candidates = set(re.findall(pattern, body))
    if len(candidates) != 1:
        raise ValueError('missing or ambiguous report footer')
    url, anchor = next(iter(candidates))
    footer = re.search(r'(?m)^[ ]{0,3}' + pattern
                       + r'[ \t]*(?:\n\s*\*Powered by \[Qodo\]\(https://www\.qodo\.ai/?\)\*)?\s*\Z', body)
    if (footer is None or body.index('<!--', footer.start()) not in markup(body)[1]
            or re.search(r'`|~|</?(?:pre|code)\b', CODE.sub('', body[:footer.start()]), re.I)):
        raise ValueError('report footer is absent or inside unsupported code markup')
    arrived = [event for event in pushes if (event.get('afterCommit') or {}).get('oid') == anchor
               and timestamp(event.get('createdAt')) <= timestamp(revision['editedAt'])]
    if not arrived:
        raise ValueError('report head arrival unproved')
    event = max(arrived, key=lambda item: timestamp(item['createdAt']))
    return anchor, {'reportMetadata': url, 'push': event['id']}


def resolve(source, text, node, pushes):
    """Return identities even when their source history cannot establish age."""
    try:
        current = qodo_findings(text)
    except ValueError:
        current = {'qodo:' + key: '' for key in set(FINDING.findall(text))}
    identities = current or {source['reference']: canonical(text)}
    result = {key: {'findingId': key, 'source': source['reference'], 'sourceId': source['id'],
                    'status': 'unknown', 'anchor': None, 'revision': None,
                    'reason': 'unsupported source or unavailable history'} for key in identities}
    try:
        chain = revisions(node, text)
        if node['id'] != source.get('nodeId'):
            raise ValueError('source node identity disagrees')
        if source.get('kind') != 'review' and timestamp(node['createdAt']) != timestamp(source.get('createdAt')):
            raise ValueError('source creation evidence disagrees')
        parsed = [qodo_findings(edit['diff']) for edit in chain] if current else None
        for key, record in result.items():
            try:
                if parsed is not None:
                    seen = [index for index, findings in enumerate(parsed) if key in findings]
                    if (not seen or seen != list(range(seen[0], len(chain)))
                            or any(parsed[index][key] != current[key] for index in seen)):
                        raise ValueError('finding changed or disappeared; substantive request ambiguous')
                    revision = chain[seen[0]]
                    if source['kind'] == 'issue_comment':
                        anchor, proof = report_head(source['pr'], revision, pushes)
                        reason = 'stable bot ID first introduced in complete revision history'
                    else:
                        if seen[0] != 0:
                            raise ValueError('edited finding has no immutable review commit binding')
                        anchor, proof = review_head(source), {}
                        reason = 'stable bot ID present in original associated review'
                else:
                    if any(canonical(edit['diff']) != identities[key] for edit in chain):
                        raise ValueError('edited unstructured finding identity is ambiguous')
                    revision, proof = chain[0], {}
                    anchor = review_head(source)
                    reason = 'original review identity and associated commit'
                record.update(status='resolved', anchor=anchor, revision=revision['id'],
                              requestedAt=revision['editedAt'], reason=reason, **proof)
            except ValueError as error:
                record['reason'] = str(error)
    except ValueError as error:
        for record in result.values():
            record['reason'] = str(error)
    return result


def review_head(source):
    anchor = source.get('reviewCommit')
    if source['kind'] not in {'review', 'inline_comment'} or not re.fullmatch(SHA, anchor or ''):
        raise ValueError('unsupported report metadata')
    if source['kind'] == 'inline_comment' and not source.get('originalInline'):
        raise ValueError('inline reply has no immutable report-head binding')
    return anchor


def graphql(query, variables, hostname, cwd):
    return conversation.gh_json(['api', '--hostname', hostname, 'graphql', '-f', 'query=' + query,
                                 *[part for key, value in variables.items() if value is not None
                                   for part in ('-F', key + '=' + str(value))]], cwd)


def connection(fetch, name):
    """Consume every page with stable totals and advancing cursors."""
    result, cursor, count, visited = [], None, None, set()
    while True:
        value = fetch(cursor)
        page = value[name]
        if type(page.get('totalCount')) is not int or page['totalCount'] < 0:
            raise ValueError('missing history count')
        if count is not None and page['totalCount'] != count:
            raise ValueError('history changed during pagination')
        count = page['totalCount']
        result.extend(page['nodes'])
        info = page['pageInfo']
        if info.get('hasNextPage') is False:
            break
        cursor = info.get('endCursor')
        if info.get('hasNextPage') is not True or not cursor or cursor in visited:
            raise ValueError('incomplete history pagination')
        visited.add(cursor)
    if any(not isinstance(item, dict) or not item.get('id') for item in result):
        raise ValueError('unavailable history node')
    if len({item['id'] for item in result}) != len(result):
        raise ValueError('duplicate history node')
    # GitHub timeline totalCount is unfiltered even with itemTypes. Edit totals
    # are exact and must include the original revision when advertised.
    if name == 'userContentEdits' and len(result) != count:
        raise ValueError('missing revision page')
    return value, result


def capture_node(source, text, hostname, cwd):
    query = ('query($id:ID!,$cursor:String) { node(id:$id) { '
             + ' '.join('... on ' + kind + ' { ' + EDIT_FIELDS + ' }'
                        for kind in ('IssueComment', 'PullRequestReview', 'PullRequestReviewComment')) + ' } }')
    prior = None

    def fetch(cursor):
        nonlocal prior
        response = graphql(query, {'id': source['nodeId'], 'cursor': cursor}, hostname, cwd)
        if response.get('errors'):
            raise ValueError('source edit history unavailable')
        node = response['data']['node']
        if not node or node.get('body') != text:
            raise ValueError('source changed during history capture')
        metadata = {key: value for key, value in node.items() if key != 'userContentEdits'}
        if prior is not None and prior != metadata:
            raise ValueError('source changed during revision pagination')
        prior = metadata
        return node

    node, edits = connection(fetch, 'userContentEdits')
    return {**{key: value for key, value in node.items() if key != 'userContentEdits'}, 'edits': edits}


def capture_pushes(pr, head, cwd):
    parsed = urlsplit(pr)
    owner, repo, _, number = parsed.path.strip('/').split('/')
    query = '''query($owner:String!,$repo:String!,$number:Int!,$cursor:String) {
      repository(owner:$owner,name:$repo) { pullRequest(number:$number) {
        headRefOid timelineItems(first:100,after:$cursor,itemTypes:[HEAD_REF_FORCE_PUSHED_EVENT]) {
          totalCount pageInfo { hasNextPage endCursor }
          nodes { ... on HeadRefForcePushedEvent { id createdAt beforeCommit { oid } afterCommit { oid } } }
        }
      } }
    }'''

    def fetch(cursor):
        response = graphql(query, {'owner': owner, 'repo': repo, 'number': number, 'cursor': cursor}, parsed.hostname, cwd)
        if response.get('errors'):
            raise ValueError('PR push history unavailable')
        value = response['data']['repository']['pullRequest']
        if value['headRefOid'] != head:
            raise ValueError('PR head changed during push capture')
        return value

    return connection(fetch, 'timelineItems')[1]


def collect(inline, top, sources, cwd):
    """Target existing sources; stable double reads avoid a new durable cache."""
    texts = {item['reference']: item['text'] for item in top['items']}
    texts.update({item['reference']: item['text'] for thread in inline['threads'] for item in thread['comments']})
    pr = inline['pullRequest']['url']
    head = inline['pullRequest']['headRefOid']
    hostname = urlsplit(pr).hostname
    nodes, pushes = {}, []
    for reference, source in sources.items():
        if (not source.get('nodeId') or source['kind'] == 'issue_comment'
                and not FINDING.search(texts[reference])):
            continue
        try:
            first = capture_node(source, texts[reference], hostname, cwd)
            second = capture_node(source, texts[reference], hostname, cwd)
            if first != second:
                raise ValueError('source history changed during capture')
            nodes[reference] = first
        except (ValueError, KeyError, TypeError, conversation.FetchError):
            # Lack of visibility must preserve the authorized unknown-age route.
            nodes[reference] = None
    if any(FINDING.search(text) for text in texts.values()):
        try:
            first = capture_pushes(pr, head, cwd)
            second = capture_pushes(pr, head, cwd)
            if first == second:
                pushes = first
        except (ValueError, KeyError, TypeError, conversation.FetchError):
            pass
    return {reference: resolve({**source, 'pr': pr}, texts[reference], nodes.get(reference), pushes)
            for reference, source in sources.items()}
