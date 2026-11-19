"""Opt-in public pricing ledger; no transcripts, native dispatch or network I/O.

Export only after the independent host reader accepts the entire closed forest.
The public verifier checks arithmetic and inventory consistency, not native
authenticity, finding quality or equivalent experimental conditions.
"""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import dollar_usage
import native_usage
import workflow_cost


FIELDS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
          'output_tokens', 'reasoning_output_tokens')


def identity(value):
    return hashlib.sha256(value.encode()).hexdigest()


def usage(vector):
    native_usage._require(isinstance(vector, list) and len(vector) == len(FIELDS),
                          'Invalid response vector')
    result = dict(zip(FIELDS, vector))
    result['total_tokens'] = result['input_tokens']+result['output_tokens']
    result = native_usage._usage(result)
    native_usage._require(result['cache_write_input_tokens'] == 0,
                          'Cache writes need independently observed TTLs')
    return result


def export(database, roots, *, source_revision, reader_revision):
    accounting = workflow_cost.collect(database, roots)
    graph = workflow_cost.host_graph(database, roots)
    native_usage._require(workflow_cost.digest(graph) == accounting['graph_sha256'],
                          'Host graph changed before ledger export')
    records = native_usage.read_records([node['rollout'] for node in graph.values()])
    header = {'type': 'scope', 'schema': 1, 'source_revision': source_revision,
              'reader_revision': reader_revision, 'rate_card': dollar_usage.RATE_CARD,
              'roots': {identity(root): bucket for root, bucket in roots.items()},
              'thread_count': accounting['thread_count'],
              'response_count': accounting['response_count'],
              'workflow_cost_usd': accounting['workflow_cost_usd'],
              'buckets': accounting['buckets']}
    rows = [header]
    for thread, result in accounting['threads'].items():
        node = graph[thread]
        native_usage._require(hashlib.sha256(Path(node['rollout']).read_bytes()).hexdigest()
                              == result['raw_rollout_sha256'], 'Raw trace changed before export')
        turns = []
        for turn in result['turns']:
            responses = [[identity(response), *[records[response]['usage'][key] for key in FIELDS]]
                         for response in turn['response_ids']]
            turns.append({'turn': identity(thread+':'+turn['turn_id']),
                          'settings': turn['settings'], 'responses': responses,
                          'inventory_sha256': workflow_cost.digest([row[0] for row in responses])})
        rows.append({'type': 'thread', 'thread': identity(thread),
                     'parent': identity(node['parent']) if node['parent'] else None,
                     'bucket': node['bucket'], 'raw_sha256': result['raw_rollout_sha256'],
                     'turns': turns})
    native_usage._require(graph == workflow_cost.host_graph(database, roots),
                          'Host graph changed during ledger export')
    verify(rows)
    return rows


def verify(rows):
    native_usage._require(bool(rows) and rows[0].get('type') == 'scope', 'Missing scope header')
    header = rows[0]
    native_usage._require(header['schema'] == 1 and header['rate_card'] == dollar_usage.RATE_CARD,
                          'Unknown schema or rate card')
    threads, responses, buckets = {}, set(), {}
    for row in rows[1:]:
        native_usage._require(row['type'] == 'thread' and row['thread'] not in threads,
                              'Duplicate or invalid thread')
        threads[row['thread']] = row
        native_usage._require(row['bucket'] in workflow_cost.BUCKETS and bool(row['turns']),
                              'Missing thread accounting')
        bucket = buckets.setdefault(row['bucket'], {'cost': Decimal(0), 'responses': 0,
                                                     'threads': 0, 'turns': 0})
        bucket['threads'] += 1
        turn_ids = set()
        for turn in row['turns']:
            settings = turn['settings']
            native_usage._require(turn['turn'] not in turn_ids and
                settings['model'] == dollar_usage.RATE_CARD['model'] and
                settings['effort'] in native_usage.EFFORTS and
                settings['service_tier'] in native_usage.TIER_MULTIPLIERS,
                'Invalid turn or actual settings')
            turn_ids.add(turn['turn'])
            native_usage._require(bool(turn['responses']) and turn['inventory_sha256'] ==
                workflow_cost.digest([response[0] for response in turn['responses']]),
                'Incomplete response inventory')
            bucket['turns'] += 1
            for response in turn['responses']:
                native_usage._require(response[0] not in responses, 'Response charged twice')
                responses.add(response[0])
                bucket['responses'] += 1
                bucket['cost'] += dollar_usage.response_cost(usage(response[1:]),
                    service_tier=settings['service_tier'])
    roots = {thread: row['bucket'] for thread, row in threads.items() if row['parent'] is None}
    native_usage._require(bool(roots) and roots == header['roots'], 'Incomplete root inventory')
    for thread, row in threads.items():
        seen, current = set(), thread
        while current is not None:
            native_usage._require(current in threads and current not in seen,
                                  'Missing or cyclic parent linkage')
            seen.add(current)
            native_usage._require(threads[current]['bucket'] == row['bucket'],
                                  'Inconsistent descendant bucket')
            current = threads[current]['parent']
    public_buckets = {key: {**value, 'cost': str(value['cost'])} for key, value in buckets.items()}
    total = sum((value['cost'] for key, value in buckets.items() if key.startswith('workflow-')),
                Decimal(0))
    native_usage._require(len(threads) == header['thread_count'] and
                          len(responses) == header['response_count'] and
                          public_buckets == header['buckets'] and
                          total == Decimal(header['workflow_cost_usd']),
                          'Ledger differs from independently collected accounting')
    return {'pricing_verified': True, 'thread_count': len(threads),
            'response_count': len(responses), 'workflow_cost_usd': str(total),
            'scope': 'Arithmetic and inventory consistency only; raw host authenticity, '
                     'quality, frozen conditions and orchestration costs need separate evidence.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    check = sub.add_parser('verify')
    check.add_argument('ledger', type=Path)
    build = sub.add_parser('export')
    build.add_argument('--database', type=Path, required=True)
    build.add_argument('--roots', type=Path, required=True)
    build.add_argument('--source-revision', required=True)
    build.add_argument('--reader-revision', required=True)
    build.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'verify':
        rows = [json.loads(line) for line in args.ledger.read_text().splitlines()]
    else:
        if args.output.exists():
            parser.error('Refusing to overwrite an existing ledger')
        rows = export(args.database, json.loads(args.roots.read_text()),
                      source_revision=args.source_revision, reader_revision=args.reader_revision)
        with args.output.open('x') as stream:
            stream.write(''.join(json.dumps(row, separators=(',', ':'))+'\n' for row in rows))
    print(json.dumps(verify(rows)))


if __name__ == '__main__':
    main()
