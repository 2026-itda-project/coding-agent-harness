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
