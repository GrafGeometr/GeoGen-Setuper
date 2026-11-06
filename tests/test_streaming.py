"""Contracts for durable ingestion, explicit failure states, and restart safety."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import streaming


class StreamingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.job = Path(self.temp.name)
        for name in ('events', 'geo', 'tasks'):
            (self.job / name).mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def event(self, identifier='story_0001', **updates):
        event = dict(schemaVersion=1, id=identifier, configuration='Triangle: A, B, C\nD = Midpoint(A, B)',
                     theorem='EqualLineSegments: A, D, D, B', readable='readable theorem')
        event.update(updates)
        return event

    def publish(self, event):
        streaming.atomic_text(self.job / 'events' / (event['id'] + '.json'), json.dumps(event))

    def test_incomplete_event_is_invisible_then_published_once(self):
        temp = self.job / 'events/story_0001.json.tmp'
        temp.write_text('{')
        queue = streaming.Queue(self.job)
        try:
            self.assertEqual(queue.ingest(), 0)
            temp.write_text(json.dumps(self.event()))
            temp.rename(temp.with_suffix(''))
            self.assertEqual(queue.ingest(), 1)
            self.assertEqual(queue.ingest(), 0)
            self.assertEqual(queue.take()['id'], 'story_0001')
            self.assertIsNone(queue.take())
        finally:
            queue.close()

    def test_duplicate_detection_survives_restart_and_keeps_errors_visible(self):
        self.publish(self.event())
        queue = streaming.Queue(self.job)
        queue.ingest()
        queue.finish('story_0001', {'state': 'ready', 'geo': 'story_0001.geo'}, True)
        queue.close()
        self.publish(self.event('story_0002'))
        (self.job / 'events/broken.json').write_text('{')
        queue = streaming.Queue(self.job)
        try:
            self.assertEqual(queue.ingest(), 2)
            states = dict(queue.db.execute('SELECT id,state FROM tasks'))
            self.assertEqual(states, {'story_0001': 'ready', 'story_0002': 'duplicate', 'broken': 'error'})
            self.assertEqual(queue.db.execute("SELECT producer_alive FROM tasks WHERE id='story_0001'").fetchone()[0], 1)
        finally:
            queue.close()

    def test_worker_rejection_never_calls_converter(self):
        response = types.SimpleNamespace(returncode=0, stdout='{"status":"rejected","message":"redundant"}', stderr='')
        with patch.object(streaming.subprocess, 'run', return_value=response) as process:
            result = streaming.process_event(self.job, 'story_0001', self.event())
        self.assertEqual(result['state'], 'rejected')
        self.assertEqual(process.call_count, 1)
        self.assertEqual(list((self.job / 'geo').iterdir()), [])

    def test_invalid_or_crashed_checker_never_counts_as_acceptance(self):
        for code, stdout in [(0, ''), (0, '{"status":"unknown"}'), (1, '{"status":"accepted"}')]:
            with self.subTest(code=code, stdout=stdout), patch.object(streaming.subprocess, 'run',
                    return_value=types.SimpleNamespace(returncode=code, stdout=stdout, stderr='diagnostic')):
                result = streaming.process_event(self.job, 'story_0001', self.event())
                self.assertEqual(result['state'], 'error')
                self.assertEqual(list((self.job / 'geo').iterdir()), [])

    def test_timeout_has_its_own_state(self):
        with patch.object(streaming.subprocess, 'run', side_effect=subprocess.TimeoutExpired('check', 1)):
            self.assertEqual(streaming.process_event(self.job, 'story_0001', self.event())['state'], 'timeout')

    def test_failed_conversion_does_not_publish_partial_geo(self):
        def execute(command, **kwargs):
            if '--check-file' in command:
                return types.SimpleNamespace(returncode=0, stdout='{"status":"accepted"}', stderr='')
            (Path(command[-1]) / 'partial.geo').write_text('partial')
            return types.SimpleNamespace(returncode=1, stdout='', stderr='unsupported construction')
        with patch.object(streaming.subprocess, 'run', side_effect=execute):
            result = streaming.process_event(self.job, 'story_0001', self.event())
        self.assertEqual(result['state'], 'error')
        self.assertEqual(list((self.job / 'geo').iterdir()), [])

    def test_readable_filenames_preserve_type_and_score_with_safe_fallbacks(self):
        self.assertEqual(streaming.finding_filename('story_0001', self.event(ranking=4051.612345)),
                         'EqualLineSegments_4051.612345__story_0001.geo')
        self.assertEqual(streaming.finding_filename('story_0001', self.event(ranking=0)),
                         'EqualLineSegments_0__story_0001.geo')
        self.assertEqual(streaming.finding_filename('story_0001', self.event(ranking=-1.5)),
                         'EqualLineSegments_-1.5__story_0001.geo')
        for value in (None, True, float('nan'), float('inf'), 'bad/path'):
            self.assertEqual(streaming.finding_filename('story_0001', self.event(ranking=value)),
                             'EqualLineSegments_unrated__story_0001.geo')
        name = streaming.finding_filename('story_0001', self.event(theorem='../bad/name: A, B', ranking=1))
        self.assertEqual(Path(name).name, name)

    def test_parallel_equal_scores_publish_distinct_files_and_manifest_links_survive_resume(self):
        events = [self.event(f'story_{i:04}', ranking=4051.612345,
                             theorem=f'CollinearPoints: A, B, P{i}', readable=f'drawing {i}')
                  for i in range(2)]
        def execute(command, **kwargs):
            (Path(command[-1]) / 'converted.geo').write_text(Path(command[-2]).read_text())
            return types.SimpleNamespace(returncode=0, stdout='', stderr='')
        with patch.object(streaming.subprocess, 'run', side_effect=execute), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda event: streaming.process_event(
                self.job, event['id'], event, extending=False), events))
        self.assertEqual({result['state'] for result in results}, {'unchecked'})
        self.assertEqual(len({result['geo'] for result in results}), 2)
        queue = streaming.Queue(self.job)
        for event, result in zip(events, results):
            self.assertTrue(result['geo'].startswith('CollinearPoints_4051.612345__'))
            self.assertEqual((self.job / 'geo' / result['geo']).read_text(), event['readable'])
            self.publish(event)
        queue.ingest()
        for event, result in zip(events, results):
            queue.finish(event['id'], result, False)
        queue.export({'phase': 'finished', 'extending': False, 'checkMs': 1000})
        queue.close()
        before = {path.name: path.read_bytes() for path in (self.job / 'geo').glob('*.geo')}
        with patch.object(streaming, 'process_event') as worker:
            streaming.resume(self.job, workers=2)
            worker.assert_not_called()
        self.assertEqual({path.name: path.read_bytes() for path in (self.job / 'geo').glob('*.geo')}, before)
        manifest_names = [line.split('\t')[3] for line in (self.job / 'results.tsv').read_text().splitlines()]
        self.assertEqual(set(manifest_names), set(before))

    def test_resume_preserves_finished_results_and_requeues_interrupted_work(self):
        self.publish(self.event())
        self.publish(self.event('story_0002', theorem='CollinearPoints: A, D, B'))
        queue = streaming.Queue(self.job)
        queue.ingest()
        queue.finish('story_0001', {'state': 'ready', 'geo': 'story_0001.geo'}, True)
        queue.db.execute("UPDATE tasks SET state='processing' WHERE id='story_0002'")
        queue.db.commit()
        queue.export({'phase': 'stopped', 'extending': True, 'checkMs': 1000})
        queue.close()
        with patch.object(streaming, 'process_event', return_value={'state': 'rejected'}) as worker:
            streaming.resume(self.job, workers=1)
        state = json.loads((self.job / 'run-state.json').read_text())
        self.assertEqual(state['searchState'], 'stopped')
        self.assertIn('поиск был остановлен', (self.job / 'stream-status.txt').read_text())
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(worker.call_args.args[1], 'story_0002')
        with sqlite3.connect(self.job / 'tasks.sqlite') as db:
            self.assertEqual(dict(db.execute('SELECT id,state FROM tasks')),
                             {'story_0001': 'ready', 'story_0002': 'rejected'})

    def test_export_is_safe_for_tsv_and_displays_failed_items(self):
        self.publish(self.event(theorem='theorem\twith\nnewlines'))
        queue = streaming.Queue(self.job)
        try:
            queue.ingest()
            queue.finish('story_0001', {'state': 'error', 'message': 'bad\ninput\t!'}, False)
            queue.export({'phase': 'finished'})
            rows = (self.job / 'results.tsv').read_text().splitlines()
            self.assertEqual(len(rows), 1)
            self.assertEqual(len(rows[0].split('\t')), 7)
            self.assertIn('error', rows[0])
        finally:
            queue.close()

    def test_export_keeps_original_beauty_and_topic_and_does_not_reorder_on_completion(self):
        self.publish(self.event('story_0001', ranking=4051.612345))
        self.publish(self.event('story_0002', ranking=0, theorem='CollinearPoints: A, D, B'))
        queue = streaming.Queue(self.job)
        try:
            queue.ingest()
            queue.finish('story_0002', {'state': 'ready', 'geo': 'b.geo'}, True)
            queue.export({'phase': 'searching'})
            before = [line.split('\t') for line in (self.job / 'results.tsv').read_text().splitlines()]
            queue.finish('story_0001', {'state': 'ready', 'geo': 'a.geo'}, True)
            queue.export({'phase': 'finished'})
            after = [line.split('\t') for line in (self.job / 'results.tsv').read_text().splitlines()]
            self.assertEqual([row[0] for row in before], ['story_0001', 'story_0002'])
            self.assertEqual([row[0] for row in before], [row[0] for row in after])
            self.assertEqual(float(after[0][5]), 4051.612345)
            self.assertEqual(after[0][6], 'EqualLineSegments')
            self.assertEqual(after[1][5:], ['0', 'CollinearPoints'])
            # Reopening an old job recovers scores from saved events, without a new search.
            queue.close()
            queue = streaming.Queue(self.job)
            queue.export({'phase': 'finished'})
            self.assertEqual((self.job / 'results.tsv').read_text().splitlines()[0].split('\t')[5], '4051.612345')
        finally:
            queue.close()

    def test_unknown_and_nonfinite_beauty_is_not_presented_as_a_number(self):
        queue = streaming.Queue(self.job)
        try:
            for i, value in enumerate([None, True, float('nan'), float('inf'), 'bad']):
                self.publish(self.event(f'story_{i}', ranking=value, theorem=f'Unknown: {i}'))
            queue.ingest()
            queue.export({'phase': 'finished'})
            for line in (self.job / 'results.tsv').read_text().splitlines():
                self.assertEqual(line.split('\t')[5:], ['', 'Unknown'])
        finally:
            queue.close()

    def test_refresh_upgrades_finished_manifest_without_rechecking_or_changing_results(self):
        self.publish(self.event(ranking=1234.56789))
        queue = streaming.Queue(self.job)
        queue.ingest()
        queue.finish('story_0001', {'state': 'ready', 'geo': 'a.geo'}, True)
        queue.export({'phase': 'finished', 'searchState': 'completed'})
        finished = queue.db.execute('SELECT finished FROM tasks').fetchone()[0]
        queue.close()
        (self.job / 'results.tsv').write_text('story_0001\tready\tEqualLineSegments: A, D, D, B\ta.geo\t\n')
        with patch.object(streaming, 'process_event') as worker:
            self.assertTrue(streaming.refresh_results(self.job))
            worker.assert_not_called()
        cols = (self.job / 'results.tsv').read_text().splitlines()[0].split('\t')
        self.assertEqual(cols[5:], ['1234.56789', 'EqualLineSegments'])
        with sqlite3.connect(self.job / 'tasks.sqlite') as db:
            self.assertEqual(db.execute('SELECT finished FROM tasks').fetchone()[0], finished)
        self.assertEqual(json.loads((self.job / 'run-state.json').read_text())['phase'], 'finished')

    def test_refresh_does_not_touch_manifest_of_an_active_consumer(self):
        queue = streaming.Queue(self.job)
        queue.export({'phase': 'searching'})
        queue.close()
        old = (self.job / 'results.tsv').read_bytes()
        with streaming.job_lock(self.job):
            self.assertFalse(streaming.refresh_results(self.job))
        self.assertEqual((self.job / 'results.tsv').read_bytes(), old)

    def test_single_consumer_lock_prevents_parallel_processing(self):
        with streaming.job_lock(self.job):
            with self.assertRaisesRegex(RuntimeError, 'другим процессом'):
                with streaming.job_lock(self.job):
                    self.fail('Second consumer acquired the same job')


if __name__ == '__main__':
    unittest.main()
