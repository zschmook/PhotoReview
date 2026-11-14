import hashlib
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from photo_review.version import VERSION,REPOSITORY
from tools.release_windows import validate_tag,release_assets,release_body
from tools.build_windows import build_command


class ReleaseTests(unittest.TestCase):
    def test_release_version_matches_windows_resources_and_metadata(self):
        self.assertEqual(REPOSITORY,'zschmook/PhotoReview')
        self.assertEqual(validate_tag('v'+VERSION),'v'+VERSION)
        for bad in ('v9999.0',VERSION,'v'+VERSION+'-beta','v'+VERSION+';bad'):
            with self.assertRaises(ValueError):validate_tag(bad)
        windows_version='.'.join((VERSION.split('.')+['0']*4)[:4])
        self.assertIn('--file-version='+windows_version,build_command())
        metadata=tomllib.loads((Path(__file__).resolve().parents[1]/'pyproject.toml').read_text())
        self.assertEqual(metadata['tool']['setuptools']['dynamic']['version']['attr'],'photo_review.version.VERSION')
    def test_release_notes_link_the_matching_tags_signing_policy(self):
        tag='v'+VERSION
        self.assertIn('https://github.com/zschmook/PhotoReview/blob/'+tag+'/docs/code-signing.md',release_body(tag))
        self.assertIn('## Code signing policy',release_body(tag))
    def test_release_checksums_describe_the_actual_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);exe=folder/'PhotoReview.exe';payload=b'generated fake EXE for manifest tests';exe.write_bytes(payload)
            files=release_assets(folder);digest=hashlib.sha256(payload).hexdigest()
            self.assertEqual([p.name for p in files],['PhotoReview.exe','SHA256SUMS.txt','release-manifest.json'])
            self.assertEqual((folder/'SHA256SUMS.txt').read_text(),digest+'  PhotoReview.exe\n')
            manifest=json.loads((folder/'release-manifest.json').read_text());self.assertEqual(manifest['sha256'],digest);self.assertEqual(manifest['size'],len(payload))
            self.assertEqual(exe.read_bytes(),payload)
    def test_release_requires_an_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):release_assets(temporary)
