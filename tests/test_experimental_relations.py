import json
from pathlib import Path
import sys,tempfile,unittest,os,subprocess,random
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from families import Catalog,Story
from experimental_relations import Geometry,Miner,analyze,analyze_configuration,sample_prefix,propose_actions,configure_worker,Unsupported,line
CATALOG=Catalog.read(Path(__file__).parent/'data/relations-catalog.json')
DUMPTY=(('P','DumptyPoint',('A','B','C')),('Q','DumptyPoint',('B','A','C')))
REFLECT=(('P','ReflectionInLineFromPoints',('A','B','C')),('Q','ReflectionInLineFromPoints',('B','A','C')))
def story(definitions=(),hidden=()):
 return Story('Triangle',('A','B','C'),definitions,('Midpoint','Orthocenter','DumptyPoint','HumptyPoint','PerpendicularProjectionOnLineFromPoints'),iterations=4,limits=(4,0,0),hidden=hidden)

class RelationTests(unittest.TestCase):
 def test_two_dumpty_points_share_carrier_without_prover_or_fact_library(self):
  for seed in [17,411]:
   result=analyze(story(DUMPTY),CATALOG,seed=seed)
   self.assertEqual(result['status'],'joint-witness-found')
   self.assertIn('shared-circle',result['families'])
   self.assertTrue(any('IntersectionOfLines' in s for s in result['anchorDescriptions'].values()))
   self.assertGreater(result['strength'],analyze(story(REFLECT),CATALOG,seed=seed)['strength'])

 def test_line_contains_both_endpoints_away_from_origin(self):
  a,b=(1.,0.),(0.,1.);l=line(a,b)
  for p in (a,b):self.assertAlmostEqual(l[0]*p[0]+l[1]*p[1]+l[2],0.)

 def test_midpoints_have_joint_relation(self):
  result=analyze(story((('P','Midpoint',('A','B')),('Q','Midpoint',('A','C')))),CATALOG)
  self.assertIn('parallel',result['families'])

 def test_three_dumpty_points_form_one_unnamed_circle_group(self):
  result=analyze_configuration(story(DUMPTY+(('R','DumptyPoint',('C','A','B')),)),CATALOG)
  self.assertTrue(any(g['members']==['P','Q','R'] for g in result['groups']))

 def test_joint_three_point_structure_is_not_reduced_to_individual_pairs(self):
  source=story((('P','Circumcenter',('A','B','C')),('Q','Orthocenter',('A','B','C')),('R','Centroid',('A','B','C'))))
  result=analyze_configuration(source,CATALOG)
  self.assertTrue(any(g['kind']=='collinear' and g['members']==['P','Q','R'] for g in result['groups']))

 def test_macro_names_have_no_effect_on_joint_relation_detection(self):
  data=json.loads(json.dumps(CATALOG.data))
  spec=next(c for c in data['constructions'] if c['name']=='DumptyPoint').copy()
  spec['name']='AnonymousConstruction';data['constructions'].append(spec)
  other=Catalog(data)
  definitions=tuple((n,'AnonymousConstruction',args) for n,_,args in DUMPTY)
  self.assertEqual(analyze(story(DUMPTY),CATALOG)['families'],analyze(story(definitions),other)['families'])

 def test_inverse_relation_is_discovered_without_named_theorem(self):
  for operation in ['Isogonal','Isotomic']:
   source=story((('P','DumptyPoint',('A','B','C')),('Q',operation,('P','A','B','C'))))
   result=analyze(source,CATALOG)
   self.assertIn('involution',result['families'])
   self.assertLessEqual(result['transformationProbes'],2)

 def test_named_and_expanded_humpty_have_same_strength(self):
  named=story((('P','HumptyPoint',('A','B','C')),('Q','HumptyPoint',('B','A','C'))))
  explicit=story((('H','Orthocenter',('A','B','C')),('M','Midpoint',('B','C')),('N','Midpoint',('A','C')),
                  ('P','PerpendicularProjectionOnLineFromPoints',('H','A','M')),
                  ('Q','PerpendicularProjectionOnLineFromPoints',('H','B','N'))))
  self.assertEqual(analyze(named,CATALOG)['families'],analyze(explicit,CATALOG)['families'])

 def test_reflection_midpoint_wrapper_does_not_inflate_the_same_points(self):
  wrapped=story((('O','Circumcenter',('A','B','C')),('l','Symedian',('A','B','C')),('m','Symedian',('B','A','C')),
                 ('R','ReflectionInLine',('l','O')),('S','ReflectionInLine',('m','O')),
                 ('P','Midpoint',('O','R')),('Q','Midpoint',('O','S'))))
  self.assertEqual(analyze(story(DUMPTY),CATALOG)['families'],analyze(wrapped,CATALOG)['families'])

 def test_circle_made_from_targets_cannot_explain_them(self):
  g=Geometry(CATALOG);env=g.story(story(REFLECT));miner=Miner(g)
  baseline=miner.pair(env['P'],env['Q'])
  fabricated=g.compile('Circumcircle',(env['P'],env['Q'],env['A']))
  with_circle=miner.pair(env['P'],env['Q'],context=(fabricated,))
  self.assertEqual(baseline['families'],with_circle['families'])

 def test_invalid_intersection_is_never_cached_as_a_valid_value(self):
  g=Geometry(CATALOG);ab=g.compile('LineFromPoints',(g.roots['A'],g.roots['B']))
  bad=g.intern('IntersectionOfLines',(ab,ab),'Point')
  for _ in range(2):
   with self.assertRaises(ValueError):g.at(bad,0)

 def test_point_budget_and_depth_are_not_bypassed_by_prefix(self):
  result,trace=sample_prefix(story(DUMPTY),CATALOG,steps=2,proposal_budget=8,max_depth=4)
  self.assertEqual(result.limits[0],4-trace['addedDefinitions'])
  self.assertLessEqual(result.iterations+trace['addedDefinitions'],4)
  self.assertEqual(result.definitions[:2],DUMPTY)
  self.assertTrue(all(s['proposed']<=8 and s['drawn']<=s['proposed'] for s in trace['trace']))

 def test_sampling_reproducible_and_hidden_points_not_used(self):
  source=story(DUMPTY,hidden=('P',))
  a,trace=sample_prefix(source,CATALOG,steps=1,proposal_budget=8,seed=31)
  b,_=sample_prefix(source,CATALOG,steps=1,proposal_budget=8,seed=31)
  self.assertEqual(a.text(),b.text())
  self.assertTrue(all('P' not in args for _,_,args in a.definitions[len(source.definitions):]))

 def test_empty_witness_search_does_not_claim_disconnection(self):
  definitions=(('I','Incenter',('A','B','C')),('H','Orthocenter',('A','B','C')),
               ('D','HumptyPoint',('A','B','C')),('E','DumptyPoint',('B','A','C')),
               ('P','PerpendicularProjectionOnLineFromPoints',('I','D','E')),
               ('Q','PerpendicularProjectionOnLineFromPoints',('H','B','E')))
  result=analyze(story(definitions),CATALOG)
  self.assertEqual(result['status'],'no-witness-within-budget')
  self.assertIn('not human difficulty',result['notice'])

 def test_unsupported_base_is_explicit(self):
  with self.assertRaises(Unsupported):Geometry(CATALOG,'Quadrilateral',('A','B','C','D'))

 def test_root_proposals_do_not_waste_slots_on_base_permutations(self):
  source=story();g=Geometry(CATALOG);env=g.story(source)
  proposals,attempts=propose_actions(source,CATALOG,g,env,Miner(g),random.Random(31),24)
  self.assertEqual(len({p['operation'] for p in proposals}),len(proposals))
  self.assertLessEqual(attempts,24*8)

 def test_worker_protocol_is_bounded_and_preserves_hidden_points(self):
  request=dict(version=1,layout='Triangle',roots=3,definitions=[dict(operation='Midpoint',arguments=[1,2])],
               hidden=[0],operations=['Midpoint'])
  env=dict(os.environ,GEOGEN_RELATION_CATALOG=str(Path(__file__).parent/'data/relations-catalog.json'),
           GEOGEN_RELATION_PROPOSALS='8',GEOGEN_RELATION_KEEP='2',GEOGEN_RELATION_POLICY='joint')
  run=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'experimental_relations.py'),'--worker'],
                     input=json.dumps(request)+'\n',capture_output=True,text=True,env=env,timeout=15,check=True)
  result=json.loads(run.stdout)
  self.assertNotIn('error',result)
  self.assertTrue(result['actions'])
  self.assertLessEqual(len(result['actions']),2)
  self.assertLessEqual(result['statistics']['proposed'],8)
  self.assertTrue(all(0 not in a['arguments'] for a in result['actions']))

 def test_stale_bundle_cannot_silently_ignore_the_experimental_switch(self):
  with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{},clear=True):
   with self.assertRaises(ValueError):configure_worker(folder)
   self.assertNotIn('GEOGEN_RELATION_WORKER',os.environ)

if __name__=='__main__':unittest.main()
