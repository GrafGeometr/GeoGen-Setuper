"""Regression checks for filesystem safety and failure reporting."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import builder
import runtime


class BuilderSafetyTests(unittest.TestCase):
    def test_staged_sources_do_not_modify_original_or_copy_old_builds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            (source / 'app').mkdir(parents=True)
            (source / 'app/Main.hs').write_text('original')
            (source / 'dist-newstyle').mkdir()
            (source / 'dist-newstyle/stale').write_text('binary')
            (source / '.git').mkdir()
            builder.copy_sources(source, root / 'staged')
            (root / 'staged/app/Main.hs').write_text('patched')
            self.assertEqual((source / 'app/Main.hs').read_text(), 'original')
            self.assertFalse((root / 'staged/dist-newstyle').exists())
            self.assertFalse((root / 'staged/.git').exists())

    def test_existing_job_is_never_cleared(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input.txt'
            source.write_text('story')
            job = root / 'job'
            job.mkdir()
            (job / 'result').write_text('valuable')
            with self.assertRaises(FileExistsError):
                runtime.run_pipeline(source, job)
            self.assertEqual((job / 'result').read_text(), 'valuable')

    def test_settings_follow_relocated_bundle_and_new_job(self):
        with tempfile.TemporaryDirectory(prefix='with spaces ') as directory:
            root = Path(directory)
            bundle = root / 'relocated bundle'
            (bundle / 'data').mkdir(parents=True)
            settings = {
                'Serilog': {}, 'ProblemGeneratorInputProviderSettings': {},
                'InferenceRuleProviderSettings': {}, 'ObjectIntroductionRuleProviderSettings': {},
                'ProblemGenerationRunnerSettings': {'ReadableBestTheoremFolder': 'old/path', 'WriteJsonOutput': True},
                'TheoremFindingSettings': {'SoughtTheoremTypes': ['CollinearPoints']},
            }
            (bundle / 'data/default-settings.json').write_text(json.dumps(settings))
            job = root / 'new job'
            with patch.object(runtime, 'ROOT', bundle):
                actual = runtime.job_settings(job)
            self.assertTrue(Path(actual['ProblemGenerationRunnerSettings']['ReadableBestTheoremFolder']).is_relative_to(job))
            self.assertTrue(actual['ObjectIntroductionRuleProviderSettings']['FilePath'].startswith(str(bundle)))
            self.assertEqual(actual['TheoremFindingSettings'], settings['TheoremFindingSettings'])

    def test_geogen_caught_error_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input.txt'
            source.write_text('story')
            def fake_execute(command, **kwargs):
                Path(kwargs['logfile']).write_text("Couldn't perform the algorithm on input file 1")
            with patch.object(runtime, 'job_settings', return_value={}), patch.object(runtime, 'execute', fake_execute):
                with self.assertRaisesRegex(RuntimeError, 'GeoGen'):
                    runtime.run_pipeline(source, root / 'job')

    def test_check_refuses_bad_filename_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'a.geo').write_text('a')
            (root / 'BAD_a.geo').write_text('a')
            with self.assertRaisesRegex(RuntimeError, 'конфликт'):
                runtime.check(root)
            self.assertTrue((root / 'a.geo').exists())
            self.assertTrue((root / 'BAD_a.geo').exists())

    def test_converter_does_not_overwrite_existing_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'input.txt').write_text('input')
            out = root / 'geo'
            out.mkdir()
            (out / 'a.geo').write_text('valuable')
            with self.assertRaisesRegex(RuntimeError, 'пустым'):
                runtime.convert(root / 'input.txt', out)
            self.assertEqual((out / 'a.geo').read_text(), 'valuable')

    def test_proxy_userinfo_never_enters_command_arguments(self):
        with self.assertRaisesRegex(builder.BuildError, 'credentials'):
            builder.proxy_jvm_options({'HTTPS_PROXY': 'http://name:secret@proxy:8080'})


if __name__ == '__main__':
    unittest.main()
