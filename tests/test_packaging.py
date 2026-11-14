"""Persistence and release layout checks; never invoke the compiler."""
from pathlib import Path
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
import io
import sys
from contextlib import redirect_stdout
from unittest.mock import patch
from photo_review.paths import ROOT,data_directory,prepare_user_data,reset_user_data,acquire_profile_guard,INITIALIZED
from tools.build_windows import build_command,publish,archive_legacy_outputs

class PackagingTests(unittest.TestCase):
    def test_heic_decoder_is_included_in_build(self):
        command=build_command()
        self.assertIn('--include-package=pillow_heif',command)
        self.assertIn('--include-module=_pillow_heif',command)
    def test_video_player_backend_is_included_in_build(self):
        self.assertIn('--include-qt-plugins=multimedia',build_command())
    def test_development_keeps_repository_data(self):
        self.assertEqual(data_directory(compiled=False),ROOT/'data')
    def test_compiled_profile_is_outside_extraction_directory(self):
        self.assertEqual(data_directory(compiled=True,environment={'LOCALAPPDATA':'C:/UserData'}),Path('C:/UserData/PhotoReview/data'))
    def test_path_lookup_and_build_dry_run_do_not_create_appdata(self):
        from tools.build_windows import main as build_main
        with tempfile.TemporaryDirectory() as temporary:
            local=Path(temporary)/'not-created'
            path=data_directory(compiled=True,environment={'LOCALAPPDATA':str(local)})
            self.assertFalse(path.exists())
            with patch.dict('os.environ',{'LOCALAPPDATA':str(local)}),patch('sys.argv',['build_windows.py','--dry-run']),redirect_stdout(io.StringIO()):
                self.assertEqual(build_main(),0)
            self.assertFalse(local.exists())
    def test_first_run_initializes_once_without_repeating_migration(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile=Path(temporary)/'data'
            prepare_user_data(compiled=True,destination=profile,executable_dir=Path(temporary)/'exe')
            self.assertTrue((profile/INITIALIZED).exists())
            with patch('photo_review.paths.sqlite3.connect',side_effect=AssertionError('Initialization ran again')):
                self.assertIsNone(prepare_user_data(compiled=True,destination=profile,executable_dir=Path(temporary)/'other-version'))
    def test_reset_archives_only_profile_and_prevents_old_settings_returning(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);profile=base/'PhotoReview'/'data';profile.mkdir(parents=True)
            (profile/'preferences.json').write_text('{"last_folder":"old selection"}')
            (profile/'cache.sqlite3').write_bytes(b'old cached state')
            runtime=base/'PhotoReview'/'runtime';runtime.mkdir();(runtime/'app.dll').write_bytes(b'runtime')
            original=base/'pictures';original.mkdir();(original/'photo.jpg').write_bytes(b'original photo')
            legacy=base/'exe'/'data';legacy.mkdir(parents=True);(legacy/'preferences.json').write_text('{"last_folder":"legacy"}')
            backup=reset_user_data(profile)
            self.assertEqual((backup/'cache.sqlite3').read_bytes(),b'old cached state')
            self.assertFalse((profile/'preferences.json').exists());self.assertFalse((profile/'cache.sqlite3').exists())
            self.assertTrue((profile/INITIALIZED).exists())
            prepare_user_data(compiled=True,destination=profile,executable_dir=base/'exe')
            self.assertFalse((profile/'preferences.json').exists())
            self.assertEqual((runtime/'app.dll').read_bytes(),b'runtime');self.assertEqual((original/'photo.jpg').read_bytes(),b'original photo')
            self.assertNotEqual(reset_user_data(profile),backup)
    @unittest.skipUnless(sys.platform=='win32','Windows named mutex')
    def test_running_profile_is_guarded_without_creating_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile=Path(temporary)/'not-created'
            guard=acquire_profile_guard(profile)
            try:
                with self.assertRaisesRegex(RuntimeError,'already using'):acquire_profile_guard(profile)
                self.assertFalse(profile.exists())
            finally:guard.close()
            again=acquire_profile_guard(profile);again.close()

    def test_onefile_command_uses_private_build_directory_and_versioned_cache(self):
        command=build_command(['--jobs=4'],build_id='abc123')
        self.assertIn('--mode=onefile',command);self.assertIn('--output-dir=.build/nuitka',command)
        self.assertIn('--onefile-tempdir-spec={CACHE_DIR}/PhotoReview/runtime/abc123',command)
        self.assertEqual(command[-1],'--jobs=4')
    def test_profile_migration_preserves_choices_and_rehomes_cached_thumbnails(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);exe=base/'dist';source=exe/'main.dist'/'data';source.mkdir(parents=True)
            (source/'preferences.json').write_text(json.dumps({'last_folder':'C:/Pictures','phone_detection_enabled':True}))
            (source/'review-draft.json').write_text(json.dumps({'decisions':{'photo':False}}))
            (source/'REVIEW_ONLY').write_text('Keep test lock')
            (source/'thumbnails').mkdir();thumb=source/'thumbnails'/'photo.jpg';thumb.write_bytes(b'cached preview')
            with closing(sqlite3.connect(source/'cache.sqlite3')) as db:
                db.execute('CREATE TABLE photos(identity TEXT PRIMARY KEY,payload TEXT,gray BLOB)')
                db.execute('INSERT INTO photos VALUES(?,?,?)',('photo',json.dumps({'thumbnail':str(thumb)}),b'feature data'))
                db.commit()
            destination=base/'profile'
            self.assertEqual(prepare_user_data(compiled=True,destination=destination,executable_dir=exe),source)
            self.assertFalse(json.loads((destination/'review-draft.json').read_text())['decisions']['photo'])
            self.assertTrue((destination/'REVIEW_ONLY').exists());self.assertTrue(thumb.exists())
            with closing(sqlite3.connect(destination/'cache.sqlite3')) as db:
                payload,gray=db.execute('SELECT payload,gray FROM photos').fetchone()
                self.assertEqual(Path(json.loads(payload)['thumbnail']),destination/'thumbnails'/'photo.jpg')
                self.assertEqual(gray,b'feature data')
            self.assertEqual((destination/'thumbnails'/'photo.jpg').read_bytes(),b'cached preview')
    def test_existing_profile_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);(base/'preferences.json').write_text('{"last_folder":"my selection"}')
            self.assertIsNone(prepare_user_data(compiled=True,destination=base,executable_dir=base/'exe'))
            self.assertEqual(json.loads((base/'preferences.json').read_text())['last_folder'],'my selection')
    def test_archived_standalone_profile_is_found(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);source=base/'.build'/'legacy-standalone'/'main.dist'/'data';source.mkdir(parents=True)
            (source/'preferences.json').write_text('{}')
            self.assertEqual(prepare_user_data(compiled=True,destination=base/'profile',executable_dir=base/'dist'),source)
    def test_publish_only_copies_one_exe_and_preserves_user_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);stage=base/'.build'/'nuitka';stage.mkdir(parents=True)
            (stage/'PhotoReview.exe').write_bytes(b'fake onefile executable')
            legacy=base/'dist'/'main.dist';legacy.mkdir(parents=True);(legacy/'keep.txt').write_text('previous app data')
            output=base/'dist'/'PhotoReview.exe';self.assertEqual(publish(stage,output),output)
            self.assertEqual(archive_legacy_outputs(base),[])
            self.assertEqual([p.name for p in output.parent.iterdir()],['PhotoReview.exe'])
            self.assertEqual((base/'.build'/'legacy-standalone'/'main.dist'/'keep.txt').read_text(),'previous app data')
    def test_failed_publish_keeps_previous_exe(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);output=base/'PhotoReview.exe';output.write_bytes(b'old executable')
            with self.assertRaises(RuntimeError):publish(base/'missing-build',output)
            self.assertEqual(output.read_bytes(),b'old executable')
