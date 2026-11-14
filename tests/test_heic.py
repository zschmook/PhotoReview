import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from PIL import Image,ImageOps,ImageChops,ImageStat
from photo_review.heic import jpeg_copy,convert_folder
from photo_review.core import scan,make_groups
from photo_review.phone_download import import_entries
from photo_review.phones import PortableDevice


class HeicTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.album=self.base/'album';self.album.mkdir();self.db=self.base/'cache'/'conversion.sqlite3'
    def photo(self,name='IMG_0001.HEIC'):
        path=self.album/name;image=Image.new('RGB',(100,60),'navy')
        for x in range(50):
            for y in range(30):image.putpixel((x,y),(210,170,40))
        exif=Image.Exif();exif[36867]='2024:02:03 12:30:00';exif[274]=6
        image.save(path,format='HEIF',quality=95,exif=exif)
        return path
    def test_real_heic_converts_upright_preserves_exif_original_and_reuses_copy(self):
        source=self.photo();before=source.read_bytes();target,created=jpeg_copy(source,self.db)
        self.assertTrue(created);self.assertEqual(target.name,'IMG_0001.HEIC.jpg')
        with Image.open(source) as original,Image.open(target) as jpeg:
            expected=ImageOps.exif_transpose(original).convert('RGB')
            self.assertEqual(jpeg.format,'JPEG');self.assertEqual(jpeg.size,expected.size)
            self.assertEqual(jpeg.getexif().get(36867),'2024:02:03 12:30:00')
            self.assertEqual(jpeg.getexif().get(274),1)
            self.assertLess(max(ImageStat.Stat(ImageChops.difference(expected,jpeg)).mean),5)
        self.assertEqual(source.read_bytes(),before)
        self.assertEqual(source.stat().st_mtime_ns,target.stat().st_mtime_ns)
        with patch('photo_review.heic.Image.open',side_effect=AssertionError('Cached conversion decoded again')):
            self.assertEqual(jpeg_copy(source,self.db),(target,False))
    def test_existing_or_changed_outputs_are_never_overwritten(self):
        source=self.photo();collision=source.with_name(source.name+'.jpg');collision.write_bytes(b'keep this user file')
        first,_=jpeg_copy(source,self.db);self.assertNotEqual(first,collision)
        first.write_bytes(b'user edited this copy')
        second,created=jpeg_copy(source,self.db)
        self.assertTrue(created);self.assertNotEqual(first,second)
        self.assertEqual(first.read_bytes(),b'user edited this copy');self.assertEqual(collision.read_bytes(),b'keep this user file')
    def test_nested_iphone_capture_metadata_and_color_profile_survive(self):
        from PIL import ImageCms
        source=self.album/'nested.HEIC';exif=Image.Exif()
        exif[34665]={36867:'2025:06:07 08:09:10',37521:'123',36881:'-04:00'}
        profile=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
        Image.new('RGB',(80,60),'purple').save(source,format='HEIF',exif=exif,icc_profile=profile)
        target,_=jpeg_copy(source,self.db)
        with Image.open(target) as jpeg:
            nested=jpeg.getexif().get_ifd(34665)
            self.assertEqual(nested[36867],'2025:06:07 08:09:10');self.assertEqual(nested[36881],'-04:00')
            self.assertEqual(jpeg.info['icc_profile'],profile)
        photos,errors=scan(self.album,self.base/'nested-cache');self.assertFalse(errors)
        self.assertEqual(photos[0].timestamp,photos[1].timestamp)
    def test_cancel_and_corrupt_input_do_not_publish_partial_jpegs(self):
        source=self.photo();cancel=threading.Event();cancel.set()
        with self.assertRaises(InterruptedError):jpeg_copy(source,self.db,cancel)
        broken=self.album/'broken.HEIC';broken.write_bytes(b'broken')
        with self.assertRaises(OSError):jpeg_copy(broken,self.db)
        self.assertEqual(list(self.album.glob('*.jpg')),[]);self.assertEqual(list(self.album.glob('*.tmp')),[])
        self.assertTrue(source.exists());self.assertEqual(broken.read_bytes(),b'broken')
    def test_source_changed_during_conversion_does_not_publish_copy(self):
        source=self.photo()
        from photo_review.heic import signature
        initial=signature(source)
        with patch('photo_review.heic.signature',side_effect=[initial,tuple([initial[0]+1,*initial[1:]])]):
            with self.assertRaisesRegex(ValueError,'changed during conversion'):jpeg_copy(source,self.db)
        self.assertEqual(list(self.album.glob('*.jpg')),[]);self.assertEqual(list(self.album.glob('*.tmp')),[])
    def test_folder_conversion_skips_thumbnails_and_reviewer_matches_heic(self):
        self.photo();self.photo('IMG_0002.heif')
        cache=self.album/'.thumbnails';cache.mkdir();(cache/'bad.heic').write_bytes(b'not an original')
        report=convert_folder(self.album,self.db)
        self.assertEqual(report['converted'],2);self.assertFalse(report['errors'])
        self.assertEqual(convert_folder(self.album,self.db)['existing'],2)
        photos,errors=scan(self.album,self.base/'review-cache');self.assertFalse(errors);self.assertEqual(len(photos),4)
        self.assertTrue(all(p.time_source.startswith('EXIF') for p in photos))
        heic=[p for p in photos if Path(p.path).suffix.lower() in {'.heic','.heif'}]
        self.assertEqual(len(make_groups(heic,10,90)),1)
        self.assertTrue(all(Path(p.thumbnail).suffix=='.jpg' for p in photos))
    def test_phone_import_keeps_original_checksum_and_separate_jpeg_copy(self):
        source=self.photo();data=source.read_bytes();sha=hashlib.sha256(data).hexdigest();pidl=[b'fake-phone',b'fake-photo']
        entry=dict(path='Internal Storage/202402_a/IMG_0001.HEIC',source=pidl,size=len(data),taken=None,modified=None)
        device=PortableDevice('USB\\VID_05AC&PID_12A8\\fixture','Apple iPhone');destination=self.base/'imports';db=self.base/'profile'/'imports.sqlite3'
        def copier(identifier,target,cancel):self.assertEqual(identifier,pidl);Path(target).write_bytes(data)
        reader=lambda identifier,cancel:(len(data),sha)
        result=import_entries([entry],device,destination,db,copier=copier,reader=reader)
        self.assertFalse(result['errors']);self.assertEqual(result['copied'],1);self.assertEqual(result['jpeg_copies'],1);self.assertEqual(result['unsupported_review'],0)
        candidate=result['cleanup_candidates'][0]
        self.assertEqual(candidate['sha256'],sha);self.assertEqual(Path(candidate['local_path']).read_bytes(),data)
        self.assertEqual(len(list(destination.rglob('*.jpg'))),1)
        repeated=import_entries([entry],device,destination,db,copier=lambda *args:self.fail('Original was downloaded twice'),reader=reader)
        self.assertFalse(repeated['errors']);self.assertEqual(repeated['skipped'],1);self.assertEqual(repeated['jpeg_copies'],0)
