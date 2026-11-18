import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from families import Catalog, Story
from search_calibration import distribution, shuffled_story, suggested_policy

class CalibrationTests(unittest.TestCase):
    def test_slow_stratum_has_the_same_weight_as_fast_stratum(self):
        result=distribution([[(0,10000)],[(10,1)]])
        self.assertEqual(result['samples'],10001)
        self.assertEqual(result['strata'],2)
        self.assertEqual(result['median'],0)
        self.assertEqual(result['p75'],10)
    def test_empty_strata_do_not_manufacture_observations(self):
        self.assertEqual(distribution([[],[]]),{'samples':0,'strata':0})
    def test_story_randomization_preserves_definitions_and_dependencies(self):
        c=Catalog.read(Path(__file__).parent/'data/relations-catalog.json')
        story=Story.parse((Path(__file__).resolve().parents[1]/'examples/search/stories/altitudes-symmetric.txt').read_text(),c)
        result=shuffled_story(story,'Midpoint',29);result.validate(c)
        self.assertEqual(set(result.definitions),set(story.definitions))
        self.assertEqual(set(result.constructions),set(story.constructions))
        self.assertEqual(result.constructions[0],'Midpoint')
        self.assertEqual(result.symmetry,story.symmetry)
    def test_no_threshold_is_generated_from_insufficient_samples(self):
        family={'stages':[{'stage':1,'theoremsPerPoint':{'samples':1},'positiveNewPointTheorems':{'samples':0}}]}
        self.assertEqual(suggested_policy(family,100)['phases'],[{}])
    def test_generated_limits_are_actual_counts_and_neutral_keeps_all(self):
        family={'stages':[{'stage':1,'theoremsPerPoint':{'samples':50,'strata':3,'p75':3},'positiveNewPointTheorems':{'samples':40,'strata':3,'p25':2}}], 'final':{'positiveNewPointTheorems':{'samples':40,'strata':3,'p25':2}}}
        policy=suggested_policy(family,100)
        self.assertEqual(policy['keepOutsideTargetsPercent'],100)
        self.assertEqual(policy['phases'],[{'theoremsPerPoint':{'min':3}}])
        self.assertEqual(policy['final'],{'newPointTheorems':{'min':1,'max':2}})
        self.assertNotIn('targetRatio',json.dumps(policy))
