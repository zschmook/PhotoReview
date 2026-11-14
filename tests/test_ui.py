import os
from datetime import datetime
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from photo_review.core import scan,make_groups
from photo_review.ui import Window,ROOT,DATA,ReviewDialog,demo_paths

class ReviewUiTests(unittest.TestCase):
    def test_heic_full_size_preview_uses_decoder_without_windows_codec(self):
        from photo_review.heic import register_heif_opener
        from PIL import Image
        folder=Path(self.temp.name)/'heic';folder.mkdir();source=folder/'photo.HEIC'
        Image.new('RGB',(90,60),'teal').save(source,format='HEIF')
        before=source.read_bytes();photos,errors=scan(folder,Path(self.temp.name)/'heic-cache')
        self.assertFalse(errors);self.assertEqual(len(photos),1)
        view=self.window.current.view;view.load(photos[0]);view.actual()
        pixmap=view.scene.items()[0].pixmap()
        self.assertFalse(pixmap.isNull());self.assertEqual((pixmap.width(),pixmap.height()),(90,60))
        self.assertEqual(source.read_bytes(),before)
    def test_iphone_prompts_describe_all_exposed_folders_and_photo_permission(self):
        from PySide6.QtWidgets import QLabel
        from photo_review.phones import PortableDevice
        from photo_review.ui import PhoneReadyDialog,PhoneDownloadDialog
        device=PortableDevice('USB\\VID_05AC&PID_12A8\\fixture','Mom')
        ready=PhoneReadyDialog(self.window,device)
        options=PhoneDownloadDialog(self.window,device,self.window.root/'Mom')
        try:
            ready_text=' '.join(item.text() for item in ready.findChildren(QLabel))
            options_text=' '.join(item.text() for item in options.findChildren(QLabel))
            self.assertIn('allow photo access',ready_text);self.assertNotIn('select File transfer',ready_text)
            self.assertIn('all photo-accessible iPhone folders',options_text);self.assertNotIn('Search only: DCIM',options_text)
        finally:ready.close();options.close()
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.window=Window(preferences=Path(self.temp.name)/'preferences.json');self.window.draft=Path(self.temp.name)/'draft.json'
        self.window.review_lock=Path(self.temp.name)/'REVIEW_ONLY';self.window.review_lock.write_text('Isolated test lock')
        self.window.root=ROOT/'demo-assets';self.window.photos,_=scan(self.window.root,DATA,selected_paths=demo_paths())
        self.window.groups=make_groups(self.window.photos);self.window.match_key=self.window.matching_key();self.window.render();self.window.show();self.app.processEvents()
    def tearDown(self):self.window.close();self.app.processEvents()
    def test_uncheck_is_draft_only_and_skip_resets_group(self):
        original=self.window.current_group();paths=[p.path for p in original.photos]
        QTest.mouseClick(self.window.keep,Qt.MouseButton.LeftButton)
        self.assertFalse(original.photos[0].keep);self.assertTrue(all(Path(p).exists() for p in paths))
        self.assertTrue(self.window.draft.exists());self.window.skip_group()
        self.assertTrue(all(p.keep for p in original.photos));self.assertTrue(original.skipped)
    def test_compare_and_final_confirmation(self):
        self.window.tile_keep(2,False);self.window.compare_button.click();self.app.processEvents()
        self.assertTrue(self.window.reference.isVisible())
        dialog=ReviewDialog(self.window);dialog.show();self.app.processEvents()
        self.assertEqual(len(dialog.review_photos),1);self.assertFalse(dialog.accept_button.isEnabled())
        dialog.confirm.setChecked(True);self.assertTrue(dialog.accept_button.isEnabled())
        # Do not click acceptance: this test never recycles real sample photos.
        dialog.reject()
    def test_all_unchecked_can_finish_dry_run_with_savings(self):
        from PySide6.QtWidgets import QMessageBox
        group=self.window.current_group()
        for photo in group.photos:photo.keep=False
        with patch('photo_review.ui.QMessageBox.warning') as warning:self.window.review()
        warning.assert_not_called()
        dialog=self.window.review_dialog
        self.assertEqual(len(dialog.review_photos),len(group.photos))
        lock=self.window.review_lock
        self.assertTrue(lock.exists(), 'This UI test requires the active review-only lock')
        dialog.confirm.setChecked(True)
        with patch('photo_review.ui.QMessageBox.information') as popup, patch('photo_review.ui.apply_plan') as apply:
            dialog.apply();self.wait_for_recycle(dialog)
        popup.assert_called_once()
        self.assertIn('You would have cleared',popup.call_args.args[2])
        self.assertIn(f'{len(group.photos)} photos',popup.call_args.args[2])
        apply.assert_not_called()
        self.assertTrue(all(Path(photo.path).exists() for photo in group.photos))

    def test_back_next_navigate_groups_and_preserve_choices(self):
        first=self.window.current_group();first.photos[1].keep=False
        self.window.photo_index=2;self.window.render()
        self.assertFalse(self.window.prev.isEnabled())
        self.window.next.click();self.app.processEvents()
        self.assertEqual(self.window.group_index,1);self.assertEqual(self.window.photo_index,0)
        self.window.prev.click();self.app.processEvents()
        self.assertEqual(self.window.group_index,0);self.assertFalse(first.photos[1].keep)
        self.window.group_index=len(self.window.groups)-1;self.window.render()
        self.assertFalse(self.window.next.isEnabled())
        self.assertFalse(hasattr(self.window,'next_group'))
    def test_strip_arrows_scroll_without_selecting_a_photo(self):
        self.window.photo_index=2;self.window.render();self.app.processEvents()
        scrollbar=self.window.filmstrip.horizontalScrollBar()
        self.assertGreater(scrollbar.maximum(),0)
        self.window.thumb_right.click();self.app.processEvents()
        self.assertGreater(scrollbar.value(),0);self.assertEqual(self.window.photo_index,2)
        self.window.choose_photo(3);self.app.processEvents();self.app.processEvents()
        self.assertGreater(scrollbar.value(),0)
        before_left=scrollbar.value();self.window.thumb_left.click();self.app.processEvents()
        self.assertLess(scrollbar.value(),before_left);self.assertEqual(self.window.photo_index,3)

    def test_preview_icons_stop_at_image_boundaries(self):
        view=self.window.current.view
        self.assertFalse(view.image_back.isEnabled())
        view.image_next.click();self.app.processEvents()
        self.assertEqual(self.window.photo_index,1);self.assertEqual(self.window.group_index,0)
        self.window.choose_photo(len(self.window.current_group().photos)-1);self.app.processEvents()
        self.assertFalse(view.image_next.isEnabled())
        self.window.navigate_photo(1)
        self.assertEqual(self.window.group_index,0)
        view.image_back.click();self.app.processEvents()
        self.assertEqual(self.window.photo_index,len(self.window.current_group().photos)-2)
    def test_keyboard_images_and_sets_are_separate(self):
        self.window.activateWindow();self.window.current.view.setFocus();self.app.processEvents()
        QTest.keyClick(self.window.current.view,Qt.Key.Key_Right);self.app.processEvents()
        self.assertEqual((self.window.group_index,self.window.photo_index),(0,1))
        QTest.keyClick(self.window.current.view,Qt.Key.Key_Left);self.app.processEvents()
        self.assertEqual((self.window.group_index,self.window.photo_index),(0,0))
        QTest.keyClick(self.window.current.view,Qt.Key.Key_Down);self.app.processEvents()
        self.assertEqual((self.window.group_index,self.window.photo_index),(1,0))
        QTest.keyClick(self.window.current.view,Qt.Key.Key_Up);self.app.processEvents()
        self.assertEqual((self.window.group_index,self.window.photo_index),(0,0))

    def test_folder_tree_filters_without_losing_keep_selections(self):
        original=self.window.groups[0];original.photos[1].keep=False
        folder=original.photos[0].folder
        node=self.window.folder_nodes[folder];self.window.select_folder(node)
        self.assertEqual(self.window.group_list.count(),1)
        self.assertEqual(self.window.current_group(),original)
        self.assertFalse(self.window.prev.isEnabled());self.assertFalse(self.window.next.isEnabled())
        self.window.navigate_group(1);self.assertEqual(self.window.current_group(),original)
        root_node=self.window.folder_nodes[str(self.window.root)];self.window.select_folder(root_node)
        self.assertEqual(self.window.group_list.count(),3)
        self.assertFalse(original.photos[1].keep)
        self.assertEqual(self.window.folder_filter,None)
    def test_folder_filter_is_saved_in_draft(self):
        folder=self.window.groups[1].photos[0].folder
        self.window.select_folder(self.window.folder_nodes[folder])
        import json
        payload=json.loads(self.window.draft.read_text())
        self.assertEqual(payload['folder_filter'],folder)

    def test_first_start_asks_inline_without_dialog(self):
        self.window.preferences=Path(self.temp.name)/'preferences.json'
        with patch('photo_review.ui.QMessageBox') as dialog,patch.object(self.window,'start_scan') as scan,patch('photo_review.ui.QFileDialog.getExistingDirectory') as picker:
            self.window.startup_folder()
            self.assertTrue(self.window.start_panel.isVisible())
            self.assertEqual(self.window.pages.currentIndex(),0)
            self.assertFalse(self.window.menuBar().isVisible())
            self.assertFalse(self.window.group_list.isVisible())
            scan.assert_not_called();dialog.assert_not_called();picker.assert_not_called()
    def test_yes_uses_standard_pictures_and_no_opens_picker(self):
        with patch('photo_review.ui.QStandardPaths.writableLocation',return_value='C:/Pictures'),patch.object(self.window,'start_scan') as scan:
            self.window.use_pictures.click()
            scan.assert_called_once_with('C:/Pictures',remember=True)
        with patch('photo_review.ui.QFileDialog.getExistingDirectory',return_value='') as picker:
            self.window.browse_pictures.click()
            picker.assert_called_once()
    def test_remembered_folder_starts_without_prompt(self):
        import json
        self.window.preferences=Path(self.temp.name)/'preferences.json'
        self.window.preferences.write_text(json.dumps({'last_folder':'C:/chosen'}))
        with patch('photo_review.ui.QMessageBox') as dialog,patch.object(self.window,'start_scan') as scan:
            self.window.startup_folder()
            scan.assert_called_once_with('C:/chosen');dialog.assert_not_called()
    def test_folder_choice_persisted_after_successful_scan(self):
        import json
        self.window.preferences=Path(self.temp.name)/'preferences.json'
        self.window.pending_root=self.window.root;self.window.pending_selection=None;self.window.pending_range=None;self.window.pending_remember=True
        self.window.scanned((self.window.photos,self.window.errors,self.window.groups))
        self.assertEqual(json.loads(self.window.preferences.read_text())['last_folder'],str(self.window.root))

    def test_phone_option_persists_without_losing_last_folder(self):
        import json
        from photo_review.phones import PhoneWatcher,PortableDevice
        real=PhoneWatcher
        self.window.write_preferences(last_folder='C:/chosen')
        with patch('photo_review.phones.PhoneWatcher',side_effect=lambda parent:real(parent,reader=lambda:[],interval=.01)):
            self.window.phone_detection.setChecked(True)
            self.assertTrue(self.window.phone_action.isChecked())
            watcher=self.window.phone_watcher
            self.assertIsNotNone(watcher)
            self.window.phone_devices_changed([PortableDevice('test','Test phone')])
            self.assertIn('Test phone',self.window.phone_status.text())
            self.window.phone_action.setChecked(False)
            self.assertFalse(self.window.phone_detection.isChecked())
            self.assertEqual(self.window.phone_status.text(),'Phone detection off')
            watcher.devices_changed.emit([PortableDevice('stale','Disconnected phone')])
            self.assertEqual(self.window.phone_status.text(),'Phone detection off')
            self.assertTrue(watcher.wait(1000));self.app.processEvents()
        prefs=json.loads(self.window.preferences.read_text())
        self.assertFalse(prefs['phone_detection_enabled']);self.assertEqual(prefs['last_folder'],'C:/chosen')
    def test_phone_preference_restored_on_next_launch(self):
        import json
        from photo_review.phones import PhoneWatcher
        prefs=Path(self.temp.name)/'enabled.json';prefs.write_text(json.dumps({'phone_detection_enabled':True}))
        real=PhoneWatcher
        with patch('photo_review.phones.PhoneWatcher',side_effect=lambda parent:real(parent,reader=lambda:[],interval=.01)):
            window=Window(preferences=prefs);watcher=window.phone_watcher
            self.assertTrue(window.phone_detection.isChecked());self.assertIsNotNone(watcher)
            window.close();self.assertTrue(watcher.wait(1000));self.app.processEvents()

    def test_phone_download_prompt_uses_date_layout_and_is_once_per_connection(self):
        from photo_review.phones import PortableDevice
        phone=PortableDevice('test','Test phone');self.window.connected_phones=[phone]
        from PySide6.QtWidgets import QDialog
        with patch('photo_review.ui.PhoneReadyDialog') as ready,patch('photo_review.ui.PhoneDownloadDialog') as dialog,patch.object(self.window,'start_phone_download') as download:
            ready.return_value.exec.return_value=QDialog.DialogCode.Accepted
            dialog.return_value.exec.return_value=QDialog.DialogCode.Accepted
            edited=self.window.root/'Custom phone folder'
            dialog.return_value.destination.text.return_value=str(edited)
            dialog.return_value.layout_choice.currentData.return_value='day-tree'
            dialog.return_value.include_videos.isChecked.return_value=False
            self.window.offer_phone_download()
            download.assert_called_once_with(phone,edited,'day-tree',include_videos=False)
            self.assertEqual(dialog.call_args.args[3],'day-tree')
            self.assertEqual(dialog.call_args.args[2],self.window.root/'Test phone')
            self.window.offer_phone_download();self.assertEqual(dialog.call_count,1)
            ready.assert_called_once_with(self.window,phone)
            self.window.offered_phones.clear();self.window.offer_phone_download()
            self.assertEqual(dialog.call_args.args[2],edited)
            self.assertEqual(download.call_count,2)

    def test_two_same_model_phones_are_prompted_and_assigned_separate_folders(self):
        from photo_review.phones import PortableDevice
        from PySide6.QtWidgets import QDialog
        from unittest.mock import MagicMock
        first=PortableDevice('first-device','Same model');second=PortableDevice('second-device','Same model')
        self.window.connected_phones=[first,second];defaults=[]
        def prompt(parent,device,destination,layout,include_videos=False):
            defaults.append(destination);dialog=MagicMock()
            dialog.exec.return_value=QDialog.DialogCode.Accepted
            dialog.destination.text.return_value=str(destination)
            dialog.layout_choice.currentData.return_value=layout
            dialog.include_videos.isChecked.return_value=include_videos
            return dialog
        with patch('photo_review.ui.PhoneReadyDialog') as ready,patch('photo_review.ui.PhoneDownloadDialog',side_effect=prompt),patch.object(self.window,'start_phone_download') as download:
            ready.return_value.exec.return_value=QDialog.DialogCode.Accepted
            self.window.offer_phone_download()
            self.assertNotIn(second.identity,self.window.offered_phones)
            self.window.offer_phone_download()
        self.assertEqual(ready.call_count,2);self.assertEqual(download.call_count,2)
        self.assertNotEqual(defaults[0],defaults[1]);self.assertEqual(defaults[0],self.window.root/'Same model')
        self.assertEqual(len(self.window.read_preferences()['phone_imports']),2)

    def test_phone_readiness_must_be_confirmed_before_options_or_scanning(self):
        from photo_review.phones import PortableDevice
        from PySide6.QtWidgets import QDialog
        phone=PortableDevice('test','Test phone');self.window.connected_phones=[phone]
        with patch('photo_review.ui.PhoneReadyDialog') as ready,patch('photo_review.ui.PhoneDownloadDialog') as options,patch.object(self.window,'start_phone_download') as download:
            ready.return_value.exec.return_value=QDialog.DialogCode.Rejected
            self.window.offer_phone_download();options.assert_not_called();download.assert_not_called()
            self.assertFalse(self.window.phone_prompt_open)
            # A manual retry can re-open readiness without unplugging the phone.
            self.window.choose_phone_download();self.assertEqual(ready.call_count,2)

    def test_phone_ready_dialog_has_no_automatic_scan_or_timeout(self):
        from photo_review.ui import PhoneReadyDialog
        from photo_review.phones import PortableDevice
        from PySide6.QtCore import QTimer
        dialog=PhoneReadyDialog(self.window,PortableDevice('test','Test phone'));dialog.show();self.app.processEvents()
        with patch.object(self.window,'start_phone_download') as download:
            QTest.qWait(100);self.assertTrue(dialog.isVisible());download.assert_not_called()
            self.assertEqual(dialog.findChildren(QTimer),[])
            dialog.ok_button.click();self.assertFalse(dialog.isVisible())

    def test_usb_mode_reenumeration_during_readiness_does_not_prompt_twice(self):
        from photo_review.phones import PortableDevice
        from PySide6.QtWidgets import QDialog
        old=PortableDevice('USB\\VID_18D1&PID_4EE5\\SERIAL-A','Test phone');unlocked=PortableDevice('USB\\VID_18D1&PID_4EE1\\SERIAL-A','Test phone');self.window.connected_phones=[old]
        def configure():self.window.phone_devices_changed([unlocked]);return QDialog.DialogCode.Rejected
        with patch('photo_review.ui.PhoneReadyDialog') as ready,patch('photo_review.ui.PhoneDownloadDialog') as options:
            ready.return_value.exec.return_value=QDialog.DialogCode.Accepted;options.return_value.exec.side_effect=configure
            self.window.offer_phone_download();self.app.processEvents();self.window.offer_phone_download()
            self.assertEqual(ready.call_count,1);self.assertIn(unlocked.identity.casefold(),self.window.offered_phones)
    def test_phone_download_waits_for_folder_choice(self):
        from photo_review.phones import PortableDevice
        self.window.connected_phones=[PortableDevice('test','Test phone')];self.window.root=None
        with patch('photo_review.ui.QMessageBox') as dialog:
            self.window.offer_phone_download();dialog.assert_not_called()

    def test_usb_prompt_imports_directly_into_current_library_once_per_connection(self):
        from photo_review.usb_import import USBDrive
        from PySide6.QtWidgets import QDialog
        drive=USBDrive('generated-drive','Family photos (R:)','R:\\');self.window.connected_usb=[drive]
        with patch('photo_review.ui.USBImportDialog') as prompt,patch.object(self.window,'start_usb_import') as start:
            prompt.return_value.exec.return_value=QDialog.DialogCode.Accepted;prompt.return_value.include_videos.isChecked.return_value=True
            self.window.offer_usb_import();self.window.offer_usb_import()
            self.assertEqual(prompt.call_count,1);self.assertEqual(prompt.call_args.args[2],self.window.root)
            start.assert_called_once_with(drive,self.window.root,True)
            self.assertTrue(self.window.read_preferences()['usb_include_videos'])
            self.window.usb_devices_changed([]);self.window.usb_devices_changed([drive]);self.window.offer_usb_import()
            self.assertEqual(prompt.call_count,2)
    def test_usb_cancel_does_not_scan_and_folder_choice_or_other_prompt_is_respected(self):
        from photo_review.usb_import import USBDrive
        from PySide6.QtWidgets import QDialog
        self.window.connected_usb=[USBDrive('fixture','Photos','R:\\')];root=self.window.root;self.window.root=None
        with patch('photo_review.ui.USBImportDialog') as prompt,patch.object(self.window,'start_usb_import') as start:
            self.window.offer_usb_import();prompt.assert_not_called()
            self.window.root=root;self.window.phone_prompt_open=True;self.window.offer_usb_import();prompt.assert_not_called()
            self.window.phone_prompt_open=False;prompt.return_value.exec.return_value=QDialog.DialogCode.Rejected
            self.window.offer_usb_import();start.assert_not_called();self.assertEqual(prompt.call_count,1)
    def test_usb_disconnect_while_options_are_open_does_not_start_import(self):
        from photo_review.usb_import import USBDrive
        from PySide6.QtWidgets import QDialog
        self.window.connected_usb=[USBDrive('fixture','Photos','R:\\')]
        def disconnect():self.window.connected_usb=[];return QDialog.DialogCode.Accepted
        with patch('photo_review.ui.USBImportDialog') as prompt,patch.object(self.window,'start_usb_import') as start,patch('photo_review.ui.QMessageBox.warning') as warning:
            prompt.return_value.exec.side_effect=disconnect;self.window.offer_usb_import()
        start.assert_not_called();warning.assert_called_once()
    def test_usb_dialog_has_library_destination_without_device_or_date_folder_controls(self):
        from photo_review.ui import USBImportDialog
        from photo_review.usb_import import USBDrive
        from PySide6.QtWidgets import QComboBox
        dialog=USBImportDialog(self.window,USBDrive('fixture','My USB','R:\\'),self.window.root)
        self.assertEqual(dialog.destination_label.text(),str(self.window.root));self.assertEqual(dialog.findChildren(QComboBox),[])
        self.assertFalse(dialog.include_videos.isChecked());self.assertTrue(dialog.video_note.isHidden())
        dialog.include_videos.setChecked(True);self.assertIn(str(self.window.root/'Videos'),dialog.video_note.text());self.assertFalse(dialog.video_note.isHidden())
        self.assertEqual(dialog.import_button.text(),'Import pictures and videos');dialog.reject()
    def test_usb_detection_starts_explicitly_and_watcher_stops_when_disabled(self):
        from photo_review.usb_import import USBWatcher
        self.assertTrue(self.window.usb_action.isChecked());self.assertIsNone(self.window.usb_watcher)
        real=USBWatcher
        with patch('photo_review.usb_import.USBWatcher',side_effect=lambda parent:real(parent,reader=lambda:[],interval=.01)):
            self.window.start_usb_detection();watcher=self.window.usb_watcher;self.assertIsNotNone(watcher)
            self.window.usb_action.setChecked(False);self.assertTrue(watcher.wait(1000));self.app.processEvents()
        self.assertFalse(self.window.read_preferences()['usb_detection_enabled']);self.assertEqual(self.window.connected_usb,[])
    def test_usb_detection_preference_is_restored_without_enumerating_native_drives(self):
        import json
        prefs=Path(self.temp.name)/'usb-disabled.json';prefs.write_text(json.dumps({'usb_detection_enabled':False}))
        with patch('photo_review.usb_import.USBWatcher') as watcher:
            window=Window(preferences=prefs);window.start_usb_detection();watcher.assert_not_called()
            self.assertFalse(window.usb_action.isChecked());window.close();self.app.processEvents()
    def test_usb_completion_preserves_library_and_never_offers_phone_deletion(self):
        from photo_review.usb_import import USBDrive
        drive=USBDrive('fixture','Photos','R:\\');library=self.window.root
        report=dict(source_kind='usb',destination=str(library),total=2,copied=1,skipped=1,bytes=100,cancelled=False,errors=[],cleanup_candidates=[])
        profile=Path(self.temp.name)/'usb-profile'
        with patch('photo_review.ui.DATA',profile),patch('photo_review.usb_import.import_usb',return_value=report) as importer,patch('photo_review.ui.PhoneCleanupDialog') as cleanup,patch('photo_review.ui.QMessageBox.information') as popup,patch.object(self.window,'start_scan') as scan:
            self.window.start_usb_import(drive,library,True);self.wait_for_job();self.app.processEvents()
            self.assertEqual(importer.call_args.args[1],library);self.assertTrue(importer.call_args.args[3])
            cleanup.assert_not_called();scan.assert_called_once_with(library);self.assertTrue((profile/'usb-import-report.json').exists())
            self.assertIn('Already exist on PC: 1 image',popup.call_args.args[2]);self.assertIn('Newly downloaded: 1 image',popup.call_args.args[2])
    def test_usb_watcher_is_stopped_on_window_close(self):
        from photo_review.usb_import import USBWatcher
        real=USBWatcher
        with patch('photo_review.usb_import.USBWatcher',side_effect=lambda parent:real(parent,reader=lambda:[],interval=.01)):
            self.window.start_usb_detection();watcher=self.window.usb_watcher;self.window.close()
            self.assertTrue(watcher.wait(1000));self.app.processEvents()

    def test_phone_import_layout_has_simple_choices_and_live_example(self):
        from photo_review.ui import PhoneDownloadDialog
        from photo_review.phones import PortableDevice
        destination=self.window.root/'Pixel'
        dialog=PhoneDownloadDialog(self.window,PortableDevice('test','Pixel'),destination)
        self.assertEqual(dialog.layout_choice.count(),3);self.assertEqual(dialog.layout_choice.currentData(),'day-tree')
        self.assertIn(str(destination),dialog.example.text());self.assertIn(datetime.now().strftime('%Y/%m/%d').replace('/',os.sep),dialog.example.text())
        dialog.layout_choice.setCurrentIndex(2);self.assertIn(datetime.now().strftime('%Y-%m-%d'),dialog.example.text())
        self.assertFalse(dialog.destination.isReadOnly())
        edited=self.window.root/'Custom phone folder';dialog.destination.setText(str(edited))
        self.assertIn(str(edited),dialog.example.text())
        dialog.destination.clear();self.assertFalse(dialog.download.isEnabled())
        dialog.destination.setText(str(edited));self.assertTrue(dialog.download.isEnabled())
        from PySide6.QtWidgets import QPushButton
        self.assertFalse(any('Browse' in button.text() for button in dialog.findChildren(QPushButton)));dialog.reject()

    def test_optional_videos_show_their_separate_folder_and_no_comparison_note(self):
        from photo_review.ui import PhoneDownloadDialog
        from photo_review.phones import PortableDevice
        destination=self.window.root/'Pixel'
        dialog=PhoneDownloadDialog(self.window,PortableDevice('video-fixture','Pixel'),destination)
        self.assertFalse(dialog.include_videos.isChecked());self.assertTrue(dialog.video_example.isHidden())
        dialog.include_videos.setChecked(True)
        self.assertFalse(dialog.video_example.isHidden());self.assertIn(str(destination/'Videos'),dialog.video_example.text())
        self.assertIn('not included in photo comparison',dialog.video_example.text());self.assertIn('videos',dialog.download.text())
        moved=self.window.root/'Custom phone';dialog.destination.setText(str(moved))
        self.assertIn(str(moved/'Videos'),dialog.video_example.text());dialog.reject()

    def test_video_import_choice_is_remembered_and_passed_to_worker(self):
        from photo_review.phones import PortableDevice
        from PySide6.QtWidgets import QDialog
        phone=PortableDevice('video-phone','Pixel');self.window.connected_phones=[phone]
        destination=self.window.root/'Pixel'
        with patch('photo_review.ui.PhoneReadyDialog') as ready,patch('photo_review.ui.PhoneDownloadDialog') as dialog,patch.object(self.window,'start_phone_download') as download:
            ready.return_value.exec.return_value=QDialog.DialogCode.Accepted;dialog.return_value.exec.return_value=QDialog.DialogCode.Accepted
            dialog.return_value.destination.text.return_value=str(destination);dialog.return_value.layout_choice.currentData.return_value='day-tree';dialog.return_value.include_videos.isChecked.return_value=True
            self.window.offer_phone_download();download.assert_called_once_with(phone,destination,'day-tree',include_videos=True)
            self.assertTrue(self.window.read_preferences()['phone_imports'][phone.identity]['include_videos'])
            self.window.offered_phones.clear();self.window.offer_phone_download()
            self.assertTrue(dialog.call_args.kwargs['include_videos'])

    def test_video_download_flag_reaches_background_downloader(self):
        from photo_review.phones import PortableDevice
        device=PortableDevice('video-fixture','Pixel');destination=self.window.root/'Pixel'
        report=dict(destination=str(destination),total=1,video_total=1,copied=1,skipped=0,bytes=100,cancelled=False,errors=[])
        def download(*args,**kwargs):self.assertTrue(kwargs['include_videos']);return report
        profile=Path(self.temp.name)/'video-profile'
        with patch('photo_review.ui.DATA',profile),patch('photo_review.phone_download.download_phone',side_effect=download),patch('photo_review.ui.QMessageBox.information'),patch.object(self.window,'start_scan'):
            self.window.start_phone_download(device,destination,include_videos=True);self.wait_for_job();self.app.processEvents()

    def test_phone_import_destination_stays_editable_after_invalid_path(self):
        from photo_review.ui import PhoneDownloadDialog
        from photo_review.phones import PortableDevice
        from PySide6.QtWidgets import QDialog
        dialog=PhoneDownloadDialog(self.window,PortableDevice('test','Pixel'),self.window.root/'Pixel')
        dialog.show();self.app.processEvents()
        with patch('photo_review.ui.QMessageBox.warning') as warning:
            dialog.destination.setText(str(Path(self.temp.name)/'outside'))
            dialog.download.click();warning.assert_called_once();self.assertTrue(dialog.isVisible())
        dialog.destination.setText(str(self.window.root/'Edited phone folder'));dialog.download.click()
        self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted);self.assertFalse(dialog.isVisible())

    def test_phone_import_dialog_rejects_folder_assigned_to_another_phone(self):
        from photo_review.ui import PhoneDownloadDialog
        from photo_review.phones import PortableDevice
        from photo_review.phone_folders import remember_phone_destination
        first=PortableDevice('first','Same model');second=PortableDevice('second','Same model')
        chosen=self.window.root/'First phone'
        settings=remember_phone_destination({},first,self.window.root,chosen,'day-tree')
        self.window.write_preferences(phone_imports=settings)
        dialog=PhoneDownloadDialog(self.window,second,chosen);dialog.show();self.app.processEvents()
        with patch('photo_review.ui.QMessageBox.warning') as warning:
            dialog.download.click();warning.assert_called_once();self.assertTrue(dialog.isVisible())
            self.assertIn('another phone',warning.call_args.args[2])
        dialog.reject()

    def test_cancelled_phone_import_saves_report_and_reviews_verified_copies(self):
        from photo_review.phones import PortableDevice
        library=Path(self.temp.name)/'library';library.mkdir();destination=library/'Phone'/'Pictures';destination.mkdir(parents=True);profile=Path(self.temp.name)/'import-profile'
        self.window.root=library;self.window.photos=[];self.window.groups=[];self.window.render();self.window.write_preferences(last_folder=str(library))
        report=dict(copied=1,skipped=0,bytes=100,cancelled=True,errors=[],destination=str(destination))
        def download(*args,**kwargs):self.assertEqual(kwargs['working_root'],library);self.window.cancel.set();return report
        with patch('photo_review.ui.DATA',profile),patch('photo_review.phone_download.download_phone',side_effect=download),patch('photo_review.ui.QMessageBox.information') as popup,patch.object(self.window,'start_scan') as scan:
            self.window.start_phone_download(PortableDevice('test','Phone'),destination);self.wait_for_job();self.app.processEvents()
            self.assertTrue((profile/'phone-import-report.json').exists());popup.assert_called_once();scan.assert_called_once_with(library)
            self.assertEqual(self.window.root,library);self.assertEqual(self.window.read_preferences()['last_folder'],str(library))

    def test_stale_phone_destination_is_ignored_and_menu_changes_the_working_root(self):
        from photo_review.phones import PortableDevice
        from PySide6.QtWidgets import QDialog
        phone=PortableDevice('phone','Pixel');self.window.connected_phones=[phone]
        self.window.write_preferences(phone_imports={'phone':{'destination':str(Path(self.temp.name)/'outside'),'layout':'month-tree'}})
        first=self.window.root;other=Path(self.temp.name)/'new-library';other.mkdir()
        with patch('photo_review.ui.PhoneReadyDialog') as ready,patch('photo_review.ui.PhoneDownloadDialog') as options,patch.object(self.window,'start_phone_download') as download:
            ready.return_value.exec.return_value=QDialog.DialogCode.Accepted;options.return_value.exec.return_value=QDialog.DialogCode.Accepted
            options.return_value.layout_choice.currentData.return_value='month-tree'
            options.return_value.include_videos.isChecked.return_value=False
            options.return_value.destination.text.side_effect=lambda:str(self.window.root/'Pixel')
            self.window.offer_phone_download();download.assert_called_once_with(phone,first/'Pixel','month-tree',include_videos=False)
            self.assertNotIn('destination',self.window.read_preferences()['phone_imports']['phone'])
            with patch('photo_review.ui.QFileDialog.getExistingDirectory',return_value=str(other)),patch.object(self.window,'start_scan') as scan:
                self.window.choose_folder();scan.assert_called_once_with(str(other),remember=True)
            self.window.root=other;self.window.offered_phones.clear();self.window.offer_phone_download()
            self.assertEqual(download.call_args.args[1],other/'Pixel')

    def test_ui_rejects_phone_destination_outside_library_without_starting_job(self):
        from photo_review.phones import PortableDevice
        root=self.window.root
        with patch.object(self.window,'run_job') as job,patch('photo_review.ui.QMessageBox.warning') as warning:
            self.window.start_phone_download(PortableDevice('phone','Pixel'),Path(self.temp.name)/'outside')
            job.assert_not_called();warning.assert_called_once();self.assertEqual(self.window.root,root)

    def test_stopped_phone_import_shows_actual_error_in_warning(self):
        report=dict(copied=0,skipped=0,bytes=0,cancelled=False,errors=[dict(path='DCIM/one.jpg',error='fixture copy failure')],stopped='Stopped after three consecutive copy errors.')
        with patch('photo_review.ui.QMessageBox.warning') as warning,patch('photo_review.ui.QMessageBox.information') as success,patch.object(self.window,'start_scan'):
            self.window.finish_phone_download(report,self.window.root)
        success.assert_not_called();warning.assert_called_once()
        self.assertEqual(warning.call_args.args[1],'Phone import stopped')
        self.assertIn('DCIM/one.jpg',warning.call_args.args[2]);self.assertIn('fixture copy failure',warning.call_args.args[2])

    def phone_cleanup_fixture(self):
        from photo_review.phones import PortableDevice
        device=PortableDevice('fixture-device','Test phone')
        candidate=dict(device_id=device.identity,phone_path='DCIM/Camera/fixture.jpg',local_path=self.window.photos[0].path,source_pidl=['00'],size=100,sha256='0'*64)
        report=dict(destination=str(self.window.root),total=1,copied=1,skipped=0,bytes=100,cancelled=False,errors=[],cleanup_candidates=[candidate],cleanup_protected=0)
        return device,report

    def test_phone_cleanup_confirmation_defaults_to_keep_and_requires_checkbox(self):
        from photo_review.ui import PhoneCleanupDialog
        from PySide6.QtWidgets import QDialog,QLabel
        device,report=self.phone_cleanup_fixture()
        dialog=PhoneCleanupDialog(self.window,device,report);dialog.show();self.app.processEvents()
        self.assertFalse(dialog.confirm.isChecked());self.assertFalse(dialog.verify_button.isEnabled());self.assertTrue(dialog.keep_button.isDefault())
        self.assertEqual(dialog.table.rowCount(),1);self.assertIn('fixture.jpg',dialog.table.item(0,0).text())
        self.assertTrue(any('may be permanent' in item.text() for item in dialog.findChildren(QLabel)))
        dialog.accept();self.assertTrue(dialog.isVisible())
        dialog.keep_button.click();self.assertEqual(dialog.result(),QDialog.DialogCode.Rejected)
        dialog=PhoneCleanupDialog(self.window,device,report);dialog.confirm.setChecked(True);dialog.verify_button.click()
        self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted)

    def test_phone_summary_distinguishes_existing_media_from_new_downloads(self):
        from photo_review.ui import PhoneCleanupDialog
        from PySide6.QtWidgets import QLabel
        device,report=self.phone_cleanup_fixture();report.update(copied=300,skipped=7500,total=7800,video_total=150,videos_skipped=100,videos_copied=50)
        dialog=PhoneCleanupDialog(self.window,device,report)
        text='\n'.join(item.text() for item in dialog.findChildren(QLabel))
        self.assertIn('Newly downloaded: 250 images / 50 videos',text)
        self.assertIn('Already exist on PC: 7,400 images / 100 videos',text)
        self.assertIn('All 7,800 saved files, including previous imports',text)
        self.assertNotIn('Already imported',text);dialog.reject()

    def test_phone_progress_separates_history_check_from_new_file_queue(self):
        import threading
        from photo_review.ui import DownloadProgressDialog
        dialog=DownloadProgressDialog(self.window,threading.Event())
        dialog.update_counts(0,7800);self.assertIn('Reading phone details',dialog.counter.text())
        dialog.update_progress('Checking phone checksums · 7,800 of 7,800 files · 7,500 already exist on PC')
        self.assertIn('7,800 of 7,800',dialog.counter.text())
        dialog.update_progress('Import plan · 300 new files to download · 7,500 already exist on PC')
        dialog.update_counts(1,300);self.assertEqual(dialog.counter.text(),'Downloading: 1 / 300')
        self.assertEqual(dialog.existing_label.text(),'Already on PC: 7,500 files')
        dialog.update_progress('Import status · Already exist on PC: 7,400 images / 100 videos · Newly downloaded: 1 image / 0 videos')
        self.assertEqual(dialog.counter.text(),'Downloading: 1 / 300')
        self.assertEqual(dialog.existing_label.text(),'Already on PC: 7,400 images / 100 videos')
        self.assertIn('1 image / 0 videos',dialog.stats.text())
        dialog.finish()

    def test_successful_download_offers_optional_phone_cleanup_before_review(self):
        from PySide6.QtWidgets import QDialog
        device,report=self.phone_cleanup_fixture()
        with patch('photo_review.ui.PhoneCleanupDialog') as prompt,patch.object(self.window,'start_phone_verification') as verify,patch.object(self.window,'start_phone_cleanup') as cleanup,patch.object(self.window,'start_scan') as scan,patch('photo_review.ui.QMessageBox.information') as popup:
            prompt.return_value.exec.return_value=QDialog.DialogCode.Rejected
            self.window.finish_phone_download(report,self.window.root,device)
            verify.assert_not_called();cleanup.assert_not_called();scan.assert_called_once_with(self.window.root);popup.assert_not_called()
            scan.reset_mock();prompt.return_value.exec.return_value=QDialog.DialogCode.Accepted
            self.window.finish_phone_download(report,self.window.root,device)
            verify.assert_called_once_with(device,report,self.window.root,confirmed=True);cleanup.assert_not_called();scan.assert_not_called()

    def test_cancelled_or_failed_download_never_offers_phone_cleanup(self):
        device,report=self.phone_cleanup_fixture()
        for change in (dict(cancelled=True),dict(errors=[dict(path='fixture.jpg',error='fixture failure')])):
            with self.subTest(change=change),patch('photo_review.ui.PhoneCleanupDialog') as prompt,patch('photo_review.ui.QMessageBox.information'),patch('photo_review.ui.QMessageBox.warning'),patch.object(self.window,'start_scan'):
                self.window.finish_phone_download(dict(report,**change),self.window.root,device);prompt.assert_not_called()

    def test_phone_cleanup_runs_in_worker_and_reports_partial_cancellation(self):
        import threading
        device,report=self.phone_cleanup_fixture();ui_thread=threading.get_ident()
        profile=Path(self.temp.name)/'phone-cleanup-profile'
        result=dict(deleted=1,bytes=100,cancelled=True,errors=[],files=['DCIM/Camera/fixture.jpg'])
        def cleanup(*args,**kwargs):
            self.assertNotEqual(threading.get_ident(),ui_thread);self.assertTrue(kwargs['confirmed'])
            self.window.cancel.set();return result
        with patch('photo_review.ui.DATA',profile),patch('photo_review.phone_cleanup.cleanup_phone',side_effect=cleanup),patch('photo_review.ui.QMessageBox.information') as popup,patch.object(self.window,'start_scan') as scan:
            self.window.start_phone_cleanup(device,report,self.window.root,confirmed=True,verification={'generated':'mocked verification result'})
            self.wait_for_job();self.app.processEvents()
            self.assertTrue((profile/'phone-cleanup-report.json').exists());popup.assert_called_once()
            self.assertEqual(popup.call_args.args[1],'Phone cleanup cancelled');scan.assert_called_once_with(self.window.root)
        self.assertTrue(all(Path(photo.path).exists() for photo in self.window.photos))

    def test_phone_cleanup_cannot_start_without_confirmation(self):
        device,report=self.phone_cleanup_fixture()
        with patch.object(self.window,'run_job') as job:
            self.window.start_phone_cleanup(device,report,self.window.root);job.assert_not_called()
            self.window.start_phone_cleanup(device,report,self.window.root,confirmed=True);job.assert_not_called()
            self.window.start_phone_verification(device,report,self.window.root);job.assert_not_called()

    def phone_verification_fixture(self,device,report):
        from photo_review.phone_cleanup import verification_plan
        return dict(mode='verification',plan=verification_plan(device,report,self.window.root),total=report['total'],verified=report['total'],cancelled=False,errors=[])

    def test_phone_final_confirmation_defaults_to_cancel_and_shows_exact_target(self):
        from photo_review.ui import PhoneCleanupFinalDialog
        device,report=self.phone_cleanup_fixture();verification=self.phone_verification_fixture(device,report)
        dialog=PhoneCleanupFinalDialog(self.window,device,report,verification);dialog.show();self.app.processEvents()
        self.assertIs(dialog.defaultButton(),dialog.cancel_button);self.assertIs(dialog.escapeButton(),dialog.cancel_button)
        self.assertIn('1 picture',dialog.text());self.assertIn(device.name,dialog.text())
        self.assertIn('checksums match',dialog.informativeText());self.assertIn('permanent',dialog.informativeText())
        dialog.cancel_button.click();self.assertIs(dialog.clickedButton(),dialog.cancel_button)

    def test_final_phone_deletion_prompt_only_follows_complete_checksum_pass(self):
        device,report=self.phone_cleanup_fixture();verification=self.phone_verification_fixture(device,report)
        with patch('photo_review.ui.PhoneCleanupFinalDialog') as final,patch.object(self.window,'start_phone_cleanup') as cleanup,patch.object(self.window,'start_scan') as scan:
            final.return_value.clickedButton.return_value=final.return_value.cancel_button
            self.window.finish_phone_verification(device,report,self.window.root,verification)
            cleanup.assert_not_called();scan.assert_called_once_with(self.window.root)
            scan.reset_mock();final.return_value.clickedButton.return_value=final.return_value.delete_button
            self.window.finish_phone_verification(device,report,self.window.root,verification)
            cleanup.assert_called_once_with(device,report,self.window.root,confirmed=True,verification=verification);scan.assert_not_called()

    def test_failed_or_cancelled_checksum_pass_never_shows_final_delete_prompt(self):
        device,report=self.phone_cleanup_fixture();verification=self.phone_verification_fixture(device,report)
        for change in (dict(cancelled=True),dict(verified=0),dict(errors=[dict(path='fixture.jpg',error='checksum mismatch')])):
            with self.subTest(change=change),patch('photo_review.ui.PhoneCleanupFinalDialog') as final,patch.object(self.window,'start_phone_cleanup') as cleanup,patch.object(self.window,'start_scan'),patch('photo_review.ui.QMessageBox.warning'),patch('photo_review.ui.QMessageBox.information'):
                self.window.finish_phone_verification(device,report,self.window.root,dict(verification,**change))
                final.assert_not_called();cleanup.assert_not_called()

    def test_phone_full_checksum_pass_runs_in_worker_before_final_prompt(self):
        import threading
        device,report=self.phone_cleanup_fixture();verification=self.phone_verification_fixture(device,report)
        profile=Path(self.temp.name)/'verification-profile';ui_thread=threading.get_ident()
        def verify(*args,**kwargs):self.assertNotEqual(threading.get_ident(),ui_thread);self.assertTrue(kwargs['confirmed']);return verification
        with patch('photo_review.ui.DATA',profile),patch('photo_review.phone_cleanup.verify_phone_cleanup',side_effect=verify),patch.object(self.window,'finish_phone_verification') as finished,patch.object(self.window,'start_phone_cleanup') as cleanup:
            self.window.start_phone_verification(device,report,self.window.root,confirmed=True)
            self.wait_for_job();self.app.processEvents()
            finished.assert_called_once_with(device,report,self.window.root,verification)
            cleanup.assert_not_called();self.assertTrue((profile/'phone-verification-report.json').exists())

    def test_live_acceptance_requires_confirmation_and_queues_recycling(self):
        self.window.review_lock=Path(self.temp.name)/'no-lock'
        self.window.current_group().photos[0].keep=False
        dialog=ReviewDialog(self.window)
        self.assertFalse(dialog.test_mode);self.assertIn('Accept',dialog.accept_button.text());self.assertFalse(dialog.accept_button.isEnabled())
        dialog.confirm.setChecked(True);self.assertTrue(dialog.accept_button.isEnabled())
        with patch('photo_review.ui.Worker') as worker,patch('photo_review.ui.apply_plan') as apply:
            dialog.apply();worker.assert_called_once();worker.return_value.start.assert_called_once();apply.assert_not_called()
            worker.return_value.isRunning.return_value=False
        dialog.reject()

    def test_recycle_processing_stays_responsive_through_validation_and_refresh(self):
        import threading
        ui_thread=threading.get_ident();thread_ids=[];validation=threading.Event();moving=threading.Event();refresh=threading.Event()
        self.window.review_lock=Path(self.temp.name)/'no-lock';selected=self.window.current_group().photos[:2]
        for photo in selected:photo.keep=False
        old=self.window.photos;dialog=ReviewDialog(self.window);dialog.show();dialog.confirm.setChecked(True)
        def apply(photos,groups,journal,progress,validation_progress):
            thread_ids.append(threading.get_ident());validation_progress(1,len(photos));validation.wait(2)
            progress(0,2,None);progress(1,2,selected[0]);moving.wait(2);progress(2,2,selected[1])
            return [dict(identity=p.identity,status='recycled') for p in selected]
        real_groups=make_groups
        def matches(photos,*args,**kwargs):
            thread_ids.append(threading.get_ident());refresh.wait(2);return real_groups(photos,*args,**kwargs)
        try:
            with patch('photo_review.ui.apply_plan',side_effect=apply),patch('photo_review.ui.make_groups',side_effect=matches):
                dialog.apply();self.app.processEvents();progress=dialog.processing_dialog
                self.assertEqual(dialog.accept_button.text(),'Accept && Recycle');self.assertTrue(progress.isVisible())
                self.assertEqual(progress.windowModality(),Qt.WindowModality.ApplicationModal)
                QTest.qWait(50);angle=progress.spinner.angle;QTest.qWait(80)
                self.assertNotEqual(progress.spinner.angle,angle);self.assertFalse(dialog.back_button.isEnabled())
                self.assertIn('Checking ',progress.counter.text());self.assertIs(self.window.photos,old)
                progress.reject();self.assertTrue(progress.isVisible())
                validation.set()
                for _ in range(80):
                    QTest.qWait(10)
                    if progress.counter.text()=='Recycling 1 of 2 files':break
                self.assertEqual(progress.counter.text(),'Recycling 1 of 2 files');moving.set()
                for _ in range(80):
                    QTest.qWait(10)
                    if progress.counter.text().startswith('Updating '):break
                self.assertTrue(progress.counter.text().startswith('Updating '));self.assertTrue(progress.isVisible())
                refresh.set();self.wait_for_recycle(dialog)
        finally:
            validation.set();moving.set();refresh.set()
            if hasattr(dialog,'worker') and dialog.worker.isRunning():self.wait_for_recycle(dialog)
        self.assertTrue(all(identity!=ui_thread for identity in thread_ids))
        self.assertEqual(len(thread_ids),2)
        self.assertEqual(len(self.window.photos),len(old)-2);self.assertTrue(dialog.success_dialog.isVisible())
        self.assertEqual(dialog.success_dialog.text(),'Success!')
        self.assertIs(dialog.success_dialog.defaultButton(),dialog.keep_sorting_button)
        self.assertIs(dialog.success_dialog.escapeButton(),dialog.keep_sorting_button)
        dialog.keep_sorting_button.click();self.app.processEvents()
        self.assertFalse(dialog.isVisible());self.assertTrue(self.window.isVisible())
        self.assertTrue(all(Path(p.path).exists() for p in old),'UI recycler was mocked; fixture photos must be untouched')

    def test_success_close_app_requires_explicit_button(self):
        self.window.review_lock=Path(self.temp.name)/'no-lock';photo=self.window.current_group().photos[0];photo.keep=False
        dialog=ReviewDialog(self.window);dialog.show();dialog.confirm.setChecked(True)
        def apply(photos,groups,journal,progress,validation_progress):
            progress(1,1,photo);return [dict(identity=photo.identity,status='recycled')]
        with patch('photo_review.ui.apply_plan',side_effect=apply),patch.object(self.window,'close') as close:
            dialog.apply();self.wait_for_recycle(dialog);close.assert_not_called()
            dialog.close_app_button.click();self.app.processEvents();close.assert_called_once()
        self.assertTrue(Path(photo.path).exists())

    def test_recycle_partial_failure_releases_modal_and_preserves_remaining_selections(self):
        self.window.review_lock=Path(self.temp.name)/'no-lock';selected=self.window.current_group().photos[:2]
        for photo in selected:photo.keep=False
        old=self.window.photos;dialog=ReviewDialog(self.window);dialog.show();dialog.confirm.setChecked(True)
        def apply(photos,groups,journal,progress,validation_progress):
            progress(1,2,selected[0]);raise OSError('Second file unavailable')
        with patch('photo_review.ui.apply_plan',side_effect=apply):
            dialog.apply();self.wait_for_recycle(dialog)
        self.assertIsNone(dialog.processing_dialog);self.assertTrue(dialog.back_button.isEnabled())
        self.assertFalse(dialog.accept_button.isEnabled());self.assertFalse(hasattr(dialog,'success_dialog'))
        self.assertIn('recycling 1 photo:',dialog.notice.text());self.assertIn('Second file unavailable',dialog.notice.text())
        self.assertNotIn(selected[0].identity,{p.identity for p in self.window.photos})
        self.assertIn(selected[1].identity,{p.identity for p in self.window.photos});self.assertFalse(selected[1].keep)
        self.assertTrue(all(Path(p.path).exists() for p in old));dialog.reject()

    def test_recycle_validation_failure_keeps_library_and_shows_error(self):
        self.window.review_lock=Path(self.temp.name)/'no-lock';self.window.current_group().photos[0].keep=False
        old=self.window.photos;dialog=ReviewDialog(self.window);dialog.confirm.setChecked(True)
        with patch('photo_review.ui.apply_plan',side_effect=ValueError('Picture changed; rescan')):
            dialog.apply();self.wait_for_recycle(dialog)
        self.assertIs(self.window.photos,old);self.assertIsNone(dialog.processing_dialog)
        self.assertIn('Picture changed; rescan',dialog.notice.text());self.assertTrue(dialog.back_button.isEnabled());dialog.reject()
    def test_open_dry_run_dialog_cannot_silently_become_live(self):
        self.window.current_group().photos[0].keep=False
        dialog=ReviewDialog(self.window)
        self.window.review_lock=Path(self.temp.name)/'no-lock'
        with patch('photo_review.ui.QMessageBox.information') as popup,patch('photo_review.ui.apply_plan') as apply:
            dialog.apply();self.wait_for_recycle(dialog);popup.assert_called_once();apply.assert_not_called()

    def test_welcome_to_review_centers_and_fits_available_screen(self):
        from PySide6.QtCore import QRect
        from types import SimpleNamespace
        available=QRect(0,0,1920,1080)
        screen=SimpleNamespace(availableGeometry=lambda:available)
        with patch.object(self.window,'screen',return_value=screen):
            self.window.show_welcome();self.window.move(1800,900);self.window.show_review();self.app.processEvents()
            self.assertTrue(available.contains(self.window.frameGeometry()))
            position=self.window.pos();self.window.render();self.assertEqual(self.window.pos(),position)
    def test_review_minimum_respects_smaller_work_area(self):
        from PySide6.QtCore import QRect
        from types import SimpleNamespace
        available=QRect(0,0,1366,728)
        with patch.object(self.window,'screen',return_value=SimpleNamespace(availableGeometry=lambda:available)):
            self.window.show_welcome();self.window.show_review();self.app.processEvents()
            self.assertLessEqual(self.window.minimumWidth(),available.width())
            self.assertLessEqual(self.window.minimumHeight(),available.height())
            self.assertTrue(available.contains(self.window.frameGeometry()))

    def test_review_preview_is_below_selected_row_and_switches_to_correct_photo(self):
        for photo in self.window.groups[0].photos[:3]:photo.keep=False
        dialog=ReviewDialog(self.window);dialog.show();self.app.processEvents()
        for photo in (dialog.review_photos[1],dialog.review_photos[2],dialog.review_photos[0]):
            dialog.table.setCurrentCell(dialog.photo_row(photo.identity),0);self.app.processEvents()
            self.assertIs(dialog.preview_panel.view.photo,photo)
            self.assertEqual(dialog.preview_row,dialog.photo_row(photo.identity)+1)
            self.assertEqual(dialog.table.columnSpan(dialog.preview_row,0),4)
            self.assertEqual(dialog.table.rowCount(),len(dialog.review_photos)+1)
            self.assertFalse(dialog.preview_keep.isChecked())
        dialog.reject()
    def test_review_keep_is_consistent_saved_and_requires_new_confirmation(self):
        import json
        photo=self.window.groups[0].photos[0];photo.keep=False
        dialog=ReviewDialog(self.window);dialog.show();self.app.processEvents();dialog.table.setCurrentCell(dialog.photo_row(photo.identity),0);self.app.processEvents()
        dialog.confirm.setChecked(True);self.assertTrue(dialog.accept_button.isEnabled());self.assertTrue(dialog.preview_keep.isEnabled())
        QTest.mouseClick(dialog.preview_keep,Qt.MouseButton.LeftButton)
        self.assertTrue(photo.keep);self.assertFalse(dialog.confirm.isChecked());self.assertFalse(dialog.confirm.isEnabled());self.assertFalse(dialog.accept_button.isEnabled())
        self.assertEqual(dialog.summary_values['RECYCLE'].text(),'0 photos')
        self.assertEqual(dialog.table.item(dialog.photo_row(photo.identity),3).text(),'Keep')
        self.assertTrue(json.loads(self.window.draft.read_text())['decisions'][photo.identity])
        dialog.preview_keep.setChecked(False)
        self.assertFalse(photo.keep);self.assertTrue(dialog.confirm.isEnabled());self.assertFalse(dialog.accept_button.isEnabled())
        self.assertEqual(dialog.table.item(dialog.photo_row(photo.identity),3).text(),'Recycle')
        self.assertEqual(dialog.summary_values['RECYCLE'].text(),'1 photo')
        self.assertTrue(Path(photo.path).exists());dialog.reject()
    def test_review_rescued_photo_is_excluded_from_recycle_plan(self):
        from photo_review.core import validate_plan
        for photo in self.window.groups[0].photos[:3]:photo.keep=False
        dialog=ReviewDialog(self.window);rescued=dialog.review_photos[1]
        dialog.table.setCurrentCell(dialog.photo_row(rescued.identity),0);dialog.preview_keep.setChecked(True)
        other=dialog.review_photos[2];dialog.table.setCurrentCell(dialog.photo_row(other.identity),0)
        dialog.table.setCurrentCell(dialog.photo_row(rescued.identity),0)
        self.assertTrue(dialog.preview_keep.isChecked())
        selected=validate_plan(self.window.photos,self.window.groups)
        self.assertEqual(len(selected),2);self.assertNotIn(rescued.identity,{p.identity for p in selected});dialog.reject()
    def test_review_keyboard_skips_expanded_preview_row(self):
        for photo in self.window.groups[0].photos[:3]:photo.keep=False
        dialog=ReviewDialog(self.window);dialog.show();self.app.processEvents()
        dialog.table.setCurrentCell(dialog.photo_row(dialog.review_photos[0].identity),0);dialog.table.setFocus()
        QTest.keyClick(dialog.table,Qt.Key.Key_Down);self.app.processEvents()
        self.assertIs(dialog.preview_photo,dialog.review_photos[1])
        QTest.keyClick(dialog.table,Qt.Key.Key_Up);self.app.processEvents()
        self.assertIs(dialog.preview_photo,dialog.review_photos[0]);dialog.reject()

    def test_clear_view_when_no_groups(self):
        self.window.groups=[];self.window.render()
        self.assertEqual(self.window.current.view.scene.items(),[])
        self.assertFalse(self.window.keep.isEnabled());self.assertEqual(self.window.filename.text(),'No photo selected')

    def test_review_mode_switch_is_immediate_and_includes_unmatched_photos(self):
        matched={p.identity for group in self.window.groups for p in group.photos}
        unique=next(p for p in self.window.photos if p.identity not in matched)
        groups=self.window.groups
        with patch.object(self.window,'run_job') as job,patch('photo_review.ui.similarity',side_effect=AssertionError('Review must not compare pictures')):
            self.window.review_radio.click();self.app.processEvents()
            self.assertEqual(self.window.mode,'review');self.assertEqual(len(self.window.current_group().photos),len(self.window.photos))
            self.assertFalse(self.window.matching_controls.isVisible());self.assertFalse(self.window.next.isVisible())
            self.assertFalse(self.window.compare_button.isVisible());self.assertFalse(self.window.fields['similarity'].isVisible())
            index=next(i for i,p in enumerate(self.window.current_group().photos) if p is unique)
            self.window.choose_photo(index);self.window.keep.click()
            self.assertFalse(unique.keep);self.assertIn(unique, self.window.current_group().photos)
            self.window.choose_photo(len(self.window.current_group().photos)-1);self.window.navigate_photo(1)
            self.assertFalse(self.window.current.view.image_next.isEnabled())
            self.window.compare_radio.click();self.app.processEvents()
            self.assertIs(self.window.groups,groups);self.assertFalse(unique.keep);job.assert_not_called()
        self.assertTrue(self.window.matching_controls.isVisible())
        dialog=ReviewDialog(self.window);self.assertIn(unique.identity,dialog.photos_by_id);dialog.reject()

    def test_review_folder_filter_and_thumbnail_keep_share_decisions(self):
        self.window.review_radio.click();self.app.processEvents()
        folder=self.window.photos[0].folder;self.window.select_folder(self.window.folder_nodes[folder])
        photos=self.window.current_group().photos
        self.assertTrue(all(p.folder==folder for p in photos));self.assertEqual(self.window.group_list.count(),len(photos))
        self.window.thumbnail_model.setData(self.window.thumbnail_model.index(1),Qt.CheckState.Unchecked,Qt.ItemDataRole.CheckStateRole)
        self.assertFalse(photos[1].keep);self.assertFalse(self.window.keep.isChecked())
        self.assertIn('Recycle',self.window.group_list.item(1).text())
        import json
        payload=json.loads(self.window.draft.read_text());self.assertEqual(payload['mode'],'review');self.assertFalse(payload['decisions'][photos[1].identity])
        self.window.select_folder(self.window.folder_nodes[str(self.window.root)])
        self.assertFalse(photos[1].keep);self.assertEqual(len(self.window.current_group().photos),len(self.window.photos))

    def test_review_thumbnail_footer_click_toggles_keep(self):
        from PySide6.QtCore import QPoint
        self.window.review_radio.click();self.app.processEvents();self.app.processEvents()
        view=self.window.review_thumbnails;index=self.window.thumbnail_model.index(self.window.photo_index)
        photo=self.window.current_photo();rect=view.visualRect(index)
        QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(rect.left()+20,rect.bottom()-15));self.app.processEvents()
        self.assertFalse(photo.keep);self.assertFalse(self.window.keep.isChecked())
        QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(rect.left()+20,rect.bottom()-15));self.app.processEvents()
        self.assertTrue(photo.keep);self.assertTrue(self.window.keep.isChecked())

    def test_review_thousands_of_thumbnails_use_virtual_cells(self):
        from dataclasses import replace
        self.window.photos=[replace(self.window.photos[0],identity=f'fixture-{i}',timestamp=i) for i in range(5000)]
        self.window.groups=[];self.window.review_radio.click();self.app.processEvents()
        self.assertEqual(self.window.thumbnail_model.rowCount(),5000)
        self.assertLess(len(self.window.review_thumbnails.findChildren(type(self.window.keep))),10)
        self.window.choose_photo(4999);self.app.processEvents();self.app.processEvents()
        self.assertEqual(self.window.review_thumbnails.currentIndex().row(),4999)
        self.assertFalse(self.window.current.view.image_next.isEnabled())

    def wait_for_job(self):
        import time
        for _ in range(300):
            self.app.processEvents()
            if self.window.busy_dialog is None:return
            # Let Python QThreads run while the main thread pumps Qt events.
            time.sleep(.01)
        self.fail('Background job did not finish')

    def wait_for_recycle(self,dialog):
        import time
        for _ in range(300):
            self.app.processEvents()
            if not dialog.worker.isRunning() and dialog.processing_dialog is None:return
            # Yield Python's GIL as well as pumping Qt events: matching runs in
            # a Python QThread, and a tight sequence of Qt waits can starve it.
            time.sleep(.01)
        self.fail(f'Recycle worker did not finish: running={dialog.worker.isRunning()}, error={dialog.processing_error!r}, result={dialog.processing_result is not None}, phase={dialog.processing_dialog.counter.text() if dialog.processing_dialog else None}')

    def test_scanning_modal_locks_gui_and_reports_real_counts(self):
        import threading
        release=threading.Event();results=[]
        def job(progress,counts):
            counts(2,5);progress('Reading test picture')
            release.wait(2);return 'complete'
        try:
            self.window.run_job(job,results.append,counted=True)
            for _ in range(50):
                QTest.qWait(10)
                if '2 of 5' in self.window.busy_dialog.counter.text():break
            dialog=self.window.busy_dialog
            self.assertTrue(dialog.isVisible());self.assertEqual(dialog.windowModality(),Qt.WindowModality.ApplicationModal)
            self.assertEqual(dialog.counter.text(),'Scanning 2 of 5 pictures');self.assertTrue(dialog.spinner.timer.isActive())
            self.assertFalse(self.window.pages.isEnabled());self.assertFalse(self.window.menuBar().isEnabled())
            self.window.review_radio.click();self.assertEqual(self.window.mode,'compare')
        finally:release.set();self.wait_for_job()
        self.assertEqual(results,['complete']);self.assertTrue(self.window.pages.isEnabled());self.assertTrue(self.window.menuBar().isEnabled())

    def test_phone_discovery_delivers_chunk_counts_before_worker_finishes(self):
        import threading
        import time
        from photo_review.ui import DownloadProgressDialog
        release=threading.Event();results=[]
        def job(progress):
            progress('Finding phone pictures · 32 pictures found · DCIM/Camera · nothing copied')
            release.wait(2)
            progress('Finding phone pictures · 64 pictures found · DCIM/Camera · nothing copied')
            return 'complete'
        try:
            self.window.run_job(job,results.append,dialog_class=DownloadProgressDialog)
            for _ in range(100):
                self.app.processEvents()
                if self.window.busy_dialog.counter.text()=='32 pictures found':break
                time.sleep(.01)
            dialog=self.window.busy_dialog
            self.assertEqual(dialog.counter.text(),'32 pictures found')
            self.assertIn('DCIM/Camera',dialog.detail.text())
            self.assertTrue(self.window.worker.isRunning());self.assertTrue(dialog.meter.isVisible())
            self.assertEqual((dialog.meter.minimum(),dialog.meter.maximum()),(0,0))
            self.assertFalse(results)
        finally:release.set();self.wait_for_job()
        self.assertEqual(results,['complete']);self.assertTrue(self.window.pages.isEnabled())

    def test_cancelled_scan_keeps_loaded_library_and_unlocks(self):
        import threading
        release=threading.Event();old=self.window.photos;results=[]
        def job(progress):
            release.wait(2)
            if self.window.cancel.is_set():raise InterruptedError('Scan cancelled')
        with patch('photo_review.ui.QMessageBox.warning') as warning:
            self.window.run_job(job,results.append)
            self.window.busy_dialog.reject();self.assertTrue(self.window.cancel.is_set())
            self.assertTrue(self.window.busy_dialog.isVisible());self.assertFalse(self.window.pages.isEnabled())
            release.set();self.wait_for_job();warning.assert_not_called()
        self.assertIs(self.window.photos,old);self.assertFalse(results);self.assertTrue(self.window.pages.isEnabled())

    def test_review_scan_skips_matching_and_remembers_mode(self):
        from PIL import Image
        source=Path(self.temp.name)/'new-pictures';source.mkdir()
        for i in range(3):Image.new('RGB',(30,30),(i*80,0,0)).save(source/f'{i}.png')
        self.window.review_radio.click()
        with patch('photo_review.ui.DATA',Path(self.temp.name)/'cache'),patch('photo_review.ui.make_groups',side_effect=AssertionError('Review scan must skip comparison')):
            self.window.start_scan(source);self.wait_for_job()
        self.assertEqual(len(self.window.photos),3);self.assertEqual(len(self.window.current_group().photos),3);self.assertEqual(self.window.groups,[])
        window=Window(preferences=self.window.preferences)
        try:self.assertEqual(window.mode,'review');self.assertTrue(window.review_radio.isChecked())
        finally:window.close()

    def test_compare_after_review_only_scan_computes_once_then_switches_instantly(self):
        from photo_review.core import make_groups
        self.window.review_radio.click();self.window.groups=[];self.window.match_key=None
        with patch('photo_review.ui.make_groups',wraps=make_groups) as match:
            self.window.compare_radio.click();self.assertIsNotNone(self.window.busy_dialog);self.wait_for_job()
            self.assertEqual(match.call_count,1);self.assertGreater(len(self.window.groups),0)
            self.window.review_radio.click();self.window.compare_radio.click()
            self.assertEqual(match.call_count,1);self.assertIsNone(self.window.busy_dialog)

    def test_failed_scan_releases_modal_and_preserves_loaded_photos(self):
        old=self.window.photos
        def job(progress):raise OSError('Unreadable test folder')
        with patch('photo_review.ui.QMessageBox.warning') as warning:
            self.window.run_job(job,lambda result:self.fail('Failed job returned a result'));self.wait_for_job()
            warning.assert_called_once();self.assertIn('Unreadable test folder',warning.call_args.args[2])
        self.assertTrue(self.window.pages.isEnabled());self.assertIsNone(self.window.busy_dialog);self.assertIs(self.window.photos,old)

    def test_closed_window_cannot_start_a_delayed_regroup(self):
        self.window.regroup_timer.start();self.window.close()
        self.assertFalse(self.window.regroup_timer.isActive())
        with patch('photo_review.ui.Worker') as worker:
            self.window.regroup();self.window.run_job(lambda progress:None,lambda result:None)
        worker.assert_not_called()

    def test_review_resume_restores_mode_photo_and_keep_choices(self):
        from dataclasses import replace
        self.window.review_radio.click();self.window.choose_photo(3);photo=self.window.current_photo();self.window.keep.click()
        saved=Path(self.temp.name)/'saved-review.json';saved.write_text(self.window.draft.read_text())
        self.window.compare_radio.click()
        with patch.object(self.window,'start_scan') as scan:
            self.window.resume(str(saved));scan.assert_called_once()
        self.window.pending_root=self.window.root;self.window.pending_selection=None;self.window.pending_range=None
        self.window.scanned(([replace(p,keep=True) for p in self.window.photos],[],[]))
        self.assertEqual(self.window.mode,'review');self.assertEqual(self.window.photo_index,3)
        self.assertEqual(self.window.current_photo().identity,photo.identity);self.assertFalse(self.window.current_photo().keep)

    def test_folders_and_picture_list_share_one_vertical_sidebar(self):
        self.assertEqual(self.window.sidebar_splitter.orientation(),Qt.Orientation.Vertical)
        self.assertTrue(self.window.sidebar_splitter.widget(0).isAncestorOf(self.window.folder_tree))
        self.assertTrue(self.window.sidebar_splitter.widget(1).isAncestorOf(self.window.group_list))

if __name__=='__main__':unittest.main()
