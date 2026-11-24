import json
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from standard_reasoning import check,check_conditional,verify_conditional_certificate
from families import canonical_event
from quality import structure
import conditional,runtime,streaming

class ConditionalTests(unittest.TestCase):
    def test_exact_directed_angle_reasoning_uses_the_explicit_premise(self):
        config='Triangle: A, B, C\nD = Midpoint(A, B)\nE = Midpoint(A, C)'
        premise='PerpendicularLines: [A, B], [B, C]'
        goal='PerpendicularLines: [A, D], [D, E]'
        self.assertEqual(check(config,goal)['status'],'unknown')
        proof=check_conditional(config,premise,goal)
        self.assertEqual(proof['status'],'proved')
        self.assertTrue(verify_conditional_certificate(config,premise,goal,proof))
        self.assertFalse(verify_conditional_certificate(config,'ParallelLines: [A, B], [B, C]',goal,proof))

    def test_conditions_and_goals_are_not_canonicalized_independently(self):
        event={'configuration':'Triangle: A, B, C','condition':'EqualLineSegments: A, B, A, C','theorem':'PerpendicularLines: [A, B], [B, C]'}
        other=dict(event,theorem='PerpendicularLines: [A, B], [A, C]')
        self.assertNotEqual(canonical_event(event,None),canonical_event(other,None))
        swapped=dict(event,condition='EqualLineSegments: A, C, A, B',theorem='PerpendicularLines: [A, C], [C, B]')
        self.assertEqual(canonical_event(event,None),canonical_event(swapped,None))

    def test_dependencies_include_the_additional_condition(self):
        config='Triangle: A, B, C\nD = Midpoint(A, B)'
        # No catalog is needed for structural dependency closure.
        self.assertEqual(structure({'configuration':config,'condition':'CollinearPoints: A, D, C','theorem':'EqualLineSegments: A, B, A, C'})['unusedDefinitions'],[])

    def test_absent_and_null_premises_keep_unconditional_features_equal(self):
        event={'configuration':'Triangle: A, B, C','theorem':'EqualLineSegments: A, B, A, C'}
        self.assertEqual(structure(event),structure(dict(event,condition=None)))

    def test_geo_round_trip_keeps_exactly_one_condition(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'a.geo';p.write_text('!Triangle: A, B, C\n!D = Midpoint(A, B)\n!Assume: EqualLineSegments: A, B, A, C\n!Witness: .5, 1\n!CollinearPoints: A, C, D - total ranking 1\n')
            e=conditional.read_geo_event(p)
            self.assertEqual(e['condition'],'EqualLineSegments: A, B, A, C')
            self.assertEqual(e['theorem'],'CollinearPoints: A, C, D')
            p.write_text(p.read_text()+'!Assume: CollinearPoints: A, B, C\n')
            with self.assertRaises(ValueError):conditional.read_geo_event(p)

    def test_conditional_event_does_not_go_to_unconditional_extending(self):
        with tempfile.TemporaryDirectory() as t:
            job=Path(t);(job/'tasks').mkdir()
            e={'configuration':'Triangle: A, B, C','condition':'EqualLineSegments: A, B, A, C','theorem':'CollinearPoints: A, B, C','readable':'fixture'}
            with patch('conditional.check_event',return_value={'status':'inconclusive','message':'insufficient roots'}) as native,patch('streaming.subprocess.run') as ordinary:
                result=streaming.process_event(job,'test',e,check_ms=1234)
            self.assertEqual(result['state'],'review');native.assert_called_once();ordinary.assert_not_called()

    def test_a_conditional_stream_does_not_merge_with_the_unconditional_goal(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'events').mkdir()
            e={'schemaVersion':1,'configuration':'Triangle: A, B, C','theorem':'CollinearPoints: A, B, C','readable':'fixture'}
            for id,condition in [('x',None),('y','EqualLineSegments: A, B, A, C'),('z','EqualLineSegments: A, B, B, C')]:
                event=dict(e,id=id)
                if condition:event['condition']=condition
                (root/'events'/f'{id}.json').write_text(json.dumps(event))
            queue=streaming.Queue(root)
            try:
                queue.ingest();rows=list(queue.db.execute('select state from tasks'));self.assertEqual([r[0] for r in rows],['queued']*3)
                queue.export({'phase':'finished'})
                self.assertIn('Дополнительное условие',''.join(p.read_text() for p in (root/'conditions').glob('*.txt')))
            finally:queue.close()

class ConditionalExtendingProtocolTests(unittest.TestCase):
    def event(self):
        return {'configuration':'Triangle: A, B, C\nD = Midpoint(A, B)','condition':'EqualLineSegments: A, D, C, D','theorem':'LengthRatio2: A, B, C, D'}

    def test_truth_only_acceptance_cannot_masquerade_as_complete_weakening_checks(self):
        from types import SimpleNamespace
        truth={'status':'accepted','samples':8,'genericCounterexamples':2,'counterexamples':[[.1,.3],[.5,1.2]]}
        with tempfile.TemporaryDirectory() as t,patch.object(runtime,'require_conditional_extending_bundle'),patch('conditional.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(truth),stderr='')):
            with self.assertRaisesRegex(ValueError,'Incomplete conditional weakening'):conditional.check_event(self.event(),Path(t))

    def test_incomplete_variant_coverage_is_not_accepted(self):
        from types import SimpleNamespace
        response={'status':'accepted','samples':8,'genericCounterexamples':2,'counterexamples':[[.1,.3],[.5,1.2]],
                  'weakening':{'status':'accepted','totalVariants':2,'completedVariants':1,'variants':[{'status':'counterexample'}]}}
        with tempfile.TemporaryDirectory() as t,patch.object(runtime,'require_conditional_extending_bundle'),patch('conditional.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(response),stderr='')):
            with self.assertRaisesRegex(ValueError,'Incomplete conditional weakening'):conditional.check_event(self.event(),Path(t))

    def test_full_variant_coverage_uses_native_extending_entry_point(self):
        from types import SimpleNamespace
        response={'status':'accepted','samples':8,'genericCounterexamples':2,'counterexamples':[[.1,.3],[.5,1.2]],
                  'weakening':{'status':'accepted','totalVariants':1,'completedVariants':1,'variants':[{'status':'counterexample'}]}}
        with tempfile.TemporaryDirectory() as t,patch.object(runtime,'require_conditional_extending_bundle'),patch('conditional.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(response),stderr='')) as worker:
            result=conditional.check_event(self.event(),Path(t))
            self.assertEqual(result['weakening']['completedVariants'],1)
            self.assertIn('--extend-conditional',worker.call_args.args[0])

    def test_quality_distinguishes_truth_checks_from_weakening_coverage(self):
        import quality
        features=quality.structure(self.event())
        with tempfile.TemporaryDirectory() as t,patch('standard_reasoning.check_conditional',return_value={'status':'unknown'}):
            full=quality.inspect_conditional(self.event(),Path(t),features,2000,{'status':'accepted','samples':8,'weakening':{'status':'accepted','completedVariants':1}})
            self.assertNotIn('conditional-weakening-not-checked',full['warnings'])
            truth=quality.inspect_conditional(self.event(),Path(t),features,2000,{'status':'accepted','samples':8})
            self.assertIn('conditional-weakening-not-checked',truth['warnings'])

    def test_directory_check_marks_only_decided_conditional_rejections(self):
        for status in ('rejected','accepted','inconclusive','timeout'):
            with self.subTest(status=status),tempfile.TemporaryDirectory() as t:
                p=Path(t)/'example.geo';p.write_text('!Triangle: A, B, C\n!D = Midpoint(A, B)\n!Assume: EqualLineSegments: A, D, C, D\n!LengthRatio2: A, B, C, D\n')
                with patch('conditional.check_event',return_value={'status':status}):runtime.check(Path(t))
                self.assertEqual((Path(t)/'BAD_example.geo').exists(),status=='rejected')
                self.assertEqual(p.exists(),status!='rejected')


class LayoutGeoParsingTest(unittest.TestCase):
    def test_every_layout_survives_geo_comment_roundtrip(self):
        import tempfile
        import conditional
        for initial in ('Triangle: A, B, C', 'RightTriangle: A, B, C',
                        'Quadrilateral: A, B, C, D', 'CyclicQuadrilateral: A, B, C, D',
                        'LineSegment: A, B', 'LineAndPoint: l, A', 'LineAndTwoPoints: l, A, B'):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as directory:
                p=Path(directory)/'test.geo'
                p.write_text('!'+initial+'\n!Assume: EqualLineSegments: A, B, A, C\n!CollinearPoints: A, B, C - total ranking 1\n')
                self.assertEqual(conditional.read_geo_event(p)['configuration'], initial)
