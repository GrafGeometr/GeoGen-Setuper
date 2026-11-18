import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import quality
import streaming
from standard_reasoning import check,verify_certificate

EVENT=dict(configuration='Triangle: A, B, C\nD = Incenter(A, B, C)',theorem='EqualLineSegments: A, B, B, C',readable='fixture')

class QualityTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)

 def response(self,command,**kwargs):
  return types.SimpleNamespace(returncode=0,stdout=json.dumps(dict(status='sampled-valid',samples=int(command[-3]),seed=int(command[-2]))),stderr='')

 def test_samples_are_not_reported_as_proof_or_novelty(self):
  with patch.object(quality.subprocess,'run',side_effect=self.response):
   report=quality.inspect_event(EVENT,'backend',self.root,samples=8)
  self.assertEqual(report['verdict'],'sampled-valid')
  self.assertEqual(report['truth']['samples'],8)
  self.assertEqual(report['standard']['status'],'unknown')
  self.assertEqual(report['features']['unusedDefinitions'],['D'])
  self.assertEqual(report['qualityScore'],0)
  self.assertTrue((self.root/'quality.json').exists())

 def test_counterexample_has_a_separate_review_status(self):
  response=types.SimpleNamespace(returncode=0,stdout=json.dumps(dict(status='counterexample',sampleSeed=1731,acute=False,initialPoints=[])),stderr='')
  with patch.object(quality.subprocess,'run',return_value=response):
   report=quality.inspect_event(EVENT,'backend',self.root)
  self.assertEqual(report['verdict'],'review')
  self.assertIn('possible-acute-only-statement',report['warnings'])
  self.assertIn('1731',quality.description(report))

 def test_incomplete_samples_and_unsupported_backend_never_qualify(self):
  for text in ['Usage: old checker',json.dumps(dict(status='sampled-valid',samples=1,seed=0))]:
   with self.subTest(text=text),patch.object(quality.subprocess,'run',return_value=types.SimpleNamespace(returncode=0,stdout=text,stderr='')):
    self.assertEqual(quality.inspect_event(EVENT,'backend',self.root)['verdict'],'review')

 def test_standard_proof_does_not_need_random_samples(self):
  event=dict(configuration='Triangle: A, B, C\nD = Midpoint(A, B)',theorem='EqualLineSegments: A, D, D, B')
  with patch.object(quality.subprocess,'run') as backend:
   report=quality.inspect_event(event,'backend',self.root)
  self.assertEqual(report['verdict'],'standard');backend.assert_not_called()

 def test_acute_domain_is_always_visible(self):
  with patch.object(quality.subprocess,'run',side_effect=self.response):
   report=quality.inspect_event(EVENT,'backend',self.root,triangle_domain='acute')
  self.assertEqual(report['truth']['domain'],'acute')
  self.assertIn('acute-triangle-required',report['warnings'])

 def test_quality_columns_are_additive_and_stale_reports_are_removed(self):
  for name in ['events','tasks','geo']:(self.root/name).mkdir()
  event=dict(EVENT,schemaVersion=1,id='item')
  (self.root/'events/item.json').write_text(json.dumps(event))
  q=streaming.Queue(self.root)
  try:
   q.ingest()
   q.finish('item',dict(state='ready',geo='x.geo',quality=dict(qualityScore=70,verdict='sampled-valid')),False)
   q.export(dict(phase='finished',qualityChecks=True))
   self.assertEqual(len((self.root/'results.tsv').read_text().strip().split('\t')),9)
   q.finish('item',dict(state='error'),False)
   self.assertEqual(q.db.execute('SELECT count(*) FROM quality_reports').fetchone()[0],0)
  finally:q.close()

 def test_missing_diagram_cannot_be_marked_drawable(self):
  with self.assertRaises(sqlite3.OperationalError):quality.audit(self.root,'backend')
  self.assertFalse((self.root/'tasks.sqlite').exists())
  (self.root/'geo').mkdir()
  with sqlite3.connect(self.root/'tasks.sqlite') as db:
   db.execute('CREATE TABLE tasks(id TEXT,event TEXT,geo TEXT,state TEXT)')
   db.execute('INSERT INTO tasks VALUES(?,?,?,?)',('task',json.dumps(EVENT),'missing.geo','ready'))
  sample=dict(verdict='sampled-valid',truth={'status':'sampled-valid','samples':8},features={'unusedDefinitions':[]},
              standard={'status':'unknown'},warnings=[],qualityScore=100)
  complete=types.SimpleNamespace(returncode=0,stdout=json.dumps(dict(status='complete',checked=0,failures=[])),stderr='')
  with patch.object(quality,'inspect_event',return_value=sample),patch.object(quality.subprocess,'run',return_value=complete):
   report=quality.audit(self.root,'backend',diagrams=True)
  self.assertEqual(report['counts'],{'review':1})
  self.assertIn('diagram-not-drawable',report['entries'][0]['warnings'])

 def test_metric_filter_uses_right_angle_and_rejects_wrong_analogue(self):
  config='RightTriangle: A, B, C\nD = Midpoint(B, C)\nE = Midpoint(A, B)\nF = Midpoint(C, D)'
  proof=check(config,'EqualLineSegments: A, F, E, F')
  self.assertEqual(proof['method'],'metric')
  self.assertTrue(verify_certificate(config,'EqualLineSegments: A, F, E, F',proof))
  self.assertEqual(check(config.replace('RightTriangle','Triangle'),'EqualLineSegments: A, F, E, F')['status'],'unknown')

 def test_affine_intersections_and_finite_concurrency(self):
  config='Triangle: A, B, C\nD = Midpoint(A, B)\nE = Midpoint(A, C)\nF = IntersectionOfLinesFromPoints(B, E, C, D)\nG = IntersectionOfLinesFromPoints(A, F, B, C)'
  self.assertEqual(check(config,'EqualLineSegments: B, G, C, G')['method'],'affine')
  self.assertEqual(check(config,'ConcurrentLines: [A, F], [B, E], [C, D]')['method'],'affine')
  parallel='Triangle: A, B, C\nD = Midpoint(A, C)\nE = Midpoint(B, C)\nF = PointReflection(A, C)\nG = PointReflection(B, C)'
  self.assertEqual(check(parallel,'ConcurrentLines: [A, B], [D, E], [F, G]')['status'],'unknown')

 def test_known_cyclic_poncelet_pattern_requires_the_actual_hypotheses(self):
  config='CyclicQuadrilateral: A, B, C, D\nP = PonceletPoint(A, B, C, D)\nM = Midpoint(A, B)'
  proof=check(config,'PerpendicularLines: [P, M], [C, D]')
  self.assertEqual(proof['method'],'known-theorem')
  self.assertTrue(verify_certificate(config,'PerpendicularLines: [P, M], [C, D]',proof))
  self.assertEqual(check(config.replace('CyclicQuadrilateral','Quadrilateral'),'PerpendicularLines: [P, M], [C, D]')['status'],'unknown')
  self.assertEqual(check(config,'PerpendicularLines: [P, M], [A, C]')['status'],'unknown')

 def test_poncelet_on_a_diameter_circle_reduces_to_a_midpoint(self):
  config='Quadrilateral: A, B, C, D\nE = PerpendicularProjectionOnLineFromPoints(B, A, D)\nF = PerpendicularProjectionOnLineFromPoints(A, B, C)\nG = PonceletPoint(A, B, E, F)'
  proof=check(config,'EqualLineSegments: E, G, F, G')
  self.assertEqual(proof['method'],'affine')
  self.assertTrue(verify_certificate(config,'EqualLineSegments: E, G, F, G',proof))
  self.assertEqual(check(config.replace('PonceletPoint(A, B, E, F)','PonceletPoint(A, C, E, F)'),'EqualLineSegments: E, G, F, G')['status'],'unknown')

 def test_metric_circle_definitions_do_not_assume_the_goal(self):
  config='Triangle: A, B, C\nO = Circumcenter(A, B, C)\nD = MidpointOfArc(A, B, C)'
  self.assertEqual(check(config,'EqualLineSegments: B, D, C, D')['method'],'metric')
  self.assertEqual(check(config,'EqualLineSegments: A, D, C, D')['status'],'unknown')
  diameter='Triangle: A, B, C\nD = OppositePointOnCircumcircle(A, B, C)'
  self.assertEqual(check(diameter,'PerpendicularLines: [A, B], [B, D]')['method'],'metric')

if __name__=='__main__':unittest.main()
