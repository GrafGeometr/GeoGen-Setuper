import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import runtime
from standard_reasoning import check,verify_certificate
from families import canonical_event

class MetricExtensionTests(unittest.TestCase):
    def test_fixed_ratios_have_independent_symbolic_checks(self):
        config='Triangle: A, B, C\nD = Midpoint(A, B)'
        for goal in ['LengthRatio2: A, B, A, D','LengthRatio2: A, D, A, B']:
            proof=check(config,goal)
            self.assertEqual(proof['status'],'proved')
            self.assertTrue(verify_certificate(config,goal,proof))
        self.assertEqual(check(config,'LengthRatio3: A, B, A, D')['status'],'unknown')
        self.assertEqual(check(config,'SquaredLengthRatio2: A, B, A, D')['status'],'unknown')

    def test_directed_angle_goal_uses_the_existing_modular_system(self):
        config='Triangle: A, B, C\nD = Midpoint(A, B)\nE = Midpoint(A, C)'
        goal='EqualAngles: [A, B], [B, C], [A, D], [D, E]'
        proof=check(config,goal)
        self.assertEqual(proof['status'],'proved')
        self.assertTrue(verify_certificate(config,goal,proof))
        wrong='EqualAngles: [A, B], [B, C], [D, E], [A, D]'
        self.assertEqual(check(config,wrong)['status'],'unknown')

    def test_angle_fingerprints_merge_regroupings(self):
        event={'configuration':'Quadrilateral: A, B, C, D','theorem':'EqualAngles: [A, B], [A, C], [D, B], [D, C]'}
        regroup=dict(event,theorem='EqualAngles: [A, B], [D, B], [A, C], [D, C]')
        reverse=dict(event,theorem='EqualAngles: [A, C], [A, B], [D, C], [D, B]')
        self.assertEqual(canonical_event(event,None),canonical_event(regroup,None))
        self.assertEqual(canonical_event(event,None),canonical_event(reverse,None))

    def test_missing_downstream_handler_is_detected_before_build(self):
        from builder import check_metric_theorem_coverage,BuildError
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            paths=[root/'GeometryExtending/app/Functions.hs',root/'Geometry/GeoGenIntegration/src/nativeMain/kotlin/Main.kt']
            types=['LengthRatio2','LengthRatio3','SquaredLengthRatio2','EqualAngles']
            for path in paths:path.parent.mkdir(parents=True)
            paths[0].write_text('factCheckers = Map.fromList '+','.join('("'+t+'", checker)' for t in types))
            paths[1].write_text('fun GeoGenContext.parseGeoGenGoal '+','.join('"'+t+'" -> handler' for t in types))
            self.assertEqual(set(check_metric_theorem_coverage(root)),set(types))
            paths[0].write_text(paths[0].read_text().replace('"EqualAngles"','"OtherType"'))
            with self.assertRaisesRegex(BuildError,'EqualAngles'):check_metric_theorem_coverage(root)

    def test_old_or_mixed_bundle_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'data').mkdir();dll=root/'core.dll';dll.write_bytes(b'fixture')
            with patch.object(runtime,'ROOT',root):
                with self.assertRaisesRegex(ValueError,'не поддерживает'):runtime.require_metric_bundle()
                (root/'data/metric-theorems.json').write_text(json.dumps({'sha256':{'core.dll':hashlib.sha256(dll.read_bytes()).hexdigest()}}))
                runtime.require_metric_bundle()
                dll.write_bytes(b'other')
                with self.assertRaisesRegex(ValueError,'Несовместимые'):runtime.require_metric_bundle()
