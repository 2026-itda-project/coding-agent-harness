"""Frozen Jev comparison suites. No fallback, retries, extraction, or prose generation."""
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


def parse_v3(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get('answers'), dict) or set(raw['answers']) != {'decision', 'conflict'}:
        raise ValueError('invalid_answer_ids')
    base = run.parse_response({**raw, 'answers': {'decision': raw['answers']['decision']}})
    conflict = raw['answers']['conflict']
    if not isinstance(conflict, dict) or conflict.get('type') != 'noul' or not run.unit_interval(conflict.get('noul')):
        raise ValueError('invalid_conflict_probability')
    override = conflict['noul'] >= 0.5
    return {**base, 'base_decision': base['decision'],
            'base_confidence': base['confidence'], 'base_probabilities': base['probabilities'],
            'conflict_probability': conflict['noul'], 'conflict_override': override,
            'decision': 'ask_user' if override else base['decision'],
            # A routing override has no calibrated four-label distribution or confidence.
            'confidence': None if override else base['confidence'],
            'probabilities': None if override else base['probabilities']}


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
    parser.add_argument('--version', choices=('v0', 'v1', 'v2', 'v3'), default='v0', help='v0: paired baseline; v1/v2/v3: Jev only, saved Sonnet baseline')
    parser.add_argument('--sonnet-only', action='store_true', help='Evaluate Sonnet with frozen v2 English four-way criteria on original120; no Jev calls')
    parser.add_argument('--suite', choices=('pilot', 'original120'), default='pilot')
    parser.add_argument('--start-index', type=int, default=0, help='Skip completed original120 cases; zero-based, no automatic replay')
    args = parser.parse_args(argv)
    if args.sonnet_only and (args.version != 'v2' or args.suite != 'original120'):
        parser.error('sonnet_only_requires_v2_original120')
    baseline = json.loads((run.ROOT / 'versions/v0/config.json').read_text())
    source = (run.ROOT / 'datasets/pilot-v1.json').read_bytes()
    if hashlib.sha256(source).hexdigest() != baseline['dataset_sha256']:
        parser.error('dataset_changed')
    cases = json.loads(source)
    if [c['id'] for c in cases] != baseline['case_ids']:
        parser.error('case_order_changed')
    prompt_bytes = (run.ROOT / 'versions' / args.version / 'prompt.json').read_bytes()
    if args.version == 'v0' and hashlib.sha256(prompt_bytes).hexdigest() != baseline['prompt_sha256']:
        parser.error('baseline_prompt_changed')
    prompt = json.loads(prompt_bytes)
    question = prompt['jev_question']
    if args.version == 'v3':
        v2 = json.loads((run.ROOT/'versions/v2/prompt.json').read_text())
        if question != v2['jev_question'] or prompt.get('conflict_question', {}).get('type') != 'noul':
            parser.error('v3_must_preserve_v2_and_add_conflict_check')
    if question.get('type') != 'choice' or set(question.get('criteria', {})) != set(run.OPTIONS):
        parser.error('invalid_prompt')
    only_jev = args.version != 'v0'
    budget = min(BUDGET_USD, 0.01) if only_jev else BUDGET_USD
    planned_calls = 16 if only_jev else 32
    if len(cases) != 16 or Counter(c['expected'] for c in cases) != dict.fromkeys(run.OPTIONS, 4):
        parser.error('pilot_must_have_four_per_label')
    if any(c['split'] != 'dev' for c in cases):
        parser.error('pilot_must_use_dev')
    if args.suite == 'original120':
        if args.version not in ('v2', 'v3'):
            parser.error('original120 requires frozen v2 or v3')
        config = json.loads((run.ROOT/'versions'/args.version/'config.json').read_text())
        if hashlib.sha256(prompt_bytes).hexdigest() != config['prompt_sha256']:
            parser.error('frozen_prompt_changed')
        candidate = (run.ROOT/'evaluation/cases.proposed.json').read_bytes()
        if hashlib.sha256(candidate).hexdigest() != baseline['original_candidate_sha256']:
            parser.error('candidate_changed')
        revised = {c['id']: c for c in json.loads(candidate)}
        originals = run.load_cases()
        cases = [revised[c['id']] for c in originals]
        if len(cases) != 120 or len({c['id'] for c in cases}) != 120:
            parser.error('invalid_original120')
        for original, case in zip(originals, cases):
            if any(case[k] != original[k] for k in ('user_input', 'requirement', 'split', 'category')):
                parser.error('original_input_changed')
        source = json.dumps(cases, ensure_ascii=False, indent=2).encode()
        budget = min(BUDGET_USD, 0.05)
        planned_calls = 120
    if args.start_index:
        if args.suite != 'original120' or not 0 <= args.start_index < len(cases):
            parser.error('invalid_start_index')
        cases = cases[args.start_index:]
        planned_calls = len(cases)
    if args.sonnet_only:
        budget = 1.20
    single_provider = 'sonnet' if args.sonnet_only else ('jev' if only_jev else None)
    if args.live and not args.sonnet_only and not os.environ.get('TYPESAFE_API_KEY'):
        parser.error('jev_key_required')
    if args.live and (args.sonnet_only or not only_jev) and (os.environ.get('SONNET_MODEL') != SONNET_MODEL or
                                       not os.environ.get('ANTHROPIC_API_KEY')):
        parser.error('expected_sonnet_key_and_model_required')
    args.out.mkdir(parents=True, exist_ok=False)
    records, spent, stop = [], 0., None
    with (args.out / 'calls.jsonl').open('x') as output:
        def write(record):
            output.write(json.dumps(record, ensure_ascii=False, allow_nan=False)+'\n')
            output.flush()
        write({'type': 'run', 'mode': 'live' if args.live else 'dry_run', 'budget_usd': budget, 'version': args.version,
               'sonnet_only': args.sonnet_only, 'suite': args.suite, 'start_index': args.start_index, 'case_ids': [c['id'] for c in cases],
               'prices_per_million': PRICES, 'sonnet_max_output': MAX_OUTPUT,
               'sonnet_thinking': 'API default, recorded in raw response',
               'prompt_sha256': hashlib.sha256(prompt_bytes).hexdigest(),
               'dataset_sha256': hashlib.sha256(source).hexdigest(),
               'code_sha256': hashlib.sha256(Path(__file__).read_bytes() + (run.ROOT/'run.py').read_bytes()).hexdigest(),
               'baseline_config': baseline, 'planned_calls': planned_calls, 'retries': 0, 'fallback': False})
        for index, case in enumerate(cases):
            jev_payload = {'model': run.MODEL, 'state': {'user_input': case['user_input'],
                           'requirement': case['requirement']}, 'questions': {'decision': question}}
            if args.version == 'v3':
                jev_payload['questions']['conflict'] = prompt['conflict_question']
            payloads = {'jev': jev_payload, 'sonnet': sonnet_request(jev_payload)}
            # Alternate order to reduce provider-order bias.
            order = (single_provider,) if single_provider else (('jev', 'sonnet') if index % 2 == 0 else ('sonnet', 'jev'))
            for provider in order:
                payload = payloads[provider]
                allowance = reserve(provider, payload)
                if spent + allowance > budget:
                    stop = 'budget_reserve_exceeded'
                    break
                if not args.live:
                    write({'type': 'request', 'case_id': case['id'], 'provider': provider,
                           'request': payload, 'reserved_usd': allowance})
                    continue
                row = {'type': 'result', 'case_id': case['id'], 'expected': case['expected'],
                       'provider': provider, 'split': case['split'], 'category': case['category'],
                       'request': payload, 'reserved_usd': allowance}
                start = time.perf_counter()
                try:
                    raw = run.call_api(payload, os.environ['TYPESAFE_API_KEY']) if provider == 'jev' else sonnet_call(payload)
                    row['response'] = raw
                    row['usage'] = raw.get('usage')
                    row['cost_usd'] = cost(provider, row['usage'])
                    spent += row['cost_usd']
                    parsed = (parse_v3(raw) if args.version == 'v3' else run.parse_response(raw)) if provider == 'jev' else parse_sonnet(raw)
                    row['decision'] = parsed['decision']
                    if provider == 'jev':
                        row.update(confidence=parsed['confidence'], probabilities=parsed['probabilities'])
                        if args.version == 'v3':
                            row.update({k: parsed[k] for k in ('base_decision', 'base_confidence', 'base_probabilities', 'conflict_probability', 'conflict_override')})
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
                  'complete': len(records) == planned_calls and not stop,
                  'providers': {k: v for k, v in summary(records).items() if single_provider is None or k == single_provider},
                  'by_split': {split: summary([r for r in records if r['split'] == split])[single_provider] for split in ('dev','test')} if single_provider else None,
                  'by_category': {cat: summary([r for r in records if r['category'] == cat])[single_provider] for cat in sorted({c['category'] for c in cases})} if single_provider else None,
                  'reference_only': 'Saved Jev v3 original120; Sonnet uses v2 English four-way classifier without the separate conflict gate' if args.sonnet_only else 'Sonnet v0 covers only the original 16-case pilot, not this suite' if args.suite == 'original120' else ('versions/v0/summary.json (not called again)' if only_jev else None)}
        write(result)
        (args.out/'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps(result, ensure_ascii=False))
    return 1 if stop else 0


if __name__ == '__main__':
    raise SystemExit(main())
