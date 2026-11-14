import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
import numpy as np
from photo_review.core import scan,make_groups,similarity,save_draft,restore_draft,validate_plan
from photo_review.recycle import apply_plan

class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.source=self.base/'photos';self.source.mkdir();self.data=self.base/'cache'
        # Mock-recycler tests use an isolated lock path; the real safety lock stays on.
        lock_patch=patch('photo_review.recycle.REVIEW_LOCK',self.base/'REVIEW_ONLY')
        lock_patch.start();self.addCleanup(lock_patch.stop)
    def photo(self,name,time='2024:06:15 12:00:00',folder=None):
        path=(folder or self.source)/name
        image=Image.new('RGB',(200,140),'#6d98b0')
        # Enough nonuniform content to exercise orientation and hashing.
        for x in range(200):image.putpixel((x,x%140),(x,100,150))
        exif=Image.Exif()
        if time:exif[36867]=time
        image.save(path,exif=exif)
        return path
    def test_metadata_and_default_keep_with_cache(self):
        self.photo('one.jpg');self.photo('two.jpg','2024:06:15 12:00:06')
        photos,errors=scan(self.source,self.data);self.assertFalse(errors)
        self.assertTrue(all(p.keep for p in photos));self.assertTrue(all(p.time_source.startswith('EXIF') for p in photos))
        self.assertAlmostEqual(similarity(*photos),100,places=2)
        with patch('photo_review.core.Image.open',side_effect=AssertionError('cached scan decoded again')):
            cached,errors=scan(self.source,self.data)
        self.assertEqual(len(cached),2);self.assertFalse(errors)
    def test_selected_subset_never_opens_older_photos(self):
        recent=self.photo('recent.jpg');self.photo('older.jpg','2020:01:01 12:00:00')
        real=Image.open;opened=[]
        def observe(path,*args,**kwargs):
            opened.append(Path(path).name);return real(path,*args,**kwargs)
        with patch('photo_review.core.Image.open',side_effect=observe):
            photos,errors=scan(self.source,self.data,selected_paths=[str(recent)])
        self.assertEqual(opened,['recent.jpg']);self.assertEqual(len(photos),1);self.assertFalse(errors)

    def test_thumbnail_directories_are_never_opened_or_counted(self):
        self.photo('original.jpg');self.photo('thumbnails.jpg')
        album=self.source/'Holiday thumbnails';album.mkdir();self.photo('real-photo.jpg',folder=album)
        ignored=[]
        for parent,name in ((self.source,'thumbnails'),(self.source,'.THUMBNAILS'),(album,'ThUmBnAiLs')):
            folder=parent/name;folder.mkdir();self.photo('cached.jpg',folder=folder);ignored.append(folder)
        real_scandir=__import__('os').scandir;opened=[]
        def scandir(path):
            opened.append(Path(path))
            if Path(path) in ignored:raise AssertionError('Thumbnail cache was opened')
            return real_scandir(path)
        counts=[]
        with patch('photo_review.core.os.scandir',side_effect=scandir):
            photos,errors=scan(self.source,self.data,count_progress=lambda x,y:counts.append((x,y)))
            cached,cache_errors=scan(self.source,self.data)
        self.assertEqual({photo.name for photo in photos},{'original.jpg','thumbnails.jpg','real-photo.jpg'})
        self.assertEqual(len(cached),3);self.assertFalse(errors);self.assertFalse(cache_errors)
        self.assertEqual(counts[0],(0,3));self.assertFalse(set(opened)&set(ignored))

    def test_directly_selected_thumbnail_directory_is_not_walked(self):
        folder=self.source/'thumbnails';folder.mkdir();self.photo('cached.jpg',folder=folder)
        with patch('photo_review.core.os.scandir',side_effect=AssertionError('Cache root must not be opened')):
            photos,errors=scan(folder,self.data)
        self.assertEqual(photos,[]);self.assertFalse(errors)

    def test_parallel_scan_reports_progress_and_cancel(self):
        self.photo('one.jpg');self.photo('two.jpg')
        progress=[];photos,errors=scan(self.source,self.data,progress.append)
        self.assertEqual(len(photos),2);self.assertFalse(errors)
        self.assertTrue(any('/ 2 photos' in message for message in progress))
        import threading
        cancelled=threading.Event();cancelled.set()
        with self.assertRaises(InterruptedError):scan(self.source,self.data,cancel=cancelled)

    def test_time_span_and_folder_boundaries(self):
        self.photo('a.jpg','2024:06:15 12:00:00');self.photo('b.jpg','2024:06:15 12:09:00');self.photo('c.jpg','2024:06:15 12:18:00')
        other=self.source/'other';other.mkdir();self.photo('d.jpg',folder=other)
        photos,_=scan(self.source,self.data);groups=make_groups(photos,10,90)
        self.assertEqual(len(groups),1);self.assertEqual(len(groups[0].photos),2)
        self.assertTrue(all(len({p.folder for p in group.photos})==1 for group in groups))
    def test_scan_counts_include_errors_and_cached_pictures(self):
        self.photo('a.jpg');self.photo('b.jpg');bad=self.source/'bad.jpg';bad.write_bytes(b'broken')
        counts=[];photos,errors=scan(self.source,self.data,count_progress=lambda x,y:counts.append((x,y)))
        self.assertEqual(counts,[(0,3),(1,3),(2,3),(3,3)])
        self.assertEqual(len(photos),2);self.assertEqual(len(errors),1)
        bad.unlink();counts=[]
        with patch('photo_review.core.Image.open',side_effect=AssertionError('cache must avoid decoding')):
            scan(self.source,self.data,count_progress=lambda x,y:counts.append((x,y)))
        self.assertEqual(counts,[(0,2),(1,2),(2,2)])
    def test_no_transitive_similarity_chain(self):
        for name in ('a.jpg','b.jpg','c.jpg'):self.photo(name)
        photos,_=scan(self.source,self.data)
        def score(a,b):return 70 if {a.name,b.name}=={'a.jpg','c.jpg'} else 95
        with patch('photo_review.core.similarity',side_effect=score):groups=make_groups(photos)
        self.assertEqual(len(groups[0].photos),2)
    def test_fallback_timestamp_and_corrupt_file_report(self):
        self.photo('no-exif.jpg',None);(self.source/'bad.jpg').write_bytes(b'not an image')
        photos,errors=scan(self.source,self.data)
        self.assertEqual(photos[0].time_source,'File modified (fallback)');self.assertEqual(len(errors),1)
    def test_draft_only_restores_matching_fingerprints(self):
        path=self.photo('one.jpg');self.photo('two.jpg')
        photos,_=scan(self.source,self.data);groups=make_groups(photos);photos[0].keep=False
        draft=self.base/'draft.json';save_draft(draft,self.source,photos,groups,10,90)
        for p in photos:p.keep=True
        restore_draft(draft,photos,groups);self.assertFalse(photos[0].keep)
        path.write_bytes(path.read_bytes()+b'changed')
        with self.assertRaises(ValueError):validate_plan(photos,groups)
    def test_all_unchecked_is_a_valid_selection(self):
        self.photo('one.jpg');self.photo('two.jpg');photos,_=scan(self.source,self.data);groups=make_groups(photos)
        for photo in photos:photo.keep=False
        self.assertEqual(validate_plan(photos,groups),photos)
        self.assertTrue(all(Path(photo.path).exists() for photo in photos))

    def test_only_unchecked_is_submitted_and_failure_is_recorded(self):
        self.photo('one.jpg');self.photo('two.jpg');photos,_=scan(self.source,self.data);groups=make_groups(photos)
        photos[1].keep=False;calls=[]
        def recycle(path):
            calls.append(path);destination=self.base/'fake-recycled.jpg';Path(path).rename(destination);return str(destination)
        counts=[];checks=[]
        def progress(done,total,photo):
            if photo:self.assertFalse(Path(photo.path).exists())
            counts.append((done,total,photo.identity if photo else None))
        records=apply_plan(photos,groups,self.base/'journal',recycle,progress=progress,validation_progress=lambda x,y:checks.append((x,y)))
        self.assertEqual(calls,[photos[1].path]);self.assertTrue(Path(photos[0].path).exists())
        self.assertEqual(records[0]['status'],'recycled')
        self.assertEqual(counts,[(0,1,None),(1,1,photos[1].identity)])
        self.assertEqual(checks,[(0,2),(1,2),(2,2)])
    def test_changed_keeper_prevents_recycling(self):
        self.photo('one.jpg');self.photo('two.jpg');photos,_=scan(self.source,self.data);groups=make_groups(photos);photos[1].keep=False
        Path(photos[0].path).rename(self.source/'moved.jpg')
        with self.assertRaises(FileNotFoundError):apply_plan(photos,groups,self.base/'journal',recycler=lambda p:self.fail('recycler called'))

    def test_recycle_progress_counts_only_confirmed_moves_before_failure(self):
        self.photo('one.jpg');self.photo('two.jpg');photos,_=scan(self.source,self.data)
        for photo in photos:photo.keep=False
        def recycle(path):
            if str(path)==photos[1].path:raise OSError('Test failure')
            target=self.base/'recycled-test.jpg';Path(path).rename(target);return str(target)
        counts=[]
        with self.assertRaisesRegex(OSError,'Test failure'):
            apply_plan(photos,[],self.base/'journal',recycle,progress=lambda x,y,p:counts.append((x,y)))
        self.assertEqual(counts,[(0,2),(1,2)]);self.assertTrue(Path(photos[1].path).exists())
        entries=[json.loads(line) for line in (self.base/'journal').read_text().splitlines()]
        self.assertEqual(entries[-1]['status'],'failed');self.assertEqual(entries[-1]['path'],photos[1].path)

if __name__=='__main__':unittest.main()
