import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PIL import Image
from photo_review.core import scan,make_groups,save_draft,restore_draft,validate_plan
from photo_review.ui import Window,ReviewDialog,DownloadProgressDialog
from tools.media_fixture import make_video


class VideoReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.root=self.base/'Pictures'
        self.video=make_video(self.root/'Phone'/'Videos'/'clip.avi');Image.new('RGB',(80,60),'navy').save(self.root/'photo.jpg')
        self.windows=[]
    def tearDown(self):
        for window in self.windows:window.close()
        self.app.processEvents();self.temp.cleanup()
    def window(self):
        window=Window(preferences=self.base/'prefs.json');window.draft=self.base/'draft.json';window.review_lock=self.base/'REVIEW_ONLY';window.review_lock.touch()
        window.root=self.root;window.photos,errors=scan(self.root,self.base/'cache',include_videos=True);self.assertFalse(errors)
        window.groups=make_groups(window.photos);window.match_key=window.matching_key();self.windows.append(window)
        window.render();window.show();self.app.processEvents();return window
    def wait_for(self,predicate,timeout=6000):
        for _ in range(timeout//25):
            self.app.processEvents()
            if predicate():return
            QTest.qWait(25)
        self.fail('Timed out waiting for video playback')
    def select_video(self,window):
        window.set_mode('review');window.select_folder(window.folder_nodes[str(self.video.parent)])
        self.app.processEvents();return window.current.video_controls
    def test_video_scan_cache_never_decodes_or_matches_videos(self):
        photos,errors=scan(self.root,self.base/'cache');self.assertEqual(len(photos),1)
        with patch('photo_review.core.Image.open',wraps=Image.open) as opened:
            items,errors=scan(self.root,self.base/'cache',include_videos=True)
            self.assertFalse(errors);self.assertEqual(len(items),2);opened.assert_not_called()
            again,errors=scan(self.root,self.base/'cache',include_videos=True);opened.assert_not_called()
        video=next(p for p in again if p.is_video);self.assertIsNone(video.gray);self.assertTrue(video.keep)
        with patch('photo_review.core.similarity',side_effect=AssertionError('Video compared')):self.assertEqual(make_groups([video,video]),[])
        video.keep=False;draft=self.base/'draft.json';save_draft(draft,self.root,again,[],10,60,mode='review');video.keep=True
        restore_draft(draft,again,[]);self.assertEqual(validate_plan(again,[]),[video]);self.assertTrue(self.video.exists())
    def test_review_video_filter_list_preview_and_keep_choices(self):
        window=self.window();window.set_mode('review');self.app.processEvents()
        with patch.object(window,'start_scan',side_effect=AssertionError('Filtering should not rescan')):
            window.media_filter.setCurrentIndex(window.media_filter.findData('videos'));self.app.processEvents()
            self.assertEqual(window.group_list.count(),1);self.assertTrue(window.current_photo().is_video)
            self.assertFalse(window.group_list.item(0).icon().isNull());self.assertIn('1 video',window.library_count.text())
            controls=window.current.video_controls;self.assertTrue(controls.isVisible());self.wait_for(lambda:controls.player.duration()>0)
            window.keep.setChecked(False);video=window.current_photo()
            window.media_filter.setCurrentIndex(window.media_filter.findData('photos'));self.app.processEvents()
            self.assertFalse(window.current_photo().is_video);self.assertTrue(controls.player.source().isEmpty());self.assertFalse(video.keep)
            window.media_filter.setCurrentIndex(window.media_filter.findData('all'));self.app.processEvents()
            self.assertEqual(window.group_list.count(),2);self.assertFalse(video.keep)
            window.set_mode('compare');self.assertFalse(window.media_filter.isVisible())
    def test_review_tree_player_navigation_and_handle_release(self):
        window=self.window();self.assertNotIn(str(self.video.parent),window.folder_nodes)
        controls=self.select_video(window);self.assertIn(str(self.video.parent),window.folder_nodes)
        self.assertIs(controls.view,window.current.view);self.assertTrue(controls.isVisible());self.assertFalse(window.matching_controls.isVisible())
        self.wait_for(lambda:controls.player.duration()>0);self.assertGreaterEqual(controls.player.duration(),3500)
        self.assertEqual(controls.player.playbackState(),QMediaPlayer.PlaybackState.StoppedState)
        controls.play.click();self.wait_for(lambda:controls.player.position()>200)
        controls.play.click();self.assertEqual(controls.player.playbackState(),QMediaPlayer.PlaybackState.PausedState)
        controls.player.setPosition(2000);window.keep.setChecked(False);self.app.processEvents()
        self.assertFalse(window.current_photo().keep);self.assertIn('video',window.keep.text());self.assertGreaterEqual(controls.player.position(),1900)
        window.set_mode('compare');self.app.processEvents();self.assertNotIn(str(self.video.parent),window.folder_nodes)
        self.assertTrue(controls.player.source().isEmpty());self.assertEqual(window.folder_filter,None)
        renamed=self.video.with_name('renamed.avi');self.video.rename(renamed);renamed.rename(self.video)
    def test_final_review_video_keep_and_close_release(self):
        window=self.window();controls=self.select_video(window);self.wait_for(lambda:controls.player.duration()>0)
        window.keep.setChecked(False);window.review();self.app.processEvents();dialog=window.review_dialog
        dialog.table.setCurrentCell(0,0);self.app.processEvents();preview=dialog.preview_panel.video_controls
        self.assertIsNotNone(preview);self.wait_for(lambda:preview.player.duration()>0)
        self.assertIn('1 file',dialog.confirm.text());self.assertFalse(dialog.preview_keep.isChecked())
        dialog.preview_keep.setChecked(True);self.assertTrue(window.current_photo().keep);self.assertFalse(dialog.confirm.isEnabled())
        dialog.reject();self.assertTrue(preview.player.source().isEmpty())
        window.close();self.assertTrue(controls.player.source().isEmpty())
    def test_broken_video_is_reviewable_and_recycling_releases_players(self):
        self.video.write_bytes(b'unsupported-video-fixture');window=self.window();controls=self.select_video(window)
        self.wait_for(lambda:controls.player.error()!=QMediaPlayer.Error.NoError)
        self.assertIn('Unable to play',controls.message.text());window.keep.setChecked(False)
        dialog=ReviewDialog(window);dialog.show();dialog.table.setCurrentCell(0,0);self.app.processEvents()
        with patch('photo_review.ui.Worker.start'):
            dialog.apply()
            self.assertTrue(controls.player.source().isEmpty());self.assertTrue(dialog.preview_panel.video_controls.player.source().isEmpty())
        dialog.processing_dialog.finish();dialog.reject()
    def test_download_speed_uses_copied_bytes_and_elapsed_time(self):
        dialog=DownloadProgressDialog(None,threading.Event())
        with patch('photo_review.ui.monotonic',return_value=10):dialog.update_progress('Import plan · 2 new files to download · 10 already exist on PC')
        dialog.update_progress('Downloading generated-video.mp4')
        with patch('photo_review.ui.monotonic',return_value=12):dialog.update_progress('Transfer bytes · 8000000')
        self.assertIn('4.00 MB/s avg',dialog.speed.text());self.assertEqual(dialog.detail.text(),'Downloading generated-video.mp4');dialog.finish();self.assertFalse(dialog.speed_timer.isActive())
    def test_download_layout_unknown_total_and_status_cannot_hide_transfer_progress(self):
        dialog=DownloadProgressDialog(None,threading.Event());dialog.show();self.app.processEvents()
        self.assertTrue(dialog.speed.isVisible());dialog.update_counts(0,0)
        self.assertNotIn('0 / 0',dialog.counter.text());self.assertIn('not known',dialog.counter.text())
        dialog.update_progress('Finding phone media · 2,220 files found (2,184 pictures, 36 videos) · DCIM/Camera · nothing copied')
        self.assertIn('2,220',dialog.counter.text());self.assertEqual(dialog.detail.text(),'DCIM/Camera')
        dialog.update_progress('Import plan · 300 new files to download · 7,500 already exist on PC');dialog.update_counts(42,300)
        dialog.update_progress('Import status · Already exist on PC: 7,400 images / 100 videos · Newly downloaded: 21 images / 21 videos')
        dialog.update_progress('Copy phone video: DCIM/Camera/fixture.mp4');dialog.update_progress('Transfer bytes · 96000000')
        self.assertEqual(dialog.counter.text(),'Downloading: 42 / 300');self.assertEqual(dialog.percent.text(),'14%')
        self.assertEqual(dialog.meter.value(),42);self.assertIn('MB/s',dialog.speed.text());self.assertTrue(dialog.speed.isVisible())
        self.assertIn('7,400 images / 100 videos',dialog.existing_label.text());self.assertIn('21 images / 21 videos',dialog.stats.text())
        dialog.reject();dialog.update_counts(43,300);self.assertEqual(dialog.counter.text(),'Cancelling…');dialog.finish()
    def test_download_empty_queue_is_complete_without_zero_fraction(self):
        dialog=DownloadProgressDialog(None,threading.Event());dialog.update_progress('Import plan · 0 new files to download · 7,500 already exist on PC')
        dialog.update_counts(0,0);dialog.update_progress('No new files to download.')
        self.assertEqual(dialog.counter.text(),'No new files to download');self.assertEqual(dialog.meter.value(),1)
        self.assertFalse(dialog.speed_timer.isActive());dialog.finish()
