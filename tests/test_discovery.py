import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import discovery
import runtime
from families import Catalog
from test_families import small_catalog


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.suite = self.root / 'suite'
        (self.suite / 'data').mkdir(parents=True); (self.suite / 'lib/geogen').mkdir(parents=True)
        (self.suite / 'data/construction-catalog.json').write_text(json.dumps(small_catalog().data))
        settings = dict(Serilog={}, ProblemGeneratorInputProviderSettings={}, InferenceRuleProviderSettings={},
                        ObjectIntroductionRuleProviderSettings={}, ProblemGenerationRunnerSettings={'ReadableBestTheoremFolder': ''})
        (self.suite / 'data/default-settings.json').write_text(json.dumps(settings))
        script = '''import sys,json,pathlib,time
settings=json.load(open(sys.argv[1]));folder=pathlib.Path(settings['ProblemGenerationRunnerSettings']['StreamingOutputFolder'])
(folder/'item.json').write_text(json.dumps(dict(schemaVersion=1,id='item',configuration='Triangle: A, B, C\\nD = Midpoint(A, B)',theorem='EqualLineSegments: A, D, D, B',readable='fixture')))
'''
        self.producer = self.suite / 'lib/geogen/GeoGen.MainLauncher.dll'; self.producer.write_text(script)
        self.calls = []
        for target, value in [('ROOT', self.suite), ('DOTNET', Path(sys.executable))]:
            context = patch.object(runtime, target, value); context.start(); self.addCleanup(context.stop)
        def checker(job, identifier, event, *args):
            self.calls.append(identifier)
            (job / 'geo' / (identifier + '.geo')).write_text('A = #(0)')
            return {'state': 'ready', 'geo': identifier + '.geo'}
        context = patch.object(discovery, 'process_event', checker); context.start(); self.addCleanup(context.stop)
        self.story = '''Constructions:\nMidpoint\nPointReflection\n\nInitial configuration:\nTriangle: A, B, C\n\nIterations: 1\nMaximalPoints: 2\nMaximalLines: 0\nMaximalCircles: 0\nSymmetryGenerationMode: GenerateBothSymmetricAndAsymmetric\n'''
        (self.root / 'story.txt').write_text(self.story)
        self.family = self.root / 'family.json'
        self.family.write_text(json.dumps(dict(version=1, stories=['story.txt'], growth={'maxAddedDefinitions':0},
                                              search=dict(maxDepth=1,maxJobs=2,sliceSeconds=1,maxPaletteSize=1))))

    def counts(self, output):
        return json.loads((output/'run-state.json').read_text())['counts']

    def test_parallel_jobs_share_one_check_and_preserve_both_origins(self):
        output = discovery.create(self.family, self.root/'campaign', budget=3, search_workers=2)
        self.assertEqual(self.counts(output), {'duplicate':1, 'ready':1})
        self.assertEqual(len(self.calls), 1)
        with sqlite3.connect(output/'tasks.sqlite') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM origins').fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT count(*) FROM search_jobs WHERE state='completed'").fetchone()[0], 2)
        manifest = (output/'results.tsv').read_text()
        finished_before = (output/'geo'/ (self.calls[0]+'.geo')).stat().st_mtime_ns
        discovery.run(output)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(manifest, (output/'results.tsv').read_text())
        self.assertEqual((output/'geo'/(self.calls[0]+'.geo')).stat().st_mtime_ns, finished_before)

    def test_bounded_search_preserves_events_and_can_continue_unstarted_jobs(self):
        self.producer.write_text(self.producer.read_text()+'time.sleep(10)\n')
        output = discovery.create(self.family, self.root/'campaign', budget=.25, search_workers=1)
        with sqlite3.connect(output/'tasks.sqlite') as db:
            states = dict(db.execute('SELECT state,count(*) FROM search_jobs GROUP BY state'))
        self.assertEqual(states, {'limited':1,'planned':1})
        self.assertEqual(self.counts(output), {'ready':1})
        self.producer.write_text(self.producer.read_text().replace('time.sleep(10)', 'time.sleep(0)'))
        discovery.run(output, extra_budget=2)
        self.assertEqual(self.counts(output), {'duplicate':1,'ready':1})
        self.assertEqual(len(self.calls), 1)

    def test_snapshot_is_self_contained_when_original_stories_are_removed(self):
        output = discovery.create(self.family, self.root/'campaign', budget=3)
        (self.root/'story.txt').unlink()
        from families import Family
        frozen = Family.read(output/'family/config.json', small_catalog())
        self.assertEqual(len(frozen.stories), 1)
        self.assertEqual(frozen.stories[0].layout, 'Triangle')
        discovery.run(output)
        self.assertEqual(len(self.calls), 1)

    def test_changed_backend_cannot_silently_reuse_old_cache(self):
        output = discovery.create(self.family, self.root/'campaign', budget=3)
        (self.suite/'BUILD.json').write_text('{"version":"new"}')
        with self.assertRaisesRegex(ValueError, 'изменились'):
            discovery.run(output)

    def test_recheck_after_upgrade_archives_diagrams_and_preserves_provenance(self):
        output = discovery.create(self.family, self.root/'campaign', budget=3)
        (self.suite/'BUILD.json').write_text('{"version":"upgraded"}')
        discovery.run(output,recheck=True)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.counts(output),{'ready':1,'duplicate':1})
        self.assertEqual(len(list((output/'archive').glob('*/geo/*.geo'))),1)
        with sqlite3.connect(output/'tasks.sqlite') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM origins').fetchone()[0],2)

    def test_bad_event_remains_visible_as_error(self):
        self.producer.write_text(self.producer.read_text()+"(folder/'broken.json').write_text('{')\n")
        output = discovery.create(self.family, self.root/'campaign', budget=3)
        self.assertEqual(self.counts(output), {'duplicate':1,'ready':1,'error':2})

    def test_budget_parser_rejects_nonfinite_negative_or_zero(self):
        self.assertEqual(discovery.seconds('1.5m'), 90)
        for value in ('nan','inf','-1','0','2d','100000000h'):
            with self.subTest(value=value), self.assertRaises(ValueError): discovery.seconds(value)

if __name__ == '__main__': unittest.main()
