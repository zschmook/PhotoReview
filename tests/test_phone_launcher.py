from pathlib import Path
import tempfile
import unittest
from launch_phone_test import prepare_test_profile

class PhoneLauncherTests(unittest.TestCase):
    def test_reset_archives_test_state_and_restores_deletion_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);profile=root/'phone-test';profile.mkdir()
            normal=root/'preferences.json';normal.write_text('normal profile unchanged')
            pictures=root/'phone-pictures';pictures.mkdir();photo=pictures/'photo.jpg';photo.write_bytes(b'original picture')
            (profile/'preferences.json').write_text('old test preferences')
            guard,backup=prepare_test_profile(profile,reset=True)
            self.assertTrue((profile/'PHONE_DELETE_DISABLED').exists())
            try:
                self.assertIsNotNone(backup);self.assertEqual((backup/'preferences.json').read_text(),'old test preferences')
                self.assertFalse((profile/'preferences.json').exists());self.assertTrue((profile/'REVIEW_ONLY').is_file())
                self.assertEqual(normal.read_text(),'normal profile unchanged');self.assertEqual(photo.read_bytes(),b'original picture')
            finally:guard.close()
    def test_running_test_profile_cannot_be_reset(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile=Path(temporary)/'phone-test';guard,_=prepare_test_profile(profile)
            (profile/'preferences.json').write_text('keep this')
            try:
                with self.assertRaisesRegex(RuntimeError,'already using'):prepare_test_profile(profile,reset=True)
                self.assertEqual((profile/'preferences.json').read_text(),'keep this')
            finally:guard.close()
