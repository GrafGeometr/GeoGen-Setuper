import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from families import Catalog, Family, Story, canonical_event


def small_catalog():
    point = {'type': 'Point'}
    def item(name, args, parameters, output='Point'):
        return dict(name=name, arguments=args, parameters=parameters, output=output)
    pair = {'count': 2, 'item': point}
    return Catalog({'version': 1, 'constructions': [
        item('Midpoint', ['Point'] * 2, [pair]),
        item('PointReflection', ['Point'] * 2, [point, point]),
        item('CircleWithCenterThroughPoint', ['Point'] * 2, [point, point], 'Circle'),
        item('LineFromPoints', ['Point'] * 2, [pair], 'Line'),
        item('Circumcircle', ['Point'] * 3, [{'count': 3, 'item': point}], 'Circle'),
        item('IntersectionOfLinesFromPoints', ['Point'] * 4, [{'count': 2, 'item': pair}]),
        item('InversionOfPoint', ['Point', 'Circle'], [point, {'type': 'Circle'}])],
        'layouts': [dict(name=n, arguments=['Point'] * count, pipelineSupported=True) for n, count in
                    [('Triangle', 3), ('RightTriangle', 3), ('Quadrilateral', 4), ('CyclicQuadrilateral', 4), ('LineSegment', 2)]]})


STORY = '''Constructions:
Midpoint
PointReflection

Initial configuration:
RightTriangle: A, B, C
D = Midpoint(B, C)
HiddenPoints: A

Iterations: 2
MaximalPoints: 2
MaximalLines: 0
MaximalCircles: 0
SymmetryGenerationMode: GenerateBothSymmetricAndAsymmetric
'''


class FamiliesTests(unittest.TestCase):
    def setUp(self):
        self.catalog = small_catalog()

    def family(self, directory, **updates):
        root = Path(directory)
        (root / 'story.txt').write_text(STORY)
        data = dict(version=1, stories=['story.txt'], allowedConstructions=['Midpoint', 'PointReflection'],
                    growth=dict(maxAddedDefinitions=1, variantsPerStory=4),
                    search=dict(maxDepth=3, maxJobs=30, sliceSeconds=1, maxPaletteSize=2), seed=23)
        data.update(updates)
        path = root / 'family.json'; path.write_text(json.dumps(data))
        return Family.read(path, self.catalog)

    def test_story_roundtrip_preserves_original_definitions_and_hidden_points(self):
        story = Story.parse(STORY, self.catalog)
        self.assertEqual(Story.parse(story.text(), self.catalog), story)
        self.assertEqual(story.hidden, ('A',))

    def test_relaxation_is_additional_and_never_overwrites_original(self):
        with tempfile.TemporaryDirectory() as directory:
            family = self.family(directory)
            plans = family.plans()
            original = [p for p in plans if not p['addedDefinitions'] and not p['relaxedFrom']]
            relaxed = [p for p in plans if not p['addedDefinitions'] and p['relaxedFrom']]
            self.assertTrue(original and relaxed)
            self.assertIn('RightTriangle: A, B, C', original[0]['story'])
            self.assertIn('Triangle: A, B, C', relaxed[0]['story'])
            self.assertEqual((Path(directory) / 'story.txt').read_text(), STORY)

    def test_added_definitions_keep_family_bounds_and_hidden_points(self):
        with tempfile.TemporaryDirectory() as directory:
            family = self.family(directory)
            plans = family.plans()
            self.assertEqual(plans, family.plans())
            expanded = [p for p in plans if p['addedDefinitions']]
            self.assertTrue(expanded)
            for plan in expanded:
                story = Story.parse(plan['story'], self.catalog)
                self.assertEqual(story.definitions[0], ('D', 'Midpoint', ('B', 'C')))
                self.assertNotIn('A', story.definitions[-1][2])
                self.assertLessEqual(plan['addedDefinitions'] + story.iterations, 3)
                self.assertEqual(story.limits[0] + plan['addedDefinitions'], 2)
                self.assertTrue(set(story.constructions) <= {'Midpoint', 'PointReflection'})

    def test_equivalent_relaxed_seed_keeps_both_origins(self):
        with tempfile.TemporaryDirectory() as directory:
            family = self.family(directory)
            from dataclasses import replace
            family.stories.append(replace(family.stories[0], layout='Triangle'))
            plans = family.plans()
            shared = [p for p in plans if len(p['seedOrigins']) > 1]
            self.assertTrue(shared)
            self.assertTrue(any(any(o['relaxedFrom'] == 'RightTriangle' for o in p['seedOrigins']) for p in shared))

    def test_unknown_options_and_empty_glob_fail_before_search(self):
        with tempfile.TemporaryDirectory() as directory:
            for updates in [dict(stories=['missing/*.txt']), dict(includeRelaxations='yes'), dict(search={'maxDeph': 2})]:
                with self.subTest(updates=updates), self.assertRaises(ValueError):
                    self.family(directory, **updates)

    def test_types_and_unsupported_layouts_are_checked(self):
        with self.assertRaises(ValueError):
            Story.parse(STORY.replace('D = Midpoint(B, C)', 'D = InversionOfPoint(B, C)'), self.catalog)
        with self.assertRaises(ValueError):
            Story.parse(STORY.replace('RightTriangle: A, B, C', 'LineAndPoint: A, B'), self.catalog)

    def event(self, configuration, theorem):
        return dict(configuration=configuration, theorem=theorem)

    def test_dedup_handles_renaming_set_arguments_and_independent_order(self):
        a = self.event('Triangle: A, B, C\nD = Midpoint(A, B)\nE = Midpoint(A, C)', 'EqualLineSegments: D, A, A, E')
        b = self.event('Triangle: X, Y, Z\nQ = Midpoint(Z, X)\nP = Midpoint(Y, X)', 'EqualLineSegments: X, Q, P, X')
        self.assertEqual(canonical_event(a, self.catalog), canonical_event(b, self.catalog))

    def test_distinct_hypotheses_and_ordered_arguments_never_merge(self):
        a = self.event('Triangle: A, B, C\nD = CircleWithCenterThroughPoint(A, B)', 'Incidence: C, D')
        b = self.event('Triangle: A, B, C\nD = CircleWithCenterThroughPoint(B, A)', 'Incidence: C, D')
        # Distinguish the roles of A/B with a further asymmetric definition.
        for event in (a, b):
            event['configuration'] += '\nE = PointReflection(A, C)'
        self.assertNotEqual(canonical_event(a, self.catalog), canonical_event(b, self.catalog))
        b = self.event(a['configuration'].replace('Triangle:', 'RightTriangle:'), a['theorem'])
        self.assertNotEqual(canonical_event(a, self.catalog), canonical_event(b, self.catalog))

    def test_explicit_and_point_defined_line_have_the_same_goal(self):
        a = self.event('Triangle: A, B, C\nl = LineFromPoints(A, B)', 'PerpendicularLines: l, [A, C]')
        b = self.event(a['configuration'], 'PerpendicularLines: [B, A], [C, A]')
        self.assertEqual(canonical_event(a, self.catalog), canonical_event(b, self.catalog))

    def test_grouping_of_four_point_construction_is_preserved(self):
        sig = self.catalog
        self.assertEqual(sig.arguments_key('IntersectionOfLinesFromPoints', ['A','B','C','D']), sig.arguments_key('IntersectionOfLinesFromPoints', ['D','C','B','A']))
        self.assertNotEqual(sig.arguments_key('IntersectionOfLinesFromPoints', ['A','B','C','D']), sig.arguments_key('IntersectionOfLinesFromPoints', ['A','C','B','D']))


if __name__ == '__main__': unittest.main()
