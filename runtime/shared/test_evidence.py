"""Conservative complete test-report readers; unsupported output is a blocker."""
import json
import re
import xml.etree.ElementTree as ET

from shared.ci_deadline import check_budget


def clean(text):
    text = text.removeprefix('\ufeff')
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    return re.sub(r'(?m)^\d{4}-\d\d-\d\dT[\d:.]+Z ?', '', text).strip()


def result(tests=None, reason=None):
    return {'complete': reason is None, 'tests': tests or [], 'reason': reason}


def case(name, outcome, signature=''):
    return {'test': name, 'outcome': outcome, 'signature': signature}


def junit(text):
    if '<!DOCTYPE' in text or '<!ENTITY' in text:
        return result(reason='unsupported XML declarations')
    try:
        tree = ET.fromstring(text)
        if tree.tag not in {'testsuite', 'testsuites'}:
            return result(reason='not JUnit')
        tests = []
        # Aggregate descendant counts once. Repeated suite.iter('testcase')
        # scans grow quadratically for nested suites and hide work from the budget.
        stack = [(tree, iter(tree), [0, 0, 0, 0])]
        while stack:
            check_budget()
            node, children, counts = stack[-1]
            child = next(children, None)
            if child is not None:
                stack.append((child, iter(child), [0, 0, 0, 0]))
                continue
            if node.tag == 'testcase':
                counts[0] += 1
                for index, tag in enumerate(('failure', 'error', 'skipped'), 1):
                    counts[index] += node.find(tag) is not None
            if node.tag == 'testsuite' or node is tree and tree.tag == 'testsuites':
                if node.tag == 'testsuite' and 'tests' not in node.attrib:
                    return result(reason='JUnit suite lacks completion count')
                if int(node.get('tests', str(counts[0]))) != counts[0]:
                    return result(reason='incomplete JUnit test count')
                for attribute, count in zip(('failures', 'errors', 'skipped'), counts[1:]):
                    if int(node.get(attribute, str(count))) != count:
                        return result(reason='incomplete JUnit result count')
            stack.pop()
            if stack:
                for index, count in enumerate(counts):
                    stack[-1][2][index] += count
        for c in tree.iter('testcase'):
            check_budget()
            if sum(c.find(tag) is not None for tag in ('failure', 'error', 'skipped')) > 1:
                return result(reason='ambiguous JUnit case result')
            name = c.attrib['name']
            prefix = c.get('classname') or c.get('file')
            name = f'{prefix}::{name}' if prefix else name
            failure = c.find('failure')
            if failure is None:
                failure = c.find('error')
            if failure is not None:
                signature = clean((failure.get('message') or '') + '\n' + ''.join(failure.itertext()))
                if not signature:
                    return result(reason='JUnit failure lacks signature')
                tests.append(case(name, 'fail', signature))
            else:
                tests.append(case(name, 'skip' if c.find('skipped') is not None else 'pass'))
        if len({t['test'] for t in tests}) != len(tests):
            return result(reason='ambiguous duplicate JUnit identity')
        return result(tests) if tests else result(reason='empty JUnit report')
    except (ET.ParseError, ValueError, KeyError):
        return result(reason='invalid or incomplete JUnit')


def pytest_output(text):
    footer = list(re.finditer(r'(?m)^=+ (.+? in [\d.]+s(?: \([^\n]*\))?) =+\s*$', text))
    if len(footer) != 1:
        return result(reason='missing or multiple pytest completion summaries')
    summary = footer[0].group(1)
    counts = {kind: int(n) for n, kind in re.findall(r'(\d+) (failed|passed|skipped|xfailed|xpassed|error|errors|deselected|warning|warnings)', summary)}
    if counts.get('error', 0) or counts.get('errors', 0):
        return result(reason='pytest setup/collection error')
    failures = re.findall(r'(?m)^FAILED (\S+) - (.+)$', text)
    if len(failures) != counts.get('failed', 0):
        return result(reason='pytest failure summary is incomplete')
    tests = [case(name, 'fail', sig) for name, sig in failures]
    # A green job or aggregate count is never named-test pass evidence.
    passed = re.findall(r'(?m)^(\S+::\S+) PASSED(?:\s+\[.*\])?\s*$', text)
    tests.extend(case(name, 'pass') for name in passed)
    if len(passed) > counts.get('passed', 0):
        return result(reason='pytest pass count mismatch')
    trailing = text[footer[0].end():].strip()
    if trailing and not re.fullmatch(r'##\[error\]Process completed with exit code [01]\.\s*', trailing):
        return result(reason='output after pytest completion')
    if any('...' in sig or '…' in sig for _, sig in failures):
        return result(reason='truncated pytest failure signature')
    return result(tests)


def go_output(text):
    packages, terminal, named = set(), {}, {}
    try:
        for line in text.splitlines():
            check_budget()
            if not line.strip():
                continue
            event = json.loads(line)
            if (not isinstance(event, dict)
                    or not isinstance(event.get('Action'), str) or not event['Action']
                    or not isinstance(event.get('Package'), str) or not event['Package']
                    or not isinstance(event.get('Test', ''), str)
                    or not isinstance(event.get('Output', ''), str)):
                return result(reason='invalid Go events')
            package = event['Package']
            packages.add(package)
            if event.get('Test'):
                named.setdefault((package, event['Test']), []).append(event)
            elif event['Action'] in {'pass', 'fail', 'skip'}:
                terminal[package] = event['Action']
    except ValueError:
        return result(reason='Go output is not a complete JSON stream')
    if not packages:
        return result(reason='invalid Go events')
    tests, package_failures = [], set()
    for package, name in sorted(named):
        check_budget()
        relevant = named[(package, name)]
        if relevant[-1]['Action'] not in {'pass', 'fail', 'skip'}:
            return result(reason='incomplete Go test events')
        outcome = relevant[-1]['Action']
        signature = '\n'.join(e.get('Output', '').strip() for e in relevant if e['Action'] == 'output'
                              and not re.match(r'\s*(?:=== RUN|=== PAUSE|=== CONT|--- (?:FAIL|PASS|SKIP))', e.get('Output', ''))).strip()
        if outcome == 'fail':
            package_failures.add(package)
            if not signature:
                return result(reason='Go failure lacks signature')
        tests.append(case(package + '::' + name, outcome, signature if outcome == 'fail' else ''))
    if set(terminal) != packages or {p for p, s in terminal.items() if s == 'fail'} != package_failures:
        return result(reason='Go build/package failure or missing terminal event')
    return result(tests)


def jest_output(text):
    try:
        report = json.loads(text)
        suites = report['testResults']
        tests = []
        for suite in suites:
            check_budget()
            assertions = suite['assertionResults']
            if not assertions or suite.get('testExecError'):
                return result(reason='Jest suite execution error')
            for assertion in assertions:
                check_budget()
                status = assertion['status']
                outcome = {'passed': 'pass', 'failed': 'fail', 'pending': 'skip', 'todo': 'skip'}.get(status)
                if not outcome:
                    return result(reason='incomplete Jest assertion')
                signature = clean('\n'.join(assertion.get('failureMessages', [])))
                if outcome == 'fail' and not signature:
                    return result(reason='Jest failure lacks signature')
                tests.append(case(suite['name'] + '::' + assertion['fullName'], outcome, signature))
        if (len(suites) != report['numTotalTestSuites'] or len(tests) != report['numTotalTests']
                or sum(t['outcome'] == 'fail' for t in tests) != report['numFailedTests']
                or sum(t['outcome'] == 'pass' for t in tests) != report['numPassedTests']
                or report.get('wasInterrupted') is not False
                or report.get('numRuntimeErrorTestSuites', 0)):
            return result(reason='incomplete Jest totals')
        return result(tests)
    except (ValueError, KeyError, TypeError):
        return result(reason='invalid Jest JSON report')


def jest_text(text):
    """Complete Jest default reporter: named failures, no inferred named passes."""
    suite_summary = re.search(r'(?m)^Test Suites:\s*(.+)$', text)
    test_summary = re.search(r'(?m)^Tests:\s*(.+)$', text)
    end = re.search(r'(?m)^Ran all test suites(?: matching .+)?\.\s*$', text)
    if not suite_summary or not test_summary or not end or text[end.end():].strip():
        return result(reason='incomplete Jest reporter summary')
    def counts(summary):
        return {kind: int(n) for n, kind in re.findall(r'(\d+) (failed|passed|skipped|todo|total)', summary)}
    suites, totals = counts(suite_summary.group(1)), counts(test_summary.group(1))
    if any(c.get('total') != sum(c.get(k, 0) for k in ('failed', 'passed', 'skipped', 'todo'))
           for c in (suites, totals)):
        return result(reason='inconsistent Jest reporter counts')
    sections = list(re.finditer(r'(?m)^(FAIL|PASS)\s+(\S+)(?:\s+\([^\n]*\))?\s*$', text[:suite_summary.start()]))
    tests = []
    for index, section in enumerate(sections):
        check_budget()
        section_end = sections[index + 1].start() if index + 1 < len(sections) else suite_summary.start()
        content = text[section.end():section_end]
        failures = list(re.finditer(r'(?m)^\s*● (.+)$', content))
        for i, failure in enumerate(failures):
            check_budget()
            stop = failures[i + 1].start() if i + 1 < len(failures) else len(content)
            message = content[failure.end():stop].strip()
            if not message or failure.group(1) == 'Test suite failed to run':
                return result(reason='Jest suite error or missing failure detail')
            name = section.group(2) + '::' + failure.group(1).replace(' › ', ' ')
            tests.append(case(name, 'fail', message))
    if (not sections or len(sections) != suites.get('total') or len(tests) != totals.get('failed', 0)
            or sum(s.group(1) == 'FAIL' for s in sections) != suites.get('failed', 0)
            or any(s.group(1) == 'FAIL' and not any(t['test'].startswith(s.group(2) + '::') for t in tests)
                   for s in sections)):
        return result(reason='Jest failed suite/assertion coverage is incomplete')
    return result(tests)


def parse_report(text):
    check_budget()
    text = clean(text)
    text = re.sub(r'(?:^|\n)##\[error\]Process completed with exit code [01]\.\s*$', '', text).strip()
    if not text:
        return result(reason='missing logs/report')
    if text.startswith('<'):
        return junit(text)
    if text.startswith('{'):
        try:
            candidate = json.loads(text)
        except ValueError:
            candidate = None
        if isinstance(candidate, dict) and 'testResults' in candidate:
            return jest_output(text)
        try:
            first = json.loads(text.splitlines()[0])
        except ValueError:
            first = None
        if isinstance(first, dict) and 'Action' in first and 'Package' in first:
            return go_output(text)
        return result(reason='unsupported JSON test report')
    # Errors outside a recognized test result cannot disappear behind a summary.
    if re.search(r'(?im)^(?:npm (?:ERR!|error)|error:|fatal:|Error:|##\[error\](?!Process completed with exit code))', text):
        return result(reason='non-test error in step output')
    if re.search(r'(?m)^Test Suites:', text):
        return jest_text(text)
    if re.search(r'(?m)^(?:=+ test session starts =+|=+ .+ in [\d.]+s(?: \([^\n]*\))? =+|'
                 r'FAILED \S+::\S+ - |\S+::\S+ (?:PASSED|FAILED|SKIPPED)\b)', text):
        return pytest_output(text)
    return result(reason='unsupported test report format')
