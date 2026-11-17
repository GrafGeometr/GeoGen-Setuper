from fractions import Fraction as Q
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from standard_reasoning import Context, ModularSystem, check, verify_certificate


class StandardReasoningTests(unittest.TestCase):
    def test_modular_equations_cannot_be_divided_like_real_equations(self):
        system = ModularSystem([({'x': 2}, Q(0), 'bisector')])
        self.assertIsNone(system.prove({'x': 1}, 0))
        self.assertIsNone(system.prove({'x': 1}, Q(1,2)))
        self.assertIsNotNone(system.prove({'x': 2}, 0))

    def test_integer_elimination_combines_relations_and_verifies_certificate(self):
        system = ModularSystem([({'x':2}, Q(1,2), 'a'), ({'x':1,'y':-1}, 0, 'b')])
        proof = system.prove({'y':2}, Q(1,2))
        self.assertIsNotNone(proof)
        self.assertTrue(system.verify({'y':2}, Q(1,2), proof))
        self.assertFalse(system.verify({'y':2}, 0, proof))
        self.assertIsNone(system.prove({'y':1}, Q(1,4)))

    def test_inconsistent_context_does_not_prove_every_goal(self):
        system = ModularSystem([({'x':1}, 0, 'a'), ({'x':1}, Q(1,2), 'b')])
        self.assertTrue(system.inconsistent)
        self.assertIsNone(system.prove({'z':1}, 0))

    def test_two_perpendiculars_yield_parallelism_without_numerical_facts(self):
        config = 'Triangle: A, B, C\nl = LineFromPoints(A, B)\nm = PerpendicularLine(C, l)\nn = PerpendicularLine(B, m)'
        result = check(config, 'ParallelLines: l, n')
        self.assertEqual(result['status'], 'proved')
        self.assertEqual(result['method'], 'angles')
        self.assertTrue(verify_certificate(config, 'ParallelLines: l, n', result))
        self.assertEqual(check(config, 'PerpendicularLines: l, n')['status'], 'unknown')

    def test_unknown_construction_does_not_supply_arbitrary_premises(self):
        config = 'Triangle: A, B, C\nD = UnhandledPoint(A, B, C)'
        self.assertEqual(check(config, 'CollinearPoints: A, B, D')['status'], 'unknown')

    def test_cyclic_base_provides_inscribed_angle_relations(self):
        result = check('CyclicQuadrilateral: A, B, C, D', 'ConcyclicPoints: A, C, B, D')
        self.assertEqual(result['status'], 'proved')
        self.assertEqual(result['method'], 'angles')
        self.assertEqual(check('Quadrilateral: A, B, C, D', 'ConcyclicPoints: A, B, C, D')['status'], 'unknown')

    def test_constructed_circle_memberships_are_symbolic_premises(self):
        config = ('Quadrilateral: A, B, C, D\nE = SecondIntersectionOfCircleAndLineFromPoints(A, B, C, D)'
                  '\nl = LineFromPoints(E, C)\nm = LineFromPoints(E, D)')
        result = check(config, 'ConcyclicPoints: A, E, C, D')
        self.assertEqual(result['status'], 'proved')
        self.assertTrue(verify_certificate(config, 'ConcyclicPoints: A, E, C, D', result))
        self.assertEqual(check(config, 'ConcyclicPoints: A, E, B, D')['status'], 'unknown')

    def test_collinear_points_do_not_satisfy_reverse_cyclic_criterion(self):
        config = 'Triangle: A, B, C\nD = Midpoint(A, B)\nE = Midpoint(A, D)'
        self.assertEqual(check(config, 'ConcyclicPoints: A, B, D, E')['status'], 'unknown')

    def test_both_bisectors_satisfy_same_doubled_angle_equation(self):
        config = 'Triangle: A, B, C\nl = InternalAngleBisector(A, B, C)\nm = ExternalAngleBisector(A, B, C)'
        self.assertEqual(check(config, 'ParallelLines: l, m')['status'], 'unknown')

    def test_affine_midpoint_identity_and_genuine_nonidentity(self):
        config = 'Triangle: A, B, C\nD = Midpoint(A, B)'
        result = check(config, 'EqualLineSegments: A, D, D, B')
        self.assertEqual(result['method'], 'affine')
        self.assertEqual(check(config, 'EqualLineSegments: A, D, D, C')['status'], 'unknown')
        self.assertTrue(verify_certificate(config, 'EqualLineSegments: A, D, D, B', result))
        result['certificate']['sources'] = []
        self.assertFalse(verify_certificate(config, 'EqualLineSegments: A, D, D, B', result))

    def test_affine_midline_and_guard_on_defined_line_objects(self):
        config = 'Triangle: A, B, C\nD = Midpoint(A, B)\nE = Midpoint(A, C)\nl = LineFromPoints(D, E)'
        self.assertEqual(check(config, 'ParallelLines: [D, E], [B, C]')['method'], 'affine')
        self.assertEqual(check(config, 'CollinearPoints: A, D, B')['status'], 'proved')
        self.assertEqual(check(config, 'CollinearPoints: B, D, E')['status'], 'unknown')

    def test_certificate_is_bound_to_hypotheses_and_goal(self):
        config = 'Triangle: A, B, C\nD = Midpoint(A, B)'
        result = check(config, 'CollinearPoints: A, D, B')
        self.assertFalse(verify_certificate(config.replace('Midpoint(A, B)', 'Midpoint(A, C)'), 'CollinearPoints: A, D, B', result))
        self.assertFalse(verify_certificate(config, 'CollinearPoints: A, D, C', result))

if __name__ == '__main__': unittest.main()
