import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import runtime

class RankBundleTests(unittest.TestCase):
    def test_rank_requires_a_matching_capable_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'data').mkdir();(root/'lib/geogen').mkdir(parents=True)
            dll=root/'lib/geogen/GeoGen.ProblemGenerator.dll';dll.write_bytes(b'fixture')
            marker={'version':1,'sha256':hashlib.sha256(dll.read_bytes()).hexdigest(),'features':['original-soft-search-v1']}
            path=root/'data/native-theorem-search.json';path.write_text(json.dumps(marker))
            with patch.object(runtime,'ROOT',root):
                runtime.require_search_bundle('soft')
                with self.assertRaisesRegex(ValueError,'ранговый интервал'):
                    runtime.require_search_bundle('soft',rank=True)
                marker['features'].append('exact-rank-interval-v1');path.write_text(json.dumps(marker))
                runtime.require_search_bundle('soft',rank=True)
                dll.write_bytes(b'other')
                with self.assertRaisesRegex(ValueError,'не соответствует DLL'):
                    runtime.require_search_bundle('soft',rank=True)
