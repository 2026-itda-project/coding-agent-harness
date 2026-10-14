import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import compare
import run

class CompareTests(unittest.TestCase):
    def test_equivalent_inputs_without_label_leak(self):
        case = run.load_cases()[0]
        case.update(expected='SECRET', rationale='SECRET')
        jev = run.make_request(case, 'policy')
        sonnet = compare.sonnet_request(jev)
        content = json.loads(sonnet['messages'][0]['content'])
        self.assertEqual(content['state'], jev['state'])
        self.assertEqual(content['criteria'], jev['questions']['decision']['criteria'])
        self.assertEqual(json.loads(sonnet['system']), jev['questions']['decision']['instructions'])
        self.assertNotIn('SECRET', json.dumps(sonnet))

    def test_cost_and_missing_usage(self):
        self.assertAlmostEqual(compare.cost('sonnet', {'input_tokens': 1000, 'output_tokens': 100}), .003)
        for usage in (None, {}, {'input_tokens': True, 'output_tokens': 0}):
            with self.assertRaises(ValueError):
                compare.cost('jev', usage)

    def test_dry_run_has_32_requests_no_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(compare, 'sonnet_call', side_effect=AssertionError), patch.object(run, 'call_api', side_effect=AssertionError), patch('sys.stdout', new_callable=io.StringIO):
                compare.main(['--out', str(Path(tmp)/'out')])
            rows=[json.loads(s) for s in (Path(tmp)/'out/calls.jsonl').read_text().splitlines()]
            self.assertEqual(sum(r['type']=='request' for r in rows), 32)
            self.assertEqual(rows[-1]['known_cost_usd'], 0)
            self.assertFalse(rows[-1]['complete'])

    def test_budget_stops_before_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict('os.environ', {'SONNET_MODEL': compare.SONNET_MODEL, 'TYPESAFE_API_KEY':'x', 'ANTHROPIC_API_KEY':'x'}), patch.object(compare, 'BUDGET_USD', 0), patch.object(compare,'sonnet_call',side_effect=AssertionError), patch.object(run,'call_api',side_effect=AssertionError), patch('sys.stdout',new_callable=io.StringIO):
                self.assertEqual(compare.main(['--live','--out',str(Path(tmp)/'out')]), 1)
            result=json.loads((Path(tmp)/'out/summary.json').read_text())
            self.assertEqual(result['stop_reason'], 'budget_reserve_exceeded')
            self.assertEqual(result['known_cost_usd'],0)

    def test_truncated_response_is_not_a_valid_label(self):
        raw={'model':compare.SONNET_MODEL,'stop_reason':'end_turn','content':[{'type':'thinking','thinking':'x'},{'type':'text','text':'{"decision":"ready"}'}]}
        self.assertEqual(compare.parse_sonnet(raw)['decision'],'ready')
        raw['stop_reason']='max_tokens'
        with self.assertRaises(ValueError):
            compare.parse_sonnet(raw)

    def test_v1_only_calls_jev_and_keeps_v0_inputs(self):
        cases = json.loads((run.ROOT/'datasets/pilot-v1.json').read_text())
        answers = []
        for c in cases:
            answers.append({'model':run.MODEL, 'answers':{'decision':{
                'type':'choice','choice':c['expected'],'confidence':1.,
                'probabilities':{key:float(key==c['expected']) for key in run.OPTIONS}}},
                'usage':{'input_tokens':1000,'output_tokens':50}})
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict('os.environ', {'TYPESAFE_API_KEY':'test'}, clear=True), patch.object(compare,'sonnet_call',side_effect=AssertionError('Sonnet forbidden')), patch.object(run,'call_api',side_effect=answers) as jev, patch('sys.stdout',new_callable=io.StringIO):
                self.assertEqual(compare.main(['--version','v1','--live','--out',str(Path(tmp)/'out')]),0)
            self.assertEqual(jev.call_count,16)
            for c,call in zip(cases,jev.call_args_list):
                self.assertEqual(call.args[0]['state'], {'user_input':c['user_input'],'requirement':c['requirement']})
                self.assertNotIn('expected',call.args[0]['state'])
            result=json.loads((Path(tmp)/'out/summary.json').read_text())
            self.assertEqual(set(result['providers']),{'jev'})
            self.assertTrue(result['complete'])
            self.assertAlmostEqual(result['known_cost_usd'],16*1000*.042/1e6)


    def test_v2_preserves_korean_inputs_and_uses_english_prompt_only(self):
        cases = json.loads((run.ROOT/'datasets/pilot-v1.json').read_text())
        v2 = json.loads((run.ROOT/'versions/v2/prompt.json').read_text())['jev_question']
        v1 = json.loads((run.ROOT/'versions/v1/prompt.json').read_text())['jev_question']
        self.assertEqual(set(v2['instructions']),set(v1['instructions']))
        self.assertEqual(list(v2['criteria']),list(v1['criteria']))
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(compare,'sonnet_call',side_effect=AssertionError), patch.object(run,'call_api',side_effect=AssertionError), patch('sys.stdout',new_callable=io.StringIO):
                compare.main(['--version','v2','--out',str(Path(tmp)/'out')])
            rows=[json.loads(s) for s in (Path(tmp)/'out/calls.jsonl').read_text().splitlines()]
            requests=[r for r in rows if r['type']=='request']
            self.assertEqual(len(requests),16)
            self.assertEqual({r['provider'] for r in requests},{'jev'})
            for c,r in zip(cases,requests):
                self.assertEqual(r['request']['state'],{'user_input':c['user_input'],'requirement':c['requirement']})
                self.assertEqual(r['request']['questions']['decision'],v2)


    def test_original120_uses_revised_labels_and_no_sonnet(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(compare,'sonnet_call',side_effect=AssertionError), patch.object(run,'call_api',side_effect=AssertionError), patch('sys.stdout',new_callable=io.StringIO):
                compare.main(['--version','v2','--suite','original120','--out',str(Path(tmp)/'out')])
            rows=[json.loads(s) for s in (Path(tmp)/'out/calls.jsonl').read_text().splitlines()]
            requests=[r for r in rows if r['type']=='request']
            self.assertEqual(len(requests),120)
            self.assertEqual({r['provider'] for r in requests},{'jev'})
            self.assertFalse(any(r['case_id'].startswith('SPEC-') for r in requests))
            self.assertEqual(rows[0]['planned_calls'],120)
            self.assertEqual(rows[0]['budget_usd'],.05)


    def test_continuation_skips_completed_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(run,'call_api',side_effect=AssertionError), patch('sys.stdout',new_callable=io.StringIO):
                compare.main(['--version','v2','--suite','original120','--start-index','58','--out',str(Path(tmp)/'out')])
            rows=[json.loads(s) for s in (Path(tmp)/'out/calls.jsonl').read_text().splitlines()]
            requests=[r for r in rows if r['type']=='request']
            self.assertEqual(len(requests),62)
            self.assertEqual(requests[0]['case_id'],'EXPLICIT-19')
            self.assertEqual(rows[0]['planned_calls'],62)

    def test_v3_conflict_override_and_strict_validation(self):
        import copy
        base = {'type':'choice', 'choice':'ready', 'confidence':.9,
                'probabilities':{'ready':1.,'ai_refine':0.,'ask_user':0.,'defer_to_spec':0.}}
        raw = {'model':run.MODEL, 'answers':{'decision':base, 'conflict':{'type':'noul','noul':.5}},
               'usage':{'input_tokens':100,'output_tokens':50}}
        result = compare.parse_v3(raw)
        self.assertEqual(result['decision'], 'ask_user')
        self.assertEqual(result['base_decision'], 'ready')
        self.assertIsNone(result['confidence'])
        self.assertIsNone(result['probabilities'])
        raw['answers']['conflict']['noul'] = .49
        self.assertEqual(compare.parse_v3(raw)['decision'], 'ready')
        for bad in (True, -.1, 1.1, float('nan'), '0.9', None):
            invalid = copy.deepcopy(raw)
            invalid['answers']['conflict']['noul'] = bad
            with self.assertRaises(ValueError):
                compare.parse_v3(invalid)
        for answers in ({'decision':base}, {**raw['answers'],'extra':base},
                        {**raw['answers'],'conflict':{'type':'choice','noul':.9}},
                        {**raw['answers'],'decision':{**base,'confidence':2}}):
            with self.assertRaises(ValueError):
                compare.parse_v3({**raw,'answers':answers})
        # A no-conflict check cannot erase an ask_user decision from the base classifier.
        raw['answers']['decision'] = {**base, 'choice':'ask_user',
            'probabilities':{'ready':0.,'ai_refine':0.,'ask_user':1.,'defer_to_spec':0.}}
        self.assertEqual(compare.parse_v3(raw)['decision'], 'ask_user')

    def test_v3_full_suite_uses_two_questions_one_call_and_preserves_base(self):
        base = {'type':'choice','choice':'ready','confidence':1.,
                'probabilities':{k:float(k=='ready') for k in run.OPTIONS}}
        raw = {'model':run.MODEL, 'answers':{'decision':base,'conflict':{'type':'noul','noul':.8}},
               'usage':{'input_tokens':1000,'output_tokens':70}}
        v2 = json.loads((run.ROOT/'versions/v2/prompt.json').read_text())['jev_question']
        cases = run.load_cases()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict('os.environ', {'TYPESAFE_API_KEY':'test'}, clear=True), patch.object(compare,'sonnet_call',side_effect=AssertionError), patch.object(run,'call_api',return_value=raw) as jev, patch('sys.stdout',new_callable=io.StringIO):
                self.assertEqual(compare.main(['--version','v3','--suite','original120','--live','--out',str(Path(tmp)/'out')]),0)
            self.assertEqual(jev.call_count,120)
            for case, call in zip(cases, jev.call_args_list):
                payload = call.args[0]
                self.assertEqual(set(payload['questions']), {'decision','conflict'})
                self.assertEqual(payload['questions']['decision'],v2)
                self.assertEqual(payload['state'], {'user_input':case['user_input'],'requirement':case['requirement']})
            rows=[json.loads(s) for s in (Path(tmp)/'out/calls.jsonl').read_text().splitlines()]
            results=[r for r in rows if r['type']=='result']
            self.assertTrue(all(r['decision']=='ask_user' and r['base_decision']=='ready' for r in results))
            self.assertEqual(rows[-1]['providers']['jev']['unnecessary_questions'],61)
            self.assertAlmostEqual(rows[-1]['known_cost_usd'],.00504)
            self.assertTrue(rows[-1]['complete'])

    def test_sonnet_only_120_never_calls_jev_and_counts_cost(self):
        raw={'model':compare.SONNET_MODEL,'stop_reason':'end_turn',
             'content':[{'type':'text','text':'{"decision":"ready"}'}],
             'usage':{'input_tokens':2000,'output_tokens':200}}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict('os.environ', {'ANTHROPIC_API_KEY':'test','SONNET_MODEL':compare.SONNET_MODEL}, clear=True), patch.object(run,'call_api',side_effect=AssertionError), patch.object(compare,'sonnet_call',return_value=raw) as sonnet, patch('sys.stdout',new_callable=io.StringIO):
                self.assertEqual(compare.main(['--version','v2','--suite','original120','--sonnet-only','--live','--out',str(Path(tmp)/'out')]),0)
            self.assertEqual(sonnet.call_count,120)
            result=json.loads((Path(tmp)/'out/summary.json').read_text())
            self.assertEqual(set(result['providers']),{'sonnet'})
            self.assertEqual(result['providers']['sonnet']['correct'],41)
            self.assertEqual(result['by_split']['test']['attempted'],60)
            self.assertAlmostEqual(result['known_cost_usd'],.72)
            self.assertTrue(result['complete'])
