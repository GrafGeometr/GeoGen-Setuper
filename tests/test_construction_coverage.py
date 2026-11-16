"""Fail before packaging when any advertised construction is unsupported downstream."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from builder import BuildError, check_construction_coverage


class ConstructionCoverageTests(unittest.TestCase):
    def prepare(self, directory):
        root = Path(directory)
        data = {
            'GeoGen/Source/Library/GeoGen.Core/Constructions/PredefinedConstructions.cs':
                'public static PredefinedConstruction Midpoint { get; }',
            'GeoGen/Source/Library/GeoGen.Core/Constructions/ComposedConstructions.cs':
                'public static ComposedConstruction IncircleTangentPoint { get; }',
            'GeometryExtending/app/Functions.hs':
                '"Midpoint", mkBuilder\n"IncircleTangentPoint", mkBuilder',
            'Geometry/GeoGenIntegration/src/nativeMain/kotlin/Main.kt':
                'when (val funName = name) { "Midpoint" -> 1\n'
                '"LegacyContact", "IncircleTangentPoint" -> 2\nelse -> error("Unexpected $funName") }',
        }
        for name, text in data.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return root

    def test_full_coverage_includes_converter_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            actual = check_construction_coverage(self.prepare(directory))
            self.assertEqual(actual['required'], ['IncircleTangentPoint', 'Midpoint'])
            self.assertIn('LegacyContact', actual['converter'])

    def test_missing_initial_construction_in_checker_stops_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.prepare(directory)
            (root / 'GeometryExtending/app/Functions.hs').write_text('"Midpoint", mkBuilder')
            with self.assertRaisesRegex(BuildError, 'Extending: IncircleTangentPoint'):
                check_construction_coverage(root)

    def test_missing_converter_handler_stops_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.prepare(directory)
            path = root / 'Geometry/GeoGenIntegration/src/nativeMain/kotlin/Main.kt'
            path.write_text('when (val funName = name) { "Midpoint" -> 1\nelse -> error("Unexpected $funName") }')
            with self.assertRaisesRegex(BuildError, 'конвертер Geometry: IncircleTangentPoint'):
                check_construction_coverage(root)

    def test_unreadable_catalog_cannot_silently_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.prepare(directory)
            core = root / 'GeoGen/Source/Library/GeoGen.Core/Constructions'
            for path in core.glob('*.cs'):
                path.write_text('')
            with self.assertRaisesRegex(BuildError, 'список построений GeoGen'):
                check_construction_coverage(root)


if __name__ == '__main__':
    unittest.main()
