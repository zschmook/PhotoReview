import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from photo_review.phone_import import inventory_tree,find_phone_shell_item,read_phone_metadata,iter_shell_items
from photo_review.phones import PortableDevice

class Item:
    def __init__(self,name,children=None,size=10,path=None,filesystem=False):
        self.Name=name;self.IsFolder=children is not None;self.Size=size;self.Path=path or name;self.IsFileSystem=filesystem
        self.children=children or []
    @property
    def GetFolder(self):return self
    def Items(self):return self.children
    def ExtendedProperty(self,key):return None

class ImportPreviewTests(unittest.TestCase):
    def test_counts_only_images_preserves_folder_paths_and_copies_nothing(self):
        root=Item('Phone',[Item('Internal storage',[Item('DCIM',[Item('one.jpg',size=100),Item('two.HEIC',size=200),Item('movie.mp4',size=500)]),Item('Pictures',[Item('one.jpg',size=None)])])])
        progress=[];report=inventory_tree(root,progress.append)
        self.assertEqual(report['image_count'],3);self.assertEqual(report['known_bytes'],300)
        self.assertEqual(report['unknown_sizes'],1);self.assertEqual(report['copied'],0);self.assertTrue(report['test_only'])
        self.assertEqual(report['images'][0]['path'],'Internal storage/DCIM/one.jpg');self.assertTrue(progress)
    def test_cancel_prevents_inventory(self):
        cancel=threading.Event();cancel.set()
        with self.assertRaises(InterruptedError):inventory_tree(Item('Phone',[]),cancel=cancel)
    def test_cyclic_shell_folder_not_followed_forever(self):
        root=Item('Phone',[],path='phone');root.children=[root]
        self.assertEqual(inventory_tree(root)['folders'],1)
    def test_unavailable_storage_fails(self):
        class Locked(Item):
            @property
            def GetFolder(self):raise OSError('Locked')
        with self.assertRaisesRegex(RuntimeError,'Unlock'):inventory_tree(Locked('Phone',[]))
    def test_disk_is_never_opened_and_only_selected_phone_is_matched(self):
        disk=Item('Phone',[],filesystem=True);phone=Item('Phone',[]);other=Item('Other phone',[])
        namespace=Item('This PC',[disk,other,phone]);shell=SimpleNamespace(NameSpace=lambda index:namespace)
        self.assertIs(find_phone_shell_item(shell,PortableDevice('id','Phone')),phone)
    def test_ambiguous_phone_names_require_resolution(self):
        namespace=Item('This PC',[Item('Phone',[]),Item('Phone',[])])
        with self.assertRaisesRegex(RuntimeError,'uniquely'):find_phone_shell_item(SimpleNamespace(NameSpace=lambda index:namespace),PortableDevice('id','Phone'))
    def test_known_other_device_is_not_selected_by_matching_model_name(self):
        class OtherPhone(Item):
            def ExtendedProperty(self,key):return 'different-device-id'
        namespace=Item('This PC',[OtherPhone('Pixel 8',[])])
        with self.assertRaisesRegex(RuntimeError,'uniquely'):
            find_phone_shell_item(SimpleNamespace(NameSpace=lambda index:namespace),PortableDevice('original-device-id','Pixel 8'))

    def test_only_selected_storage_folders_are_opened(self):
        opened=[]
        class Folder(Item):
            @property
            def GetFolder(self):opened.append(self.Path);return self
        unwanted=[Folder(name,[Item('unwanted.jpg')],path='internal/'+name) for name in ('Android','Music','Alarms','Podcasts','Other')]
        # A selected name deeper inside an unselected folder must not be found.
        unwanted[-1].children=[Folder('DCIM',[Item('hidden.jpg')],path='internal/Other/DCIM')]
        chosen=[Folder(name,[Item(name+'.jpg')],path='internal/'+name) for name in ('DCIM','Pictures','Download','Documents','Movies')]
        chosen[0].children.append(Folder('Camera',[Item('camera.jpg')],path='internal/DCIM/Camera'))
        internal=Folder('Internal shared storage',unwanted+chosen+[Item('loose.jpg')],path='internal')
        card=Folder('SD card',[Folder('DCIM',[Item('card.jpg')],path='card/DCIM'),Folder('Android',[Item('cache.jpg')],path='card/Android')],path='card')
        report=inventory_tree(Folder('Phone',[internal,card],path='phone'))
        self.assertEqual(report['image_count'],7);self.assertFalse(report['errors'])
        self.assertFalse(any(path in opened for path in ('internal/Android','internal/Music','internal/Alarms','internal/Podcasts','internal/Other','internal/Other/DCIM','card/Android')))
        self.assertNotIn('loose.jpg',{entry['path'].split('/')[-1] for entry in report['images']})
        self.assertIn('internal/DCIM/Camera',opened);self.assertIn('card/DCIM',opened)

    def test_direct_public_folders_are_case_insensitive_and_allow_subfolders(self):
        class Forbidden(Item):
            @property
            def GetFolder(self):self.fail_if_opened=True;raise AssertionError('Unselected directory opened')
        ignored=Forbidden('Android',[Item('cache.jpg')]);ignored.fail_if_opened=False
        root=Item('Phone',[ignored,Item('dCiM',[Item('Android',[Item('nested.jpg')],path='DCIM/Android')]),Item('Downloads',[Item('download.png')]),Item('Documents',[Item('paper.jpg'),Item('paper.pdf')])])
        report=inventory_tree(root)
        self.assertEqual(report['image_count'],3);self.assertFalse(ignored.fail_if_opened);self.assertFalse(report['errors'])

    def test_thumbnail_directories_are_excluded_before_opening_in_preview_and_download(self):
        opened=[]
        class Folder(Item):
            @property
            def GetFolder(self):opened.append(self.Path);return self
        class Cache(Item):
            @property
            def GetFolder(self):raise AssertionError('Thumbnail cache must never be opened')
        for name_only in (False,True):
            with self.subTest(defer_metadata=name_only):
                camera=Folder('Camera',[Item('original.jpg'),Cache('thumbnails',[Item('cached.jpg')],path='DCIM/Camera/thumbnails')],path='DCIM/Camera')
                root=Folder('Phone',[Folder('DCIM',[camera,Cache('.THUMBNAILS',[Item('cached.jpg')],path='DCIM/.THUMBNAILS')],path='DCIM'),Folder('Pictures',[Folder('Holiday thumbnails',[Item('real.jpg')],path='Pictures/Holiday thumbnails'),Item('thumbnails.jpg')],path='Pictures')],path='phone')
                report=inventory_tree(root,include_sources=name_only,defer_metadata=name_only)
                self.assertEqual(report['image_count'],3);self.assertFalse(report['errors'])
                self.assertEqual(set(report['ignored_folders']),{'DCIM/.THUMBNAILS','DCIM/Camera/thumbnails'})
                self.assertNotIn('DCIM/.THUMBNAILS',opened);self.assertNotIn('DCIM/Camera/thumbnails',opened)

    def test_iphone_style_dcim_subfolders_are_kept_without_fixed_folder_names(self):
        root=Item('Apple iPhone',[Item('Internal Storage',[Item('DCIM',[Item('100APPLE',[Item('IMG_0001.HEIC'),Item('IMG_0001.MOV'),Item('IMG_0001.AAE')],path='DCIM/100APPLE'),Item('202611__',[Item('IMG_0002.JPG')],path='DCIM/202611__'),Item('.thumbnails',[Item('cached.jpg')],path='DCIM/.thumbnails')],path='DCIM')],path='internal')],path='phone')
        report=inventory_tree(root,include_sources=True,defer_metadata=True)
        self.assertEqual(report['image_count'],2);self.assertFalse(report['errors'])
        self.assertEqual({row['path'].split('/')[-1] for row in report['images']},{'IMG_0001.HEIC','IMG_0002.JPG'})
        self.assertEqual({path.split('/')[-1] for path in report['companion_paths']},{'IMG_0001.MOV','IMG_0001.AAE'})

    def test_iphone_date_folders_scan_all_exposed_storage_with_videos_opt_in(self):
        class Cache(Item):
            @property
            def GetFolder(self):raise AssertionError('Thumbnail folder must not be opened')
        root=Item('Apple iPhone',[Item('Internal Storage',[
            Item('202610_a',[Item('IMG_0795.HEIC'),Item('IMG_0795.MOV'),Item('IMG_0795.AAE')]),
            Item('202610_b',[Item('nested',[Item('photo.JPG')]),Cache('.thumbnails',[Item('cached.jpg')])]),
            Item('Other album',[Item('clip.MP4')]),Item('loose.JPG')
        ])])
        for deferred in (False,True):
            with self.subTest(deferred=deferred):
                photos=inventory_tree(root,defer_metadata=deferred)
                all_media=inventory_tree(root,defer_metadata=deferred,include_videos=True)
                self.assertEqual((photos['image_count'],photos['video_count']),(3,0))
                self.assertEqual((all_media['image_count'],all_media['video_count']),(3,2))
                self.assertFalse(all_media['errors']);self.assertTrue(all_media['storage_available'])
                self.assertEqual(all_media['folder_scope'],'all-exposed-storage')
                self.assertIn('Internal Storage/202610_a/IMG_0795.MOV',all_media['companion_paths'])
                self.assertIn('Internal Storage/202610_b/.thumbnails',all_media['ignored_folders'])

    def test_renamed_iphone_uses_identity_and_android_keeps_folder_filter(self):
        root=Item('Mom',[Item('Internal Storage',[Item('202611_a',[Item('photo.HEIC')]),Item('Android',[Item('cache.jpg')])])])
        apple=PortableDevice('USB\\VID_05AC&PID_12A8\\fixture','Mom')
        report=inventory_tree(root,device=apple,defer_metadata=True)
        self.assertEqual(report['image_count'],2)
        android=PortableDevice('USB\\VID_18D1&PID_4EE1\\fixture','Mom')
        self.assertEqual(inventory_tree(root,device=android)['image_count'],0)

    def test_iphone_empty_root_does_not_claim_storage_is_ready(self):
        self.assertFalse(inventory_tree(Item('Apple iPhone',[]))['storage_available'])

    def test_videos_are_opt_in_counted_separately_and_thumbnail_folders_still_excluded(self):
        class Cache(Item):
            @property
            def GetFolder(self):raise AssertionError('Video thumbnail cache must not be opened')
        class Video(Item):
            def ExtendedProperty(self,key):return 'clip.MP4' if key=='System.FileName' else None
        root=Item('Phone',[Item('DCIM',[Item('photo.jpg'),Item('movie.MOV')],path='DCIM'),Item('Movies',[Video('clip'),Cache('.thumbnails',[Item('cached.mp4')],path='Movies/.thumbnails')],path='Movies')],path='phone')
        photos=inventory_tree(root,include_sources=True,defer_metadata=True)
        messages=[];media=inventory_tree(root,messages.append,include_sources=True,defer_metadata=True,include_videos=True)
        self.assertEqual(photos['image_count'],1);self.assertEqual(photos['video_count'],0);self.assertEqual(photos['file_count'],1)
        self.assertEqual(media['image_count'],1);self.assertEqual(media['video_count'],2);self.assertEqual(media['file_count'],3)
        self.assertEqual({row['path'] for row in media['videos']},{'DCIM/movie.MOV','Movies/clip.MP4'})
        self.assertFalse(media['errors']);self.assertIn('Movies/.thumbnails',media['ignored_folders'])
        self.assertTrue(any('3 files found (1 picture, 2 videos)' in message for message in messages))

    def test_video_metadata_reads_encoded_date_without_image_date_query(self):
        from datetime import datetime
        captured=datetime(2025,2,3,4,5,6);queries=[]
        class Video(Item):
            ModifyDate=None
            def ExtendedProperty(self,key):queries.append(key);return captured if key=='System.Media.DateEncoded' else None
        ready=read_phone_metadata(dict(path='DCIM/movie.mp4',_shell_item=Video('movie.mp4')))
        self.assertEqual(ready['taken'],captured.timestamp());self.assertEqual(queries,['System.Size','System.Media.DateEncoded'])

    def test_large_video_size_uses_the_64_bit_windows_property(self):
        class Video:
            Path='virtual-video';ModifyDate=None
            @property
            def Size(self):raise AssertionError('Use the available 64-bit video size')
            def ExtendedProperty(self,key):return 5*1024**3+123 if key=='System.Size' else None
        ready=read_phone_metadata(dict(path='DCIM/large.mp4',_shell_item=Video()))
        self.assertEqual(ready['size'],5*1024**3+123)

    def test_cancel_during_video_size_read_stops_inventory(self):
        cancel=threading.Event()
        class Video(Item):
            def ExtendedProperty(self,key):
                if key=='System.Size':cancel.set();return 5*1024**3
        root=Item('Phone',[Item('DCIM',[Video('clip.mp4')],path='DCIM')],path='phone')
        with self.assertRaises(InterruptedError):inventory_tree(root,cancel=cancel,include_videos=True)

    def test_large_selected_folder_reports_count_before_enumeration_finishes(self):
        progress=[]
        class StreamingFolder(Item):
            def Items(self):
                for index in range(60):
                    if index==33:self.assert_progress()
                    yield Item(f'{index}.jpg')
            def assert_progress(self):
                if not any('32 pictures found' in message for message in progress):raise AssertionError('Next batch fetched before reporting the prior batch')
        root=Item('Phone',[Item('Internal storage',[StreamingFolder('DCIM',[])])])
        report=inventory_tree(root,progress.append)
        self.assertEqual(report['image_count'],60);self.assertFalse(report['errors'])
        self.assertTrue(any('Internal storage/DCIM' in message for message in progress))

    def test_cancel_during_selected_folder_count_stops_discovery(self):
        cancel=threading.Event();root=Item('Phone',[Item('DCIM',[Item('one.jpg'),Item('two.jpg')])])
        def progress(message):
            if '1 pictures found' in message:cancel.set()
        with self.assertRaises(InterruptedError):inventory_tree(root,progress,cancel)

    def test_counting_defers_all_remote_photo_metadata(self):
        accesses=[]
        class Picture:
            Name='first.jpg';IsFolder=False
            @property
            def Size(self):accesses.append('size');return 10
            @property
            def Path(self):accesses.append('path');return 'virtual-picture'
            @property
            def ModifyDate(self):accesses.append('modified');return None
            def ExtendedProperty(self,key):
                if key=='System.FileName':accesses.append('filename');return 'first.jpg'
                accesses.append('taken');return None
        picture=Picture();root=Item('Phone',[Item('DCIM',[picture])]);messages=[]
        report=inventory_tree(root,messages.append,include_sources=True,defer_metadata=True)
        self.assertEqual(report['image_count'],1);self.assertEqual(accesses,['filename'])
        self.assertTrue(any('1 pictures found' in message for message in messages))
        self.assertIs(report['images'][0]['_shell_item'],picture)
        ready=read_phone_metadata(report['images'][0]);self.assertEqual(ready['size'],10)
        self.assertEqual(accesses,['filename','path','size','taken','modified']);self.assertNotIn('_shell_item',ready)

    def test_hidden_display_extensions_still_count_actual_photo_filenames(self):
        class Hidden(Item):
            def __init__(self,display,filename):super().__init__(display);self.filename=filename
            def ExtendedProperty(self,key):return self.filename if key=='System.FileName' else None
        root=Item('Phone',[Item('DCIM',[Hidden('PXL_123','PXL_123.jpg'),Hidden('PXL_456.MP','PXL_456.MP.jpg'),Hidden('clip','clip.mp4')])])
        report=inventory_tree(root,include_sources=True,defer_metadata=True)
        self.assertEqual(report['image_count'],2)
        self.assertEqual([row['path'] for row in report['images']],['DCIM/PXL_123.jpg','DCIM/PXL_456.MP.jpg'])

    def test_native_iteration_never_requests_aliased_bulk_items_or_loses_tail(self):
        class Enum:
            def __init__(self,values):self.values=values;self.index=0;self.requests=[]
            def QueryInterface(self,iid):return self
            def Next(self,count):
                self.requests.append(count)
                end=min(len(self.values),self.index+count);result=self.values[self.index:end];self.index=end
                # Actual provider repeats the last item in bulk results and
                # drops short results; Next(1) returns distinct stable items.
                return tuple([result[-1]]*count) if len(result)==count else ()
        for count in (0,1,16,32,33,70):
            with self.subTest(count=count):
                enum=Enum(list(range(count)));items=SimpleNamespace(_oleobj_=SimpleNamespace(InvokeTypes=lambda *args:enum))
                with patch('win32com.client.dynamic.Dispatch',side_effect=lambda value:value):
                    values=list(iter_shell_items(items))
                self.assertEqual(values,list(range(count)))
                self.assertEqual(enum.requests,[1]*(count+1))

    def test_native_metadata_uses_absolute_identifier_without_parsing_display_path(self):
        ole=object();pidl=[b'device',b'picture']
        class Picture:
            _oleobj_=ole;Size=42;ModifyDate=None
            @property
            def Path(self):raise AssertionError('MTP display path is not a reliable file identifier')
            def ExtendedProperty(self,key):return None
        with patch('win32com.shell.shell.SHGetIDListFromObject',return_value=pidl) as identifier:
            entry=read_phone_metadata(dict(path='DCIM/one.jpg',_shell_item=Picture()))
        identifier.assert_called_once_with(ole);self.assertEqual(entry['source'],pidl);self.assertEqual(entry['size'],42)

    def test_dcim_camera_is_searched_before_movies_or_other_dcim_subfolders(self):
        opened=[]
        class Folder(Item):
            @property
            def GetFolder(self):opened.append(self.Path);return self
        dcim=Folder('DCIM',[Folder('Camera',[Item('camera.jpg')],path='DCIM/Camera'),Folder('ZZZ',[],path='DCIM/ZZZ')])
        root=Folder('Phone',[Folder('Movies',[Item('poster.jpg')]),Folder('Pictures',[]),dcim])
        first_seen=[]
        def progress(message):
            if '1 pictures found' in message and not first_seen:
                first_seen.append(True)
                self.assertNotIn('Movies',opened);self.assertNotIn('DCIM/ZZZ',opened)
        report=inventory_tree(root,progress,defer_metadata=True)
        self.assertEqual(report['image_count'],2);self.assertTrue(first_seen);self.assertFalse(report['errors']);self.assertTrue(report['images'][0]['path'].endswith('Camera/camera.jpg'))

    def test_virtual_phone_storage_is_not_skipped_for_filesystem_flag(self):
        storage=Item('Internal shared storage',[Item('DCIM',[Item('one.jpg')])],path='::{device}/Internal shared storage',filesystem=True)
        report=inventory_tree(Item('Phone',[storage]))
        self.assertEqual(report['image_count'],1);self.assertFalse(report['errors'])

    def test_drive_container_is_refused_before_opening_it(self):
        class Drive(Item):
            @property
            def GetFolder(self):raise AssertionError('Physical drive must not be opened')
        root=Item('Phone',[Drive('Drive',[],path='F:\\',filesystem=True)])
        report=inventory_tree(root)
        self.assertEqual(report['image_count'],0);self.assertTrue(any('drive container excluded' in entry['error'] for entry in report['errors']))

    def test_trace_identifies_pending_shell_call_before_it_runs(self):
        events=[]
        class BrokenFolder(Item):
            @property
            def GetFolder(self):
                self.assert_started();raise OSError('Test Shell failure')
            def assert_started(self):
                if events[-1]['event']!='start' or events[-1]['action']!='Open folder':raise AssertionError('Missing pre-call trace')
        with self.assertRaises(RuntimeError):inventory_tree(BrokenFolder('Phone',[]),trace=events.append)
        self.assertTrue(any(event['event']=='error' and event['action']=='Open folder' and 'seconds' in event for event in events))

    def test_cancel_stops_metadata_before_another_remote_property(self):
        cancel=threading.Event();accesses=[]
        class Picture:
            @property
            def Path(self):accesses.append('path');cancel.set();return 'virtual-picture'
            @property
            def Size(self):raise AssertionError('No more properties after cancellation')
        with self.assertRaises(InterruptedError):read_phone_metadata(dict(path='DCIM/one.jpg',_shell_item=Picture()),cancel=cancel)
        self.assertEqual(accesses,['path'])
