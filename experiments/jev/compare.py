"""Fixed 16-case paired pilot. No fallback, retries, extraction, or prose generation."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import statistics
import time
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError

import run
import pipeline

BUDGET_USD = 0.50
SONNET_MODEL = 'claude-sonnet-5-5'
MAX_OUTPUT = 1024
PRICES = {'jev': (0.042, 0), 'sonnet': (2, 10)}


def cost(provider, usage):
    if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0
                                          for k in ('input_tokens', 'output_tokens')):
        raise ValueError('missing_usage_stop')
    if usage.get('cache_creation_input_tokens', 0) or usage.get('cache_read_input_tokens', 0):
        raise ValueError('unexpected_cache_pricing')
    incoming, outgoing = PRICES[provider]
    return (usage['input_tokens'] * incoming + usage['output_tokens'] * outgoing) / 1_000_000


def sonnet_request(jev_payload):
    question = jev_payload['questions']['decision']
    return {'model': SONNET_MODEL, 'max_tokens': MAX_OUTPUT,
            'system': json.dumps(question['instructions'], ensure_ascii=False),
            'messages': [{'role': 'user', 'content': json.dumps({
                'state': jev_payload['state'], 'criteria': question['criteria']}, ensure_ascii=False)}],
            'output_config': {'format': {'type': 'json_schema', 'schema': pipeline.object_schema({
                'decision': {'type': 'string', 'enum': list(run.OPTIONS)}})}}}


def reserve(provider, payload):
    # Conservative byte-based input allowance plus envelope/schema overhead, not a billing quote.
    input_allowance = len(json.dumps(payload, ensure_ascii=False).encode()) + 4096
    return cost(provider, {'input_tokens': input_allowance,
                          'output_tokens': MAX_OUTPUT if provider == 'sonnet' else 0})


def sonnet_call(payload):
    req = Request(pipeline.SONNET_ENDPOINT, data=json.dumps(payload, ensure_ascii=False).encode(),
                  headers={'x-api-key': os.environ['ANTHROPIC_API_KEY'],
                           'anthropic-version': '2023-06-01', 'Content-Type': 'application/json'})
    try:
        with build_opener(run.NoRedirect()).open(req, timeout=60) as response:
            return json.load(response)
    except (HTTPError, URLError, OSError, ValueError):
        raise RuntimeError('sonnet_request_failed') from None


def parse_sonnet(raw):
    if raw.get('model') != SONNET_MODEL or raw.get('stop_reason') != 'end_turn':
        raise ValueError('incomplete_or_wrong_model')
    content = raw.get('content')
    if not isinstance(content, list) or any(not isinstance(b, dict) or b.get('type') not in
                                           ('text', 'thinking', 'redacted_thinking') for b in content):
        raise ValueError('invalid_content')
    texts = [b['text'] for b in content if b['type'] == 'text']
    if len(texts) != 1:
        raise ValueError('invalid_text_count')
    result = json.loads(texts[0])
    pipeline.exact_map(result, ['decision'])
    if not isinstance(result['decision'], str) or result['decision'] not in run.OPTIONS:
        raise ValueError('invalid_decision')
    return result


def summary(records):
    report = {}
    for provider in PRICES:
        rows = [r for r in records if r['provider'] == provider]
        valid = [r for r in rows if r['status'] == 'ok']
        ask = [r for r in valid if r['expected'] == 'ask_user']
        other = [r for r in valid if r['expected'] != 'ask_user']
        report[provider] = {
            'attempted': len(rows), 'valid': len(valid), 'errors': len(rows)-len(valid),
            'correct': sum(r['decision'] == r['expected'] for r in valid),
            'required_questions_found': sum(r['decision'] == 'ask_user' for r in ask),
            'required_questions_total_valid': len(ask),
            'unnecessary_questions': sum(r['decision'] == 'ask_user' for r in other),
            'non_question_total_valid': len(other),
            'median_latency_ms': statistics.median(r['latency_ms'] for r in valid) if valid else None,
            'cost_usd': sum(r['cost_usd'] for r in rows) if all('cost_usd' in r for r in rows) else None,
            'input_tokens': sum(r.get('usage', {}).get('input_tokens', 0) for r in rows),
            'output_tokens': sum(r.get('usage', {}).get('output_tokens', 0) for r in rows),
            'confusion': {label: dict(Counter(r['decision'] for r in valid if r['expected'] == label))
                          for label in run.OPTIONS}}
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    folder = run.ROOT / 'evaluation'
    manifest = json.loads((folder / 'review-manifest.json').read_text())
    source = (folder / 'cases.proposed.json').read_bytes()
    if hashlib.sha256(source).hexdigest() != manifest['candidate_sha256']:
        parser.error('candidate_changed')
    by_id = {c['id']: c for c in json.loads(source)}
    cases = [by_id[i] for i in manifest['pilot_case_ids']]
    if len(cases) != 16 or Counter(c['expected'] for c in cases) != dict.fromkeys(run.OPTIONS, 4):
        parser.error('pilot_must_have_four_per_label')
    if any(c['split'] != 'dev' for c in cases):
        parser.error('pilot_must_use_dev')
    policy = (run.ROOT / 'policy.md').read_text()
    if args.live and (os.environ.get('SONNET_MODEL') != SONNET_MODEL or
                      not all(os.environ.get(k) for k in ('TYPESAFE_API_KEY', 'ANTHROPIC_API_KEY'))):
        parser.error('expected_keys_and_sonnet_model_required')
    args.out.mkdir(parents=True, exist_ok=False)
    records, spent, stop = [], 0., None
    with (args.out / 'calls.jsonl').open('x') as output:
        def write(record):
            output.write(json.dumps(record, ensure_ascii=False, allow_nan=False)+'\n')
            output.flush()
        write({'type': 'run', 'mode': 'live' if args.live else 'dry_run', 'budget_usd': BUDGET_USD,
               'prices_per_million': PRICES, 'sonnet_max_output': MAX_OUTPUT,
               'sonnet_thinking': 'API default, recorded in raw response',
               'policy_sha256': hashlib.sha256(policy.encode()).hexdigest(),
               'code_sha256': hashlib.sha256(Path(__file__).read_bytes() + (run.ROOT/'run.py').read_bytes()).hexdigest(),
               'manifest': manifest, 'planned_calls': 32, 'retries': 0, 'fallback': False})
        for index, case in enumerate(cases):
            jev_payload = run.make_request(case, policy)
            payloads = {'jev': jev_payload, 'sonnet': sonnet_request(jev_payload)}
            # Alternate order to reduce provider-order bias.
            for provider in (('jev', 'sonnet') if index % 2 == 0 else ('sonnet', 'jev')):
                payload = payloads[provider]
                allowance = reserve(provider, payload)
                if spent + allowance > BUDGET_USD:
                    stop = 'budget_reserve_exceeded'
                    break
                if not args.live:
                    write({'type': 'request', 'case_id': case['id'], 'provider': provider,
                           'request': payload, 'reserved_usd': allowance})
                    continue
                row = {'type': 'result', 'case_id': case['id'], 'expected': case['expected'],
                       'provider': provider, 'request': payload, 'reserved_usd': allowance}
                start = time.perf_counter()
                try:
                    raw = run.call_api(payload, os.environ['TYPESAFE_API_KEY']) if provider == 'jev' else sonnet_call(payload)
                    row['response'] = raw
                    row['usage'] = raw.get('usage')
                    row['cost_usd'] = cost(provider, row['usage'])
                    spent += row['cost_usd']
                    parsed = run.parse_response(raw) if provider == 'jev' else parse_sonnet(raw)
                    row['decision'] = parsed['decision']
                    if provider == 'jev':
                        row.update(confidence=parsed['confidence'], probabilities=parsed['probabilities'])
                    row['status'] = 'ok'
                    if row['cost_usd'] > allowance:
                        stop = 'input_allowance_underestimated'
                except (RuntimeError, ValueError, TypeError, KeyError):
                    row.update(status='error', error='request_usage_or_contract_failed')
                    stop = 'error_no_retry'
                row['latency_ms'] = (time.perf_counter()-start)*1000
                records.append(row)
                write(row)
                print(case['id'], provider, row['status'], row.get('decision', ''), flush=True)
                if stop:
                    break
            if stop:
                break
        result = {'type': 'summary', 'stop_reason': stop, 'known_cost_usd': spent,
                  'complete': len(records) == 32 and not stop, 'providers': summary(records)}
        write(result)
        (args.out/'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps(result, ensure_ascii=False))
    return 1 if stop else 0


if __name__ == '__main__':
    raise SystemExit(main())
