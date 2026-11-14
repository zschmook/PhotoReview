import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from photo_review.phones import PortableDevice
from photo_review.phone_cleanup import cleanup_phone,cleanup_available,delete_phone_file,locked_backup,phone_digest,verify_phone_cleanup,verification_complete
from photo_review.phone_download import import_entries


class PhoneCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'library';self.root.mkdir()
        self.device=PortableDevice('USB\\VID_18D1&PID_4EE1\\GENERATED-FIXTURE','Pixel fixture')
        self.destination=self.root/'Pixel fixture';self.destination.mkdir()
        self.database=self.base/'profile'/'imports.sqlite3';self.journal=self.base/'profile'/'cleanup.jsonl'
        self.payload=b'generated photo bytes for cleanup tests';self.identifier=[b'fake-device',b'fake-image']
        self.entry=dict(path='Internal storage/DCIM/Camera/fixture.jpg',source=self.identifier,size=len(self.payload),modified=12345,taken=None)
        self.report=import_entries([self.entry],self.device,self.destination,self.database,copier=lambda source,target,cancel:Path(target).write_bytes(self.payload),reader=self.reader)
        self.deleted=[]
    def reader(self,identifier,cancel):return len(self.payload),hashlib.sha256(self.payload).hexdigest()
    def deleter(self,identifier,cancel,before_delete):before_delete();self.deleted.append(identifier)
    def cleanup(self,**changes):
        options=dict(confirmed=True,reader=self.reader,deleter=self.deleter,devices=lambda:[self.device]);options.update(changes)
        verification=verify_phone_cleanup(self.device,self.report,self.root,self.journal.with_name('verification.jsonl'),confirmed=options['confirmed'],reader=options['reader'],devices=options['devices'],cancel=options.get('cancel'))
        if not verification_complete(verification,self.device,self.report,self.root):return verification
        return cleanup_phone(self.device,self.report,self.root,self.journal,verification=verification,**options)
    def test_import_manifest_is_serializable_and_repeat_includes_verified_skips(self):
        self.assertTrue(cleanup_available(self.report));json.dumps(self.report)
        repeated=import_entries([self.entry],self.device,self.destination,self.database,copier=lambda *args:self.fail('Already imported file must not be recopied'),reader=self.reader)
        self.assertEqual(repeated['skipped'],1);self.assertEqual(len(repeated['cleanup_candidates']),1)
        self.assertEqual(repeated['cleanup_candidates'][0]['source_pidl'],[part.hex() for part in self.identifier])
    def test_incremental_skips_still_require_fresh_phone_checksums_before_removal(self):
        new_entry=dict(self.entry,path='Internal storage/DCIM/Camera/new.jpg',source=[b'fake-device',b'new-image'])
        report=import_entries([self.entry,new_entry],self.device,self.destination,self.database,copier=lambda source,target,cancel:Path(target).write_bytes(self.payload),reader=self.reader)
        self.assertEqual((report['new_total'],report['copied'],report['skipped']),(0,0,2))
        originals=[]
        def reader(identifier,cancel):originals.append(identifier);return self.reader(identifier,cancel)
        result=verify_phone_cleanup(self.device,report,self.root,self.journal,confirmed=True,reader=reader,devices=lambda:[self.device])
        self.assertTrue(verification_complete(result,self.device,report,self.root));self.assertEqual(result['verified'],2)
        self.assertEqual(originals,[self.identifier,new_entry['source']]);self.assertEqual(self.deleted,[])
    def test_pc_cached_import_does_not_authorize_cleanup_after_phone_edit(self):
        report=import_entries([self.entry],self.device,self.destination,self.database,copier=lambda *args:self.fail('Unexpected copy'),reader=lambda *args:self.fail('Unchanged repeat should use PC index'))
        self.assertEqual(report['cached_sources'],1)
        calls=[]
        def changed(identifier,cancel):calls.append(identifier);return len(self.payload),'0'*64
        result=verify_phone_cleanup(self.device,report,self.root,self.journal,confirmed=True,reader=changed,devices=lambda:[self.device])
        self.assertEqual(calls,[self.identifier]);self.assertEqual(result['verified'],0);self.assertTrue(result['errors'])
        self.assertFalse(verification_complete(result,self.device,report,self.root));self.assertEqual(self.deleted,[])
    def test_only_explicitly_confirmed_complete_download_can_delete(self):
        with self.assertRaisesRegex(ValueError,'confirmation'):self.cleanup(confirmed=False)
        for key,value in (('cancelled',True),('errors',[{'error':'fixture'}]),('total',2),('stopped','fixture error')):
            with self.subTest(key=key):
                original=self.report.get(key);self.report[key]=value
                with self.assertRaisesRegex(ValueError,'complete, error-free'):self.cleanup()
                if original is None:self.report.pop(key,None)
                else:self.report[key]=original
        self.assertEqual(self.deleted,[])
    def test_success_has_exact_source_verification_locked_backup_and_journal(self):
        target=Path(self.report['cleanup_candidates'][0]['local_path']);events=[]
        def reader(identifier,cancel):
            events.append('read original');self.assertEqual(identifier,self.identifier)
            with self.assertRaises(OSError):target.write_bytes(b'cannot edit a locked backup')
            with self.assertRaises(OSError):target.unlink()
            return self.reader(identifier,cancel)
        def deleter(identifier,cancel,before_delete):
            events.append('delete');self.deleter(identifier,cancel,before_delete)
        result=self.cleanup(reader=reader,deleter=deleter)
        self.assertEqual(events,['read original','read original','delete']);self.assertEqual(result['deleted'],1);self.assertFalse(result['errors'])
        self.assertEqual(target.read_bytes(),self.payload)
        self.assertEqual([json.loads(line)['state'] for line in self.journal.read_text().splitlines()],['verified','planned','deleted'])
    def test_changed_missing_or_outside_backup_never_reaches_phone_delete(self):
        target=Path(self.report['cleanup_candidates'][0]['local_path'])
        target.write_bytes(b'changed copy');result=self.cleanup()
        self.assertEqual(result['deleted'],0);self.assertIn('Saved copy',result['errors'][0]['error'])
        target.unlink();result=self.cleanup();self.assertEqual(result['deleted'],0)
        self.report['cleanup_candidates'][0]['local_path']=str(self.base/'outside.jpg')
        result=self.cleanup();self.assertEqual(result['deleted'],0);self.assertEqual(self.deleted,[])
    def test_phone_hash_mismatch_retains_original(self):
        result=self.cleanup(reader=lambda *args:(len(self.payload),'0'*64))
        self.assertEqual(result['deleted'],0);self.assertIn('exactly match',result['errors'][0]['error']);self.assertEqual(self.deleted,[])
    def test_other_phone_is_not_accepted_even_with_same_model_name(self):
        other=PortableDevice('USB\\VID_18D1&PID_4EE1\\OTHER-SERIAL',self.device.name)
        with self.assertRaisesRegex(OSError,'disconnected'):self.cleanup(devices=lambda:[other])
        self.report['cleanup_candidates'][0]['device_id']=other.identity
        result=self.cleanup();self.assertEqual(result['deleted'],0);self.assertIn('different phone',result['errors'][0]['error'])
    def test_disconnect_after_verification_stops_before_delete(self):
        present=[True]
        def reader(*args):present[0]=False;return self.reader(*args)
        result=self.cleanup(reader=reader,devices=lambda:[self.device] if present[0] else [])
        self.assertEqual(result['deleted'],0);self.assertIn('disconnected',result['errors'][0]['error']);self.assertEqual(self.deleted,[])
    def test_cancel_after_hash_keeps_phone_original(self):
        cancel=threading.Event()
        def reader(*args):cancel.set();return self.reader(*args)
        result=self.cleanup(reader=reader,cancel=cancel)
        self.assertTrue(result['cancelled']);self.assertEqual(result['deleted'],0);self.assertEqual(self.deleted,[])
    def test_test_profile_marker_blocks_cleanup(self):
        (self.journal.parent/'PHONE_DELETE_DISABLED').write_text('test-only')
        with self.assertRaisesRegex(ValueError,'disabled'):self.cleanup()
        self.assertEqual(self.deleted,[])
    def test_thumbnail_and_non_photo_paths_are_refused(self):
        candidate=self.report['cleanup_candidates'][0]
        for path in ('Internal storage/DCIM/.thumbnails/preview.jpg','Internal storage/DCIM/video.mov','Internal storage/Android/private.jpg','../outside.jpg'):
            with self.subTest(path=path):
                candidate['phone_path']=path;result=self.cleanup()
                self.assertEqual(result['deleted'],0);self.assertTrue(result['errors'])
        self.assertEqual(self.deleted,[])

    def test_iphone_date_path_accepts_verified_media_but_still_refuses_unsafe_paths(self):
        self.device=PortableDevice('USB\\VID_05AC&PID_12A8\\fixture','Mom')
        candidate=self.report['cleanup_candidates'][0];candidate['device_id']=self.device.identity
        for path in ('../outside.jpg','Internal Storage/202611_a/.thumbnails/cache.jpg','Internal Storage/202611_a/note.txt'):
            candidate['phone_path']=path;result=self.cleanup()
            self.assertTrue(result['errors']);self.assertEqual(self.deleted,[])
        candidate['phone_path']='Internal Storage/202611_a/fixture.jpg'
        result=self.cleanup();self.assertFalse(result['errors']);self.assertEqual(result['deleted'],1)

    def test_selected_video_participates_in_both_checksum_passes_and_cleanup(self):
        candidate=self.report['cleanup_candidates'][0]
        target=self.destination/'Videos'/'2024'/'01'/'clip.mp4';target.parent.mkdir(parents=True)
        Path(candidate['local_path']).rename(target)
        candidate.update(phone_path='Internal storage/DCIM/Camera/clip.mp4',local_path=str(target))
        self.report['include_videos']=True;self.report['video_total']=1
        reads=[]
        result=self.cleanup(reader=lambda *args:(reads.append(True) or self.reader(*args)))
        self.assertEqual(result['deleted'],1);self.assertEqual(len(reads),2);self.assertEqual(target.read_bytes(),self.payload)
    def test_companion_guard_is_rechecked_by_cleanup_backend(self):
        self.report['discovery']=dict(companion_paths=['Internal storage/DCIM/Camera/fixture.mov'])
        result=self.cleanup();self.assertEqual(result['deleted'],0)
        self.assertIn('uncopied video/edit companion',result['errors'][0]['error']);self.assertEqual(self.deleted,[])
    def test_duplicate_identifier_in_full_checksum_pass_prevents_all_deletion(self):
        self.report['cleanup_candidates']*=2;self.report['total']=2;self.report['copied']=2
        result=self.cleanup()
        self.assertEqual(result['deleted'],0);self.assertEqual(len(self.deleted),0)
        self.assertIn('Duplicate',result['errors'][0]['error'])

    def test_full_checksum_pass_is_required_before_second_confirmation(self):
        with self.assertRaisesRegex(ValueError,'every downloaded picture'):
            cleanup_phone(self.device,self.report,self.root,self.journal,confirmed=True,reader=self.reader,deleter=self.deleter,devices=lambda:[self.device])
        self.assertEqual(self.deleted,[])

    def test_successful_full_pass_only_checks_then_plan_changes_are_refused(self):
        verification=verify_phone_cleanup(self.device,self.report,self.root,self.journal,confirmed=True,reader=self.reader,devices=lambda:[self.device])
        self.assertEqual(verification['verified'],1);self.assertEqual(verification['deleted'],0)
        self.assertTrue(verification_complete(verification,self.device,self.report,self.root))
        self.assertEqual([json.loads(line)['state'] for line in self.journal.read_text().splitlines()],['verified'])
        self.report['cleanup_candidates'][0]['local_path']=str(self.destination/'other.jpg')
        with self.assertRaisesRegex(ValueError,'checksum verification'):
            cleanup_phone(self.device,self.report,self.root,self.journal,confirmed=True,verification=verification,reader=self.reader,deleter=self.deleter,devices=lambda:[self.device])
        self.assertEqual(self.deleted,[])

    def test_last_picture_checksum_mismatch_prevents_deleting_earlier_matches(self):
        from copy import deepcopy
        second=deepcopy(self.report['cleanup_candidates'][0]);second['phone_path']='Internal storage/DCIM/Camera/second.jpg';second['source_pidl']=[b'fake-second'.hex()]
        self.report['cleanup_candidates'].append(second);self.report['total']=2;self.report['copied']=2
        calls=[]
        def reader(identifier,cancel):
            calls.append(identifier)
            return self.reader(identifier,cancel) if len(calls)==1 else (len(self.payload),'0'*64)
        result=self.cleanup(reader=reader)
        self.assertEqual(result['verified'],1);self.assertEqual(result['deleted'],0);self.assertEqual(len(calls),2)
        self.assertEqual(self.deleted,[]);self.assertTrue(result['errors'])

    def test_changes_after_full_pass_are_rechecked_before_actual_deletion(self):
        verification=verify_phone_cleanup(self.device,self.report,self.root,self.journal,confirmed=True,reader=self.reader,devices=lambda:[self.device])
        target=Path(self.report['cleanup_candidates'][0]['local_path']);target.write_bytes(b'edited after verification')
        result=cleanup_phone(self.device,self.report,self.root,self.journal,confirmed=True,verification=verification,reader=self.reader,deleter=self.deleter,devices=lambda:[self.device])
        self.assertEqual(result['deleted'],0);self.assertIn('Saved copy changed',result['errors'][0]['error'])
        target.write_bytes(self.payload)
        result=cleanup_phone(self.device,self.report,self.root,self.journal,confirmed=True,verification=verification,reader=lambda *args:(len(self.payload),'0'*64),deleter=self.deleter,devices=lambda:[self.device])
        self.assertEqual(result['deleted'],0);self.assertIn('does not exactly match',result['errors'][0]['error']);self.assertEqual(self.deleted,[])

    def test_full_pass_checks_protected_pictures_as_well_as_cleanup_candidates(self):
        from copy import deepcopy
        protected=deepcopy(self.report['cleanup_candidates'][0]);protected['phone_path']='Internal storage/DCIM/Camera/live.jpg';protected['source_pidl']=[b'fake-live'.hex()]
        self.report['verification_candidates']=[self.report['cleanup_candidates'][0],protected]
        self.report['discovery']=dict(companion_paths=['Internal storage/DCIM/Camera/live.mov'])
        self.report['total']=2;self.report['copied']=2;calls=[]
        verification=verify_phone_cleanup(self.device,self.report,self.root,self.journal,confirmed=True,reader=lambda source,cancel:(calls.append(source) or self.reader(source,cancel)),devices=lambda:[self.device])
        self.assertEqual(verification['verified'],2);self.assertTrue(verification_complete(verification,self.device,self.report,self.root))
        self.assertEqual(len(calls),2);self.assertEqual(self.deleted,[])

    def test_deletion_candidate_cannot_be_missing_from_full_checksum_plan(self):
        from copy import deepcopy
        wrong=deepcopy(self.report['cleanup_candidates'][0]);wrong['source_pidl']=[b'not-the-target'.hex()]
        self.report['verification_candidates']=[wrong]
        with self.assertRaisesRegex(ValueError,'Every deletion candidate'):
            verify_phone_cleanup(self.device,self.report,self.root,self.journal,confirmed=True,reader=self.reader,devices=lambda:[self.device])
        self.assertEqual(self.deleted,[])

    def test_partial_delete_failure_only_occurs_after_full_pass(self):
        from copy import deepcopy
        second=deepcopy(self.report['cleanup_candidates'][0]);second['phone_path']='Internal storage/DCIM/Camera/second.jpg';second['source_pidl']=[b'second'.hex()]
        self.report['cleanup_candidates'].append(second);self.report['total']=2;self.report['copied']=2
        events=[]
        def reader(identifier,cancel):events.append('verify');return self.reader(identifier,cancel)
        def deleter(identifier,cancel,before_delete):
            events.append('delete')
            if self.deleted:raise OSError('fixture deletion failed')
            self.deleter(identifier,cancel,before_delete)
        result=self.cleanup(reader=reader,deleter=deleter)
        self.assertEqual(events[:3],['verify','verify','verify'])
        self.assertEqual(result['deleted'],1);self.assertEqual(len(self.deleted),1)
        self.assertIn('fixture deletion failed',result['errors'][0]['error'])

    def test_native_deleter_accepts_only_exact_leaf_and_confirmed_success(self):
        from win32com.shell import shell,shellcon
        source=SimpleNamespace(GetAttributes=lambda flags:flags&shellcon.SFGAO_CANDELETE);calls=[]
        class Operation:
            def SetOperationFlags(self,flags):pass
            def DeleteItem(self,item,sink):calls.append(item);self.sink=sink
            def PerformOperations(self):self.sink.PreDeleteItem(0,source);self.sink.PostDeleteItem(0,source,0,None);return 0
            def GetAnyOperationsAborted(self):return False
        with patch('photo_review.phone_cleanup.phone_shell_item',return_value=source),patch.object(shell,'SHGetIDListFromObject',return_value=self.identifier),patch('pythoncom.WrapObject',side_effect=lambda sink,iid:sink),patch('pythoncom.CoCreateInstance',return_value=Operation()):
            delete_phone_file(self.identifier)
        self.assertEqual(calls,[source])
        with self.assertRaisesRegex(ValueError,'identifier'):delete_phone_file('C:\\physical-file.jpg')
        with patch('photo_review.phone_cleanup.phone_shell_item',return_value=SimpleNamespace(GetAttributes=lambda flags:flags)),patch('pythoncom.CoCreateInstance') as operation:
            with self.assertRaisesRegex(ValueError,'folder/link'):delete_phone_file(self.identifier)
            operation.assert_not_called()
    def test_native_deleter_refuses_unexpected_callback_target(self):
        from win32com.shell import shell,shellcon
        source=SimpleNamespace(GetAttributes=lambda flags:flags&shellcon.SFGAO_CANDELETE)
        class Operation:
            def SetOperationFlags(self,flags):pass
            def DeleteItem(self,item,sink):self.sink=sink
            def PerformOperations(self):self.sink.PreDeleteItem(0,source);raise AssertionError('Unexpected target must be rejected first')
        with patch('photo_review.phone_cleanup.phone_shell_item',return_value=source),patch.object(shell,'SHGetIDListFromObject',return_value=[b'wrong']),patch('pythoncom.WrapObject',side_effect=lambda sink,iid:sink),patch('pythoncom.CoCreateInstance',return_value=Operation()):
            with self.assertRaisesRegex(Exception,'Unexpected phone'):delete_phone_file(self.identifier)
    def test_native_deleter_requires_device_capability_and_refuses_ignored_result(self):
        from win32com.shell import shell,shellcon
        source=SimpleNamespace(GetAttributes=lambda flags:flags&shellcon.SFGAO_CANDELETE)
        class Operation:
            def SetOperationFlags(self,flags):pass
            def DeleteItem(self,item,sink):self.sink=sink
            def PerformOperations(self):self.sink.PreDeleteItem(0,source);self.sink.PostDeleteItem(0,source,1,None);return 0
        with patch('photo_review.phone_cleanup.phone_shell_item',return_value=SimpleNamespace(GetAttributes=lambda flags:0)),patch('pythoncom.CoCreateInstance') as create:
            with self.assertRaisesRegex(ValueError,'does not allow'):delete_phone_file(self.identifier)
            create.assert_not_called()
        with patch('photo_review.phone_cleanup.phone_shell_item',return_value=source),patch.object(shell,'SHGetIDListFromObject',return_value=self.identifier),patch('pythoncom.WrapObject',side_effect=lambda sink,iid:sink),patch('pythoncom.CoCreateInstance',return_value=Operation()):
            with self.assertRaisesRegex(Exception,'not confirmed'):delete_phone_file(self.identifier)
    def test_native_digest_requests_read_only_stream(self):
        import pythoncom
        from win32com.shell import shell
        parts=iter([self.payload,b'']);context=SimpleNamespace(GetBindOptions=lambda:{'Mode':2},SetBindOptions=lambda options:self.assertEqual(options['Mode'],0))
        stream=SimpleNamespace(Read=lambda count:next(parts))
        item=SimpleNamespace(BindToHandler=lambda ctx,bhid,iid:stream)
        with patch('photo_review.phone_cleanup.phone_shell_item',return_value=item),patch('pythoncom.CreateBindCtx',return_value=context):
            self.assertEqual(phone_digest(self.identifier),self.reader(None,None))
    def test_native_checksum_reports_bytes_and_cancels_between_blocks(self):
        cancel=threading.Event();reads=[];updates=[]
        context=SimpleNamespace(GetBindOptions=lambda:{},SetBindOptions=lambda options:None)
        def read(count):reads.append(count);return self.payload
        item=SimpleNamespace(BindToHandler=lambda *args:SimpleNamespace(Read=read))
        def progress(size):updates.append(size);cancel.set()
        with patch('photo_review.phone_cleanup.phone_shell_item',return_value=item),patch('pythoncom.CreateBindCtx',return_value=context),patch('time.monotonic',side_effect=[0,.3,.3]):
            with self.assertRaises(InterruptedError):phone_digest(self.identifier,cancel,progress)
        self.assertEqual(updates,[len(self.payload)]);self.assertEqual(reads,[1024*1024])
