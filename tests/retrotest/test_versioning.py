import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.retrotest import versioning
from research.retrotest.common import write_json


class VersionTests(unittest.TestCase):
    def test_bump_adds_one_increment_and_preserves_parent(self):
        model=versioning.model_snapshot('v0.1001')
        with tempfile.TemporaryDirectory() as tmp:
            versions=Path(tmp)
            old=versions/'v0.1001';old.mkdir()
            for name,text in model['files'].items():(old/name).write_text(text)
            with patch.object(versioning,'VERSIONS',versions):
                self.assertEqual(versioning.bump('v0.1001'),'v0.1002')
                child=versioning.model_snapshot('v0.1002')
                self.assertEqual(child['config']['parent'],'v0.1001')
                self.assertEqual(child['config']['parent_sha256'],model['sha256'])
                self.assertEqual(versioning.model_snapshot('v0.1001')['sha256'],model['sha256'])
                with self.assertRaises(ValueError):versioning.bump('v0.1001')
