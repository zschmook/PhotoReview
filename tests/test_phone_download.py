import os,sqlite3,tempfile,threading,unittest
from contextlib import closing
from unittest.mock import patch
from datetime import datetime
from pathlib import Path
from PIL import Image
from photo_review.phones import PortableDevice
from photo_review.phone_download import import_entries,default_destination,safe_name,digest,copy_shell_file,download_phone,destination_inside_root

class PhoneDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.base=Path(self.temp.name)
        self.phone=self.base/'phone';self.phone.mkdir();self.output=self.base/'output';self.db=self.base/'profile'/'imports.sqlite3'
        self.device=PortableDevice('fixture-device','Pixel Test');self.entries=[]
    def picture(self,name='one.jpg',taken='2024:02:03 12:30:00',data=None):
        source=self.phone/name;source.parent.mkdir(parents=True,exist_ok=True)
        if data is not None:source.write_bytes(data)
        else:
            exif=Image.Exif()
            exif[270]=name
            if taken:exif[36867]=taken
            Image.new('RGB',(40,30),'#4589bd').save(source,exif=exif)
        entry=dict(path='Internal storage/DCIM/'+name,size=source.stat().st_size,source=str(source),modified=datetime(2025,6,7,12).timestamp(),taken=None)
        self.entries.append(entry);return source,entry
    def copy(self,source,target,cancel):Path(target).write_bytes(Path(source).read_bytes())
    def read(self,source,cancel):return Path(source).stat().st_size,digest(source)
    def run_import(self,**kwargs):return import_entries(self.entries,self.device,self.output,self.db,copier=self.copy,reader=self.read,**kwargs)
    def cached_phone_fixture(self):
        source,entry=self.picture();entry['source']=[b'test-phone',b'test-stable-object'];reads=[];copies=[]
        def reader(identifier,cancel):reads.append(identifier);return source.stat().st_size,digest(source)
        def copier(identifier,target,cancel):copies.append(identifier);target.write_bytes(source.read_bytes())
        def run(device=None):return import_entries(self.entries,device or self.device,self.output,self.db,reader=reader,copier=copier)
        return source,entry,reads,copies,run
    def test_repeat_native_phone_import_reuses_pc_index_and_still_has_cleanup_checks(self):
        source,entry,reads,copies,run=self.cached_phone_fixture();first=run();second=run()
        self.assertEqual((len(reads),len(copies)),(1,1));self.assertEqual((second['copied'],second['skipped'],second['cached_sources']),(0,1,1))
        self.assertEqual(len(second['cleanup_candidates']),1);self.assertEqual(second['cleanup_candidates'][0]['sha256'],digest(source))
        self.assertEqual(second['checksum_algorithm'],'SHA-256')
    def test_repeat_index_invalidates_phone_metadata_device_and_object_changes(self):
        source,entry,reads,copies,run=self.cached_phone_fixture();run()
        entry['modified']+=1;run();self.assertEqual(len(reads),2)
        entry['source']=[b'test-phone',b'different-object'];run();self.assertEqual(len(reads),3)
        run(PortableDevice('different-device','Pixel Test'));self.assertEqual(len(reads),4)
        entry['modified']=None;run();run();self.assertEqual(len(reads),6)
        self.assertEqual(len(copies),1)
    def test_repeat_index_cannot_skip_missing_or_modified_local_backups(self):
        source,entry,reads,copies,run=self.cached_phone_fixture();first=run();target=Path(first['files'][0]['path'])
        target.write_bytes(b'x'*source.stat().st_size);second=run()
        self.assertEqual((second['copied'],second['cached_sources']),(1,0));self.assertEqual(len(reads),2)
        Path(second['files'][0]['path']).unlink();third=run()
        self.assertEqual((third['copied'],third['cached_sources']),(1,0));self.assertEqual(len(reads),3)
    def test_reset_repeat_index_returns_to_fresh_phone_checksums(self):
        source,entry,reads,copies,run=self.cached_phone_fixture();run();self.db.unlink();second=run()
        self.assertEqual((second['copied'],second['skipped'],second['cached_sources']),(0,1,0));self.assertEqual(len(reads),2)
    def test_byte_progress_copy_uses_read_only_stream_and_exclusive_output(self):
        from types import SimpleNamespace
        from io import BytesIO
        payload=b'generated-video-data'*200000;stream=BytesIO(payload);stage=self.base/'staged.mp4';seen=[]
        item=SimpleNamespace(BindToHandler=lambda *args:SimpleNamespace(Read=stream.read))
        with patch('photo_review.phone_download.phone_shell_item',return_value=item):
            copy_shell_file([b'generated-identifier'],stage,progress=seen.append)
            self.assertEqual(stage.read_bytes(),payload);self.assertEqual(seen[-1],len(payload))
            with self.assertRaises(FileExistsError):copy_shell_file([b'generated-identifier'],stage,progress=seen.append)
        self.assertEqual(stage.read_bytes(),payload)
    def test_default_path_and_exif_date_tree(self):
        source,entry=self.picture();before=digest(source);report=self.run_import()
        target=self.output/'2024'/'02'/'03'/'one.jpg'
        self.assertEqual(default_destination(self.device,self.base),self.base/'Pixel Test')
        self.assertTrue(target.is_file());self.assertEqual(digest(target),before);self.assertEqual(digest(source),before)
        self.assertEqual(report['copied'],1);self.assertEqual(report['files'][0]['date_source'],'EXIF date taken')
    def test_layout_choices_and_modified_fallback(self):
        self.picture(taken=None)
        report=self.run_import(layout='month-tree');self.assertTrue((self.output/'2025'/'06'/'one.jpg').exists())
        self.output=self.base/'other-output';report=self.run_import(layout='day-folder')
        self.assertTrue((self.output/'2025-06-07'/'one.jpg').exists());self.assertEqual(report['files'][0]['date_source'],'Phone modified time')

    def test_video_uses_video_subfolder_metadata_date_and_never_image_decoding(self):
        source=self.phone/'clip.MP4';source.write_bytes(b'generated video bytes, not an image')
        self.entries=[dict(path='Internal storage/DCIM/Camera/clip.MP4',source=str(source),size=source.stat().st_size,taken=datetime(2025,4,5,12).timestamp(),modified=None)]
        with patch('photo_review.phone_download.Image.open',side_effect=AssertionError('Videos must not be decoded as pictures')):
            report=self.run_import(layout='month-tree')
            from photo_review.core import scan
            photos,errors=scan(self.output,self.base/'review-cache')
        target=self.output/'Videos'/'2025'/'04'/'clip.MP4'
        self.assertEqual(target.read_bytes(),source.read_bytes());self.assertEqual(report['videos_copied'],1)
        self.assertEqual(report['video_total'],1);self.assertEqual(report['picture_total'],0);self.assertEqual(report['unsupported_review'],0)
        self.assertEqual(report['files'][0]['date_source'],'Phone video date');self.assertEqual(photos,[]);self.assertEqual(errors,[])
        repeated=self.run_import(layout='month-tree');self.assertEqual(repeated['videos_skipped'],1);self.assertEqual(repeated['copied'],0)
    def test_verified_import_is_skipped_and_missing_copy_is_downloaded_again(self):
        source,entry=self.picture();first=self.run_import();second=self.run_import()
        self.assertEqual(second['skipped'],1);self.assertEqual(second['copied'],0)
        Path(first['files'][0]['path']).unlink();third=self.run_import();self.assertEqual(third['copied'],1)
        self.assertTrue(source.exists())
    def test_existing_renamed_pc_picture_is_skipped_without_import_history(self):
        source,entry=self.picture();existing=self.output/'old folder'/'renamed.jpg'
        existing.parent.mkdir(parents=True);existing.write_bytes(source.read_bytes());reads=[]
        def reader(source,cancel):reads.append(source);return self.read(source,cancel)
        report=import_entries(self.entries,self.device,self.output,self.db,reader=reader,copier=lambda *args:self.fail('A checksum match must not be downloaded'))
        self.assertEqual((report['copied'],report['skipped'],report['new_total']),(0,1,0))
        self.assertEqual(reads,[str(source)]);self.assertEqual(list(self.output.rglob('*.jpg')),[existing]);self.assertFalse(report['errors'])
    def test_normal_import_uses_native_phone_checksum_before_skipping_manual_copy(self):
        source,entry=self.picture();entry['source']=[b'generated-phone-identifier'];existing=self.output/'manually copied.jpg'
        existing.parent.mkdir();existing.write_bytes(source.read_bytes())
        with patch('photo_review.phone_cleanup.phone_digest',return_value=(entry['size'],digest(source))) as native:
            report=import_entries(self.entries,self.device,self.output,self.db,copier=lambda *args:self.fail('Matching file must not be copied'))
        self.assertEqual(native.call_args.args[:2],(entry['source'],None));self.assertEqual((report['copied'],report['skipped']),(0,1))
        self.assertEqual(report['cleanup_candidates'][0]['local_path'],str(existing));self.assertFalse(report['errors'])
    def test_reset_history_and_renamed_local_copy_do_not_cause_another_download(self):
        self.picture();first=self.run_import();old=Path(first['files'][0]['path']);renamed=old.with_name('manually renamed.jpg');old.rename(renamed)
        self.db.unlink()
        with patch.object(self,'copy',side_effect=AssertionError('History is not required for duplicate detection')):report=self.run_import()
        self.assertEqual((report['copied'],report['skipped']),(0,1));self.assertTrue(renamed.exists())
    def test_phone_content_changes_are_found_even_when_size_and_timestamps_do_not(self):
        source,entry=self.picture(data=b'old bytes');first=self.run_import();source.write_bytes(b'new bytes')
        report=self.run_import();self.assertEqual((report['copied'],report['skipped']),(1,0))
        self.assertEqual(Path(first['files'][0]['path']).read_bytes(),b'old bytes')
        self.assertEqual(Path(report['files'][0]['path']).read_bytes(),b'new bytes')
    def test_existing_video_is_content_matched_under_a_different_name_and_date(self):
        source,entry=self.picture('clip.MP4',data=b'generated video fixture');existing=self.output/'Videos'/'2020'/'different name.mp4'
        existing.parent.mkdir(parents=True);existing.write_bytes(source.read_bytes())
        with patch.object(self,'copy',side_effect=AssertionError('Existing video must be skipped')):report=self.run_import()
        self.assertEqual((report['copied'],report['videos_skipped'],report['skipped']),(0,1,1));self.assertFalse(report['errors'])
    def test_all_phone_checksums_finish_before_copying_and_identical_sources_copy_once(self):
        self.picture('first.jpg',data=b'identical generated photo');self.picture('second.jpg',data=b'identical generated photo')
        reads=[];copies=[]
        def reader(source,cancel):reads.append(source);return self.read(source,cancel)
        def copier(source,target,cancel):
            self.assertEqual(len(reads),2);copies.append(source);self.copy(source,target,cancel)
        report=import_entries(self.entries,self.device,self.output,self.db,reader=reader,copier=copier)
        self.assertEqual((report['new_total'],report['copied'],report['skipped'],report['duplicate_phone_files']),(1,1,1,1))
        self.assertEqual(len(copies),1);self.assertEqual(len(list(self.output.rglob('*.jpg'))),1)
    def test_equal_size_and_name_with_different_content_is_not_a_duplicate(self):
        source,entry=self.picture(data=b'phone bytes');existing=self.output/'2025'/'06'/'07'/'one.jpg'
        existing.parent.mkdir(parents=True);existing.write_bytes(b'other bytes')
        report=self.run_import();self.assertEqual((report['copied'],report['skipped']),(1,0))
        self.assertEqual(existing.read_bytes(),b'other bytes');self.assertEqual(Path(report['files'][0]['path']).read_bytes(),b'phone bytes')
    def test_unreadable_phone_checksum_never_falls_back_to_copying(self):
        self.picture()
        def reader(*args):raise OSError('fixture phone read failed')
        report=import_entries(self.entries,self.device,self.output,self.db,reader=reader,copier=lambda *args:self.fail('No blind copy after checksum failure'))
        self.assertEqual(report['copied'],0);self.assertEqual(len(report['errors']),1);self.assertFalse(list(self.output.rglob('*.jpg')))
    def test_corrupt_copy_with_matching_size_is_not_published(self):
        source,entry=self.picture(data=b'correct bytes')
        report=import_entries(self.entries,self.device,self.output,self.db,reader=self.read,copier=lambda source,target,cancel:Path(target).write_bytes(b'corrupt bytes'))
        self.assertEqual(report['copied'],0);self.assertIn('checksum does not match',report['errors'][0]['error']);self.assertFalse(list(self.output.rglob('*.jpg')))
    def test_pc_file_edited_while_phone_is_hashed_is_not_trusted(self):
        source,entry=self.picture(data=b'phone bytes');existing=self.output/'existing.jpg';existing.parent.mkdir();existing.write_bytes(source.read_bytes())
        def reader(source,cancel):
            info=existing.stat();existing.write_bytes(b'other bytes');os.utime(existing,ns=(info.st_atime_ns,info.st_mtime_ns+2_000_000_000))
            return self.read(source,cancel)
        report=import_entries(self.entries,self.device,self.output,self.db,reader=reader,copier=self.copy)
        self.assertEqual((report['copied'],report['skipped']),(1,0));self.assertEqual(existing.read_bytes(),b'other bytes')
    def test_cancelling_local_checksum_stops_before_phone_reads_or_downloads(self):
        source,entry=self.picture();existing=self.output/'existing.jpg';existing.parent.mkdir();existing.write_bytes(source.read_bytes());cancel=threading.Event()
        def checksum(*args):cancel.set();raise InterruptedError('fixture cancellation')
        with patch('photo_review.phone_download.digest',side_effect=checksum),patch.object(self,'read') as reader:
            report=self.run_import(cancel=cancel)
        self.assertTrue(report['cancelled']);self.assertEqual(report['copied'],0);reader.assert_not_called();self.assertFalse(report['errors'])
    def test_size_filter_and_thumbnail_exclusion_avoid_unnecessary_local_hashes(self):
        source,entry=self.picture(data=b'generated photo');thumb=self.output/'.thumbnails'/'same.jpg';thumb.parent.mkdir(parents=True);thumb.write_bytes(source.read_bytes())
        unrelated=self.output/'unrelated.mp4';unrelated.write_bytes(b'larger unrelated video data')
        def checksum(path,*args):
            self.assertNotIn(Path(path),(thumb,unrelated));return digest(path)
        with patch('photo_review.phone_download.digest',side_effect=checksum):report=self.run_import()
        self.assertEqual((report['copied'],report['skipped']),(1,0))
    def test_returning_phone_compares_checksums_before_copying_and_queues_only_new_files(self):
        for index in range(12):self.picture(f'old-{index}.jpg')
        first=self.run_import();old_paths={row['path'] for row in first['files']}
        for index in range(3):self.picture(f'new-{index}.jpg')
        progress=[];counts=[];copies=[]
        def copy(source,target,cancel):
            self.assertTrue(any(message.startswith('Import plan · 3 new files to download · 12 already exist') for message in progress))
            copies.append(source);self.copy(source,target,cancel)
        def hash_new_only(path,*args):
            self.assertNotIn(str(path),old_paths,'Unchanged previous imports must not be rehashed')
            return digest(path)
        with patch('photo_review.phone_download.digest',side_effect=hash_new_only):
            report=import_entries(self.entries,self.device,self.output,self.db,copier=copy,reader=self.read,progress=progress.append,counts=lambda x,y:counts.append((x,y)))
        self.assertEqual((report['total'],report['checked'],report['new_total'],report['copied'],report['skipped']),(15,15,3,3,12))
        self.assertEqual(len(copies),3);self.assertTrue(all(Path(source).name.startswith('new-') for source in copies))
        self.assertEqual(counts,[(0,15),(0,3),(1,3),(2,3),(3,3)])
        self.assertTrue(all(Path(path).exists() for path in old_paths));self.assertFalse(report['errors'])
    def test_unchanged_phone_entries_skip_capture_date_and_local_content_reads(self):
        source,entry=self.picture();date_reads=[]
        class Picture:
            Path=str(source);Size=source.stat().st_size;ModifyDate=datetime.fromtimestamp(entry['modified'])
            def ExtendedProperty(self,key):
                date_reads.append(key);return None
        self.entries=[dict(path=entry['path'],size=None,_shell_item=Picture())]
        self.run_import();self.assertEqual(date_reads,['System.Photo.DateTaken']);date_reads.clear()
        with patch('photo_review.phone_download.digest',side_effect=AssertionError('Unchanged PC files must use the local checksum cache')),patch.object(self,'read',wraps=self.read) as phone_read:
            report=self.run_import()
        phone_read.assert_called_once_with(str(source),None)
        self.assertEqual((report['copied'],report['skipped'],report['new_total']),(0,1,0));self.assertEqual(date_reads,[])
    def test_old_import_database_is_checked_once_then_uses_cached_validation(self):
        self.picture();first=self.run_import();target=Path(first['files'][0]['path'])
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('DROP TABLE saved_copy_checks');db.commit()
        with patch('photo_review.phone_download.digest',wraps=digest) as checksum:
            second=self.run_import();self.assertEqual(checksum.call_count,1);self.assertEqual(checksum.call_args.args[0],target)
        with patch('photo_review.phone_download.digest',side_effect=AssertionError('Upgraded history should be cached')):
            third=self.run_import()
        self.assertEqual((second['skipped'],third['skipped']),(1,1))
    def test_changed_same_size_local_copy_is_reimported_without_overwriting_edit(self):
        self.picture();first=self.run_import();target=Path(first['files'][0]['path']);info=target.stat()
        content=bytearray(target.read_bytes());content[-1]^=1;target.write_bytes(content)
        os.utime(target,ns=(info.st_atime_ns,info.st_mtime_ns+2_000_000_000))
        report=self.run_import()
        self.assertEqual((report['copied'],report['skipped']),(1,0));self.assertEqual(target.read_bytes(),content)
        self.assertNotEqual(report['files'][0]['path'],str(target))
    def test_unchanged_content_with_new_local_timestamp_is_revalidated_then_cached(self):
        self.picture();first=self.run_import();target=Path(first['files'][0]['path']);info=target.stat()
        os.utime(target,ns=(info.st_atime_ns,info.st_mtime_ns+2_000_000_000))
        with patch('photo_review.phone_download.digest',wraps=digest) as checksum:
            report=self.run_import();self.assertEqual(checksum.call_count,1);self.assertEqual(checksum.call_args.args[0],target)
        self.assertEqual((report['copied'],report['skipped']),(0,1))
        with patch('photo_review.phone_download.digest',side_effect=AssertionError('Revalidated content should be cached')):self.run_import()
    def test_changed_source_metadata_with_identical_content_is_skipped(self):
        source,entry=self.picture();first=self.run_import();entry['modified']+=10
        report=self.run_import();self.assertEqual((report['copied'],report['skipped']),(0,1))
        self.assertTrue(Path(first['files'][0]['path']).exists())
    def test_case_sensitive_phone_names_keep_independent_history(self):
        self.picture('first.jpg',data=b'first generated photo')
        self.picture('second.jpg',data=b'other generated photo')
        self.entries[0]['path']='DCIM/Camera/PHOTO.jpg';self.entries[1]['path']='DCIM/Camera/photo.jpg'
        first=self.run_import();second=self.run_import()
        self.assertEqual((first['copied'],second['skipped']),(2,2))
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM imports').fetchone()[0],2)
    def test_cancel_during_history_check_does_not_start_any_copies(self):
        for index in range(40):self.picture(f'photo-{index}.jpg')
        cancel=threading.Event()
        def progress(message):
            if message.startswith('Checking phone checksums · 32 of'):cancel.set()
        report=import_entries(self.entries,self.device,self.output,self.db,copier=lambda *args:self.fail('No copy before checksum comparison finishes'),reader=self.read,progress=progress,cancel=cancel)
        self.assertTrue(report['cancelled']);self.assertEqual(report['copied'],0);self.assertEqual(report['checked'],32)
        self.assertFalse(list(self.output.rglob('*.jpg')))
    def test_cancel_on_final_history_update_or_copy_count_blocks_cleanup_offer(self):
        self.picture();cancel=threading.Event()
        def progress(message):
            if message.startswith('Checking phone checksums · 1 of'):cancel.set()
        cancelled=self.run_import(cancel=cancel,progress=progress)
        self.assertTrue(cancelled['cancelled']);self.assertEqual(cancelled['copied'],0)
        cancel.clear()
        finished=self.run_import(cancel=cancel,counts=lambda done,total:cancel.set() if done==total==1 else None)
        self.assertTrue(finished['cancelled']);self.assertEqual(finished['copied'],1)
    def test_same_model_phones_keep_separate_folders_and_import_history(self):
        from photo_review.phone_folders import phone_destination,remember_phone_destination
        self.picture();self.output.mkdir()
        first=self.device;second=PortableDevice('another-device',first.name)
        first_folder=phone_destination(first,self.output,{})
        settings=remember_phone_destination({},first,self.output,first_folder,'day-tree')
        second_folder=phone_destination(second,self.output,settings)
        reports=[]
        for device,folder in ((first,first_folder),(second,second_folder),(first,first_folder),(second,second_folder)):
            reports.append(import_entries(self.entries,device,folder,self.db,copier=self.copy,reader=self.read))
        self.assertNotEqual(first_folder,second_folder)
        self.assertEqual([report['copied'] for report in reports],[1,1,0,0])
        self.assertEqual([report['skipped'] for report in reports],[0,0,1,1])
        self.assertNotEqual(reports[0]['files'][0]['path'],reports[1]['files'][0]['path'])
    def test_same_name_and_existing_files_are_never_overwritten(self):
        self.picture();self.picture('other/one.jpg')
        existing=self.output/'2024'/'02'/'03'/'one.jpg';existing.parent.mkdir(parents=True);existing.write_bytes(b'existing user file')
        report=self.run_import();self.assertEqual(report['copied'],2);self.assertEqual(existing.read_bytes(),b'existing user file')
        self.assertEqual(len(list(existing.parent.glob('*.jpg'))),3)
    def test_bad_size_is_not_published_and_can_retry(self):
        source,entry=self.picture();original=entry['size'];entry['size']+=10
        report=self.run_import();self.assertEqual(report['copied'],0);self.assertEqual(len(report['errors']),1)
        self.assertFalse(list(self.output.rglob('*.jpg')))
        entry['size']=original;self.assertEqual(self.run_import()['copied'],1)
    def test_cancel_keeps_verified_prefix_and_resumes_without_recopies(self):
        first,_=self.picture();second,_=self.picture('two.jpg');cancel=threading.Event()
        def counts(done,total):
            if done==1:cancel.set()
        report=self.run_import(cancel=cancel,counts=counts)
        self.assertTrue(report['cancelled']);self.assertEqual(report['copied'],1)
        resumed=self.run_import();self.assertEqual(resumed['copied'],1);self.assertEqual(resumed['skipped'],1)
        self.assertTrue(first.exists());self.assertTrue(second.exists())
    def test_unknown_date_and_unsafe_names(self):
        source,entry=self.picture(data=b'unsupported image data');entry['modified']=None;entry['path']='Internal storage/DCIM/a:b.jpg'
        report=self.run_import();target=Path(report['files'][0]['path'])
        self.assertEqual(target.parent,self.output/'Unknown date');self.assertNotIn(':',target.name)
        self.assertNotEqual(safe_name('CON'),'CON');self.assertTrue(source.exists())
    def test_changed_local_copy_is_not_trusted_or_overwritten(self):
        self.picture();first=self.run_import();old=Path(first['files'][0]['path']);old.write_bytes(b'locally edited')
        second=self.run_import();self.assertEqual(second['copied'],1);self.assertEqual(old.read_bytes(),b'locally edited')
        self.assertNotEqual(second['files'][0]['path'],str(old))
    def test_capture_calendar_day_is_not_shifted_by_timezone(self):
        source,entry=self.picture(taken='2024:01:01 00:30:00')
        with Image.open(source) as original:
            exif=original.getexif();exif[36881]='+09:00';original.save(self.phone/'with-zone.jpg',exif=exif)
        entry['source']=str(self.phone/'with-zone.jpg');entry['size']=Path(entry['source']).stat().st_size
        report=self.run_import();self.assertEqual(Path(report['files'][0]['path']).parent,self.output/'2024'/'01'/'01')
    def test_shell_filesystem_source_is_refused_without_copying(self):
        from types import SimpleNamespace
        from win32com.shell import shell,shellcon
        item=SimpleNamespace(GetAttributes=lambda flag:shellcon.SFGAO_FILESYSTEM)
        with patch.object(shell,'SHCreateItemFromParsingName',return_value=item),patch('pythoncom.CoCreateInstance') as create:
            with self.assertRaisesRegex(ValueError,'filesystem source'):copy_shell_file('fixture-phone-item',self.base/'staged.jpg')
            create.assert_not_called()
    def test_locked_phone_with_no_storage_does_not_start_import(self):
        inventory=dict(folders=1,images=[],image_count=0,errors=[])
        with patch('win32com.client.dynamic.Dispatch'),patch('photo_review.phone_download.find_phone_shell_item'),patch('photo_review.phone_download.inventory_tree',return_value=inventory),patch('photo_review.phone_download.import_entries') as transfer:
            with self.assertRaisesRegex(RuntimeError,'storage is not available yet'):download_phone(self.device,self.output,self.db)
            transfer.assert_not_called()
    def test_uncopied_video_and_edit_companions_protect_phone_originals(self):
        from copy import deepcopy
        inventory=dict(folders=3,images=[],image_count=3,errors=[],companion_paths=['DCIM/IMG_1.MOV','DCIM/IMG_2.JPG.xmp'])
        candidates=[dict(phone_path='DCIM/'+name,source_pidl=['00']) for name in ('IMG_1.HEIC','IMG_2.JPG','IMG_3.JPG')]
        copied=dict(destination=str(self.output),total=3,copied=3,skipped=0,bytes=30,cancelled=False,errors=[],cleanup_candidates=candidates)
        with patch('win32com.client.dynamic.Dispatch'),patch('photo_review.phone_download.find_phone_shell_item'),patch('photo_review.phone_download.inventory_tree',return_value=inventory),patch('photo_review.phone_download.import_entries',return_value=deepcopy(copied)):
            report=download_phone(self.device,self.output,self.db)
        self.assertEqual(report['cleanup_protected'],2)
        self.assertEqual([row['phone_path'] for row in report['cleanup_candidates']],['DCIM/IMG_3.JPG'])

    def test_included_video_is_verified_and_is_no_longer_an_unbacked_companion(self):
        from copy import deepcopy
        inventory=dict(folders=3,images=[dict(path='DCIM/IMG_1.HEIC')],media=[dict(path='DCIM/IMG_1.HEIC'),dict(path='DCIM/IMG_1.MOV')],image_count=1,video_count=1,errors=[],companion_paths=['DCIM/IMG_1.MOV'])
        candidates=[dict(phone_path='DCIM/'+name,source_pidl=['00']) for name in ('IMG_1.HEIC','IMG_1.MOV')]
        copied=dict(destination=str(self.output),total=2,copied=2,skipped=0,bytes=30,cancelled=False,errors=[],cleanup_candidates=candidates)
        with patch('win32com.client.dynamic.Dispatch'),patch('photo_review.phone_download.find_phone_shell_item'),patch('photo_review.phone_download.inventory_tree',return_value=inventory) as discovery,patch('photo_review.phone_download.import_entries',return_value=deepcopy(copied)) as imported:
            report=download_phone(self.device,self.output,self.db,include_videos=True)
        self.assertTrue(discovery.call_args.kwargs['include_videos']);self.assertEqual(len(imported.call_args.args[0]),2)
        self.assertEqual(report['cleanup_protected'],0);self.assertEqual(len(report['verification_candidates']),2)
        self.assertEqual(len(report['cleanup_candidates']),2);self.assertEqual(report['unbacked_companion_paths'],[])
        inventory['companion_paths'].append('DCIM/IMG_1.MOV.xmp')
        with patch('win32com.client.dynamic.Dispatch'),patch('photo_review.phone_download.find_phone_shell_item'),patch('photo_review.phone_download.inventory_tree',return_value=inventory),patch('photo_review.phone_download.import_entries',return_value=deepcopy(copied)):
            protected=download_phone(self.device,self.output,self.db,include_videos=True)
        self.assertEqual(protected['cleanup_protected'],2);self.assertEqual(protected['cleanup_candidates'],[])
    def test_download_outside_working_root_is_rejected_before_phone_or_disk_writes(self):
        library=self.base/'library';library.mkdir()
        with patch('win32com.client.dynamic.Dispatch') as phone:
            with self.assertRaisesRegex(ValueError,'selected library'):download_phone(self.device,self.output,self.db,working_root=library)
            phone.assert_not_called()
        self.assertFalse(self.output.exists());self.assertFalse(self.db.parent.exists())
        with self.assertRaises(ValueError):destination_inside_root(library/'..'/'outside',library)
        self.assertEqual(destination_inside_root(library/'Pixel'/'Pictures',library),library/'Pixel'/'Pictures')
    def test_deferred_metadata_is_read_during_import_after_total_is_known(self):
        source,entry=self.picture();observed=[];counts=[]
        class Picture:
            @property
            def Path(self):observed.append('path');self.check_total();return str(source)
            @property
            def Size(self):return source.stat().st_size
            @property
            def ModifyDate(self):return datetime.fromtimestamp(entry['modified'])
            def ExtendedProperty(self,key):return None
            def check_total(self):
                if counts!=[(0,1)]:raise AssertionError('Metadata read before publishing the total')
        self.entries=[dict(path=entry['path'],size=None,_shell_item=Picture())]
        report=self.run_import(counts=lambda x,y:counts.append((x,y)))
        self.assertEqual(report['copied'],1);self.assertEqual(observed,['path']);self.assertEqual(counts,[(0,1),(0,1),(1,1)])
        self.assertTrue(source.exists())
    def test_namespace_alias_to_physical_file_is_refused(self):
        from types import SimpleNamespace
        from win32com.shell import shell,shellcon
        item=SimpleNamespace(GetAttributes=lambda flag:shellcon.SFGAO_FILESYSTEM,GetDisplayName=lambda kind:'C:\\fixture.jpg')
        with patch.object(shell,'SHCreateItemFromParsingName',return_value=item),patch('pythoncom.CoCreateInstance') as create:
            with self.assertRaisesRegex(ValueError,'filesystem source'):copy_shell_file('::{fixture-alias}',self.base/'staged.jpg')
            create.assert_not_called()
    def test_identifier_resolving_to_physical_file_is_refused(self):
        from types import SimpleNamespace
        from win32com.shell import shell,shellcon
        item=SimpleNamespace(GetAttributes=lambda flag:shellcon.SFGAO_FILESYSTEM,GetDisplayName=lambda kind:'C:\\fixture.jpg')
        with patch.object(shell,'SHCreateItemFromIDList',return_value=item),patch('pythoncom.CoCreateInstance') as create:
            with self.assertRaisesRegex(ValueError,'filesystem source'):copy_shell_file([b'local-file-identifier'],self.base/'staged.jpg')
            create.assert_not_called()
    def test_identifier_copy_uses_shell_identity_and_records_copy_trace(self):
        from types import SimpleNamespace
        from win32com.shell import shell
        source,entry=self.picture();pidl=[b'phone',b'picture'];stage=self.base/'staged.jpg'
        item=SimpleNamespace(GetAttributes=lambda flag:0);folder=object();calls=[]
        class Operation:
            def SetOperationFlags(self,flags):pass
            def CopyItem(self,source_item,destination,name,sink):
                calls.append((source_item,destination,name));self.sink=sink
            def PerformOperations(self):
                stage.write_bytes(source.read_bytes())
                self.sink.PostCopyItem(0,item,folder,stage.name,0,SimpleNamespace(GetDisplayName=lambda kind:str(stage)))
                return 0
            def GetAnyOperationsAborted(self):return False
        with patch.object(shell,'SHCreateItemFromIDList',return_value=item) as identify,patch.object(shell,'SHCreateItemFromParsingName',return_value=folder) as parse,patch('pythoncom.WrapObject',side_effect=lambda sink,iid:sink),patch('pythoncom.CoCreateInstance',return_value=Operation()):
            copy_shell_file(pidl,stage)
        identify.assert_called_once_with(pidl,shell.IID_IShellItem)
        parse.assert_called_once_with(str(stage.parent),None,shell.IID_IShellItem)
        self.assertEqual(calls,[(item,folder,stage.name)]);self.assertEqual(digest(stage),digest(source))
        events=[]
        def fail_copy(*args):raise OSError('fixture copy failure')
        report=import_entries(self.entries,self.device,self.output,self.db,copier=fail_copy,reader=self.read,trace=events.append)
        self.assertEqual(report['copied'],0);self.assertEqual(report['errors'][0]['path'],entry['path'])
        self.assertEqual([event['event'] for event in events],['start','finish','start','error'])
        self.assertEqual([event['action'] for event in events],['Check phone checksum']*2+['Copy phone picture']*2)
    def test_shell_operation_is_copy_only_and_confirms_destination(self):
        from types import SimpleNamespace
        from win32com.shell import shell
        stage=self.base/'staged.jpg';source=SimpleNamespace(GetAttributes=lambda flag:0);folder=object();calls=[]
        class Operation:
            def SetOperationFlags(self,flags):pass
            def CopyItem(self,item,destination,name,sink):
                calls.append((item,destination,name));self.sink=sink
            def PerformOperations(self):
                self.sink.PreCopyItem(0,source,folder,stage.name);stage.write_bytes(b'copied fixture bytes')
                self.sink.PostCopyItem(0,source,folder,stage.name,0,SimpleNamespace(GetDisplayName=lambda kind:str(stage)))
                return 0
            def GetAnyOperationsAborted(self):return False
        with patch.object(shell,'SHCreateItemFromParsingName',side_effect=[source,folder]),patch('pythoncom.WrapObject',side_effect=lambda sink,iid:sink),patch('pythoncom.CoCreateInstance',return_value=Operation()):
            copy_shell_file('fixture-phone-item',stage)
        self.assertEqual(calls,[(source,folder,stage.name)]);self.assertEqual(stage.read_bytes(),b'copied fixture bytes')
