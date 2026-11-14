"""USB tests use fake Win32 metadata and disposable folders, never mounted drives."""
from pathlib import Path
import tempfile,threading,unittest
from contextlib import contextmanager
from itertools import count
from types import SimpleNamespace
from unittest.mock import patch
from PySide6.QtTest import QSignalSpy,QTest
from PySide6.QtWidgets import QApplication
from photo_review.usb_import import USBDrive,USBWatcher,enumerate_usb_drives,inventory_usb,import_usb,usb_source
from photo_review.phone_cleanup import cleanup_available
from photo_review.phone_download import digest


class USBDetectionTests(unittest.TestCase):
    def test_only_ready_removable_drives_are_reported_without_walking_files(self):
        volume_calls=[];error_modes=[]
        class API:
            def GetLogicalDrives(self):return sum(1<<index for index in (2,3,17,18,19))
            def GetDriveTypeW(self,root):return {'C:\\':3,'D:\\':5,'R:\\':2,'S:\\':4,'T:\\':2}[root]
            def SetThreadErrorMode(self,mode,old):
                error_modes.append(mode)
                if old is not None:old._obj.value=0
                return True
            def GetVolumeInformationW(self,root,label,size,serial,*args):
                volume_calls.append(root)
                if root=='T:\\':return False
                label.value='Family photos';serial._obj.value=123;return True
            def GetVolumeNameForVolumeMountPointW(self,root,guid,size):guid.value='\\\\?\\Volume{fixture}\\';return True
        with patch('photo_review.usb_import.os.scandir',side_effect=AssertionError('Detection must not walk files')):
            drives=enumerate_usb_drives(API())
        self.assertEqual(len(drives),1);self.assertEqual(drives[0].root,'R:\\');self.assertIn('Family photos',drives[0].name)
        self.assertEqual(volume_calls,['R:\\','T:\\']);self.assertEqual(error_modes,[0x8001,0,0x8001,0])
    def test_insertion_disconnect_and_same_letter_replacement(self):
        app=QApplication.instance() or QApplication([]);first=USBDrive('one','First','R:\\');second=USBDrive('two','Second','R:\\');calls=[0]
        def reader():
            calls[0]+=1
            if calls[0]==1:return []
            if calls[0] in (2,3):return [first]
            if calls[0]==4:return [second]
            watcher.stop();return []
        watcher=USBWatcher(reader=reader,interval=.005);added=QSignalSpy(watcher.device_connected);removed=QSignalSpy(watcher.device_disconnected)
        watcher.start()
        try:
            for _ in range(100):
                QTest.qWait(10)
                if not watcher.isRunning():break
            self.assertEqual(added.count(),2);self.assertEqual(removed.count(),1)
        finally:watcher.stop();watcher.wait(1000)


class USBImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.base=Path(self.temp.name)
        self.source=self.base/'usb';self.source.mkdir();self.target=self.base/'pictures';self.database=self.base/'profile'/'imports.sqlite3'
        self.drive=USBDrive('generated-usb','Family photos',str(self.source));self.validator=lambda drive:None
    def file(self,name,content):
        path=self.source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content);return path
    def run_import(self,**kwargs):return import_usb(self.drive,self.target,self.database,validator=self.validator,**kwargs)
    def test_pictures_go_straight_to_library_and_videos_to_shared_videos(self):
        photo=self.file('old albums/nested/a.jpg',b'generated picture');video=self.file('DCIM/clip.mp4',b'generated video')
        report=self.run_import(include_videos=True)
        self.assertEqual((report['copied'],report['videos_copied']),(2,1));self.assertFalse(report['errors'])
        self.assertEqual((self.target/'a.jpg').read_bytes(),photo.read_bytes());self.assertEqual((self.target/'Videos'/'clip.mp4').read_bytes(),video.read_bytes())
        self.assertFalse((self.target/self.drive.name).exists());self.assertEqual(report['cleanup_candidates'],[]);self.assertFalse(cleanup_available(report))
        report['cleanup_candidates']=[dict(source_pidl=['00'])];self.assertFalse(cleanup_available(report))
        self.assertEqual(set(path.name for path in self.target.iterdir()),{'a.jpg','Videos'})
        self.assertTrue(photo.exists());self.assertTrue(video.exists())
    def test_video_opt_in_and_thumbnail_system_folder_exclusions(self):
        self.file('root.jpg',b'root image');self.file('videos/clip.mp4',b'video');self.file('.thumbnails/cache.jpg',b'cache')
        self.file('$RECYCLE.BIN/old.jpg',b'recycled');self.file('System Volume Information/private.jpg',b'system')
        entries,errors=inventory_usb(self.drive,validator=self.validator)
        self.assertEqual([entry['path'] for entry in entries],['root.jpg']);self.assertFalse(errors)
        report=self.run_import();self.assertEqual(report['copied'],1);self.assertFalse((self.target/'Videos').exists())
    def test_all_usb_folders_are_scanned_without_the_android_phone_allowlist(self):
        pictures=['root.jpg','Old albums/2008/deep/family.jpg','Android/app media/saved.jpg','Music/covers/cover.png','.hidden album/holiday.jpg']
        video='Arbitrary folder/lectures/clip.mp4'
        for name in pictures+[video]:self.file(name,('generated content: '+name).encode())
        photos,errors=inventory_usb(self.drive,validator=self.validator)
        self.assertEqual({entry['path'] for entry in photos},set(pictures));self.assertFalse(errors)
        media,errors=inventory_usb(self.drive,include_videos=True,validator=self.validator)
        self.assertEqual({entry['path'] for entry in media},set(pictures+[video]));self.assertFalse(errors)
        report=self.run_import(include_videos=True)
        self.assertEqual(report['copied'],6);self.assertFalse(report['errors'])
        self.assertEqual({path.name for path in self.target.glob('*') if path.is_file()},{Path(name).name for name in pictures})
        self.assertTrue((self.target/'Videos'/'clip.mp4').exists())
    def test_existing_content_anywhere_in_library_is_not_copied_again(self):
        source=self.file('album.jpg',b'already saved bytes');existing=self.target/'Pixel'/'2024'/'renamed.jpg';existing.parent.mkdir(parents=True);existing.write_bytes(source.read_bytes())
        report=self.run_import();self.assertEqual((report['copied'],report['skipped']),(0,1));self.assertFalse((self.target/'album.jpg').exists())
        self.database.unlink();self.assertEqual(self.run_import()['skipped'],1)
    def test_name_collisions_preserve_both_distinct_pictures_and_source(self):
        source=self.file('same.jpg',b'USB contents');existing=self.target/'same.jpg';existing.parent.mkdir();existing.write_bytes(b'existing PC contents')
        report=self.run_import();self.assertEqual(report['copied'],1);self.assertEqual(existing.read_bytes(),b'existing PC contents')
        self.assertEqual(len(list(self.target.glob('*.jpg'))),2);self.assertEqual(source.read_bytes(),b'USB contents')
    def test_identical_pictures_in_different_usb_folders_copy_once(self):
        self.file('one/a.jpg',b'identical bytes');self.file('two/b.jpg',b'identical bytes')
        report=self.run_import();self.assertEqual((report['copied'],report['skipped'],report['duplicate_source_files']),(1,1,1))
    def test_destination_cannot_overlap_usb_source_before_any_writes(self):
        self.file('a.jpg',b'original');originals=set(self.source.iterdir())
        for target in (self.source,self.source/'new folder',self.base):
            with self.subTest(target=target):
                with self.assertRaisesRegex(ValueError,'outside the USB'):import_usb(self.drive,target,self.database,validator=self.validator)
        self.assertEqual(set(self.source.iterdir()),originals);self.assertFalse(self.database.parent.exists())
    def test_removed_or_replaced_drive_stops_without_copying(self):
        self.file('a.jpg',b'original')
        def gone(drive):raise OSError('USB drive was removed or replaced')
        with self.assertRaisesRegex(OSError,'removed or replaced'):import_usb(self.drive,self.target,self.database,validator=gone)
        self.assertFalse(self.target.exists());self.assertFalse(self.database.exists())
    def test_source_must_remain_inside_selected_drive(self):
        outside=self.base/'outside.jpg';outside.write_bytes(b'outside')
        with self.assertRaisesRegex(ValueError,'outside the selected drive'):
            with usb_source(str(outside),self.drive,None,self.validator):self.fail('Outside source must not open')
    def test_cancel_checksum_phase_copies_nothing(self):
        self.file('a.jpg',b'original');cancel=threading.Event()
        def progress(message):
            if message.startswith('Checking USB checksums · 1 of'):cancel.set()
        report=self.run_import(cancel=cancel,progress=progress)
        self.assertTrue(report['cancelled']);self.assertEqual(report['copied'],0);self.assertFalse(list(self.target.glob('*.jpg')))
    def test_source_changes_between_checksum_and_copy_are_rejected(self):
        source=self.file('a.jpg',b'original');changed=[False]
        def validator(drive):
            if changed[0]:source.write_bytes(b'modified')
        def progress(message):
            if message.startswith('Import plan · '):changed[0]=True
        report=import_usb(self.drive,self.target,self.database,validator=validator,progress=progress)
        self.assertEqual(report['copied'],0);self.assertTrue(report['errors']);self.assertFalse(list(self.target.glob('*.jpg')))
    def test_junctions_and_symbolic_links_never_open_other_folders(self):
        self.file('a.jpg',b'original');outside=self.base/'outside';outside.mkdir();(outside/'private.jpg').write_bytes(b'outside')
        import os
        real_scandir=os.scandir
        class Link:
            name='linked folder';path=str(outside)
            def __init__(self,reparse):self.reparse=reparse
            def stat(self,follow_symlinks=False):return SimpleNamespace(st_file_attributes=0x400 if self.reparse else 0)
            def is_symlink(self):return not self.reparse
        @contextmanager
        def scandir(path):
            self.assertNotEqual(Path(path),outside,'Linked targets must not be opened')
            with real_scandir(path) as items:yield [*items,Link(True),Link(False)]
        with patch('photo_review.usb_import.os.scandir',side_effect=scandir):entries,errors=inventory_usb(self.drive,validator=self.validator)
        self.assertEqual([entry['path'] for entry in entries],['a.jpg']);self.assertFalse(errors)
    def test_access_denied_folder_is_reported_while_other_pictures_import(self):
        self.file('a.jpg',b'original');self.file('denied/private.jpg',b'private')
        import os
        real_scandir=os.scandir
        def scandir(path):
            if Path(path)==self.source/'denied':raise PermissionError('fixture access denied')
            return real_scandir(path)
        with patch('photo_review.usb_import.os.scandir',side_effect=scandir):report=self.run_import()
        self.assertEqual(report['copied'],1);self.assertEqual(len(report['errors']),1);self.assertIn('denied',report['errors'][0]['path'])
        self.assertFalse((self.target/'private.jpg').exists())
    def test_cancellation_mid_copy_does_not_publish_a_partial_picture(self):
        source=self.file('large.jpg',b'x'*(3*1024*1024));before=digest(source);cancel=threading.Event()
        def progress(message):
            if message.startswith('Copying USB file:'):cancel.set()
        with patch('photo_review.usb_import.monotonic',side_effect=count(0,.3)):
            report=self.run_import(cancel=cancel,progress=progress)
        self.assertTrue(report['cancelled']);self.assertEqual(report['copied'],0);self.assertFalse(list(self.target.glob('*.jpg')))
        self.assertFalse(list(self.target.glob('.phone-import-*')));self.assertEqual(digest(source),before)
