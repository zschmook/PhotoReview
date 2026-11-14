"""GPU path tests use generated arrays, never personal photos or recycling."""
import threading
import unittest
from unittest.mock import patch
import numpy as np
from photo_review.core import Photo,make_groups,similarity

def pictures(count):
    rng=np.random.default_rng(37)
    return [Photo(f'C:/generated/month/{i:03}.jpg',str(i),1,1,1,i,96,96,float(i),'generated',0,rng.integers(0,256,(96,96),dtype=np.uint8)) for i in range(count)]

class FallbackTests(unittest.TestCase):
    def test_oversized_folder_rejected_before_statistics_allocation(self):
        from types import SimpleNamespace
        from photo_review.gpu import GPUScorer
        from unittest.mock import Mock
        device=Mock();device.limits={'max-storage-buffer-binding-size':1}
        adapter=Mock();adapter.info={'adapter_type':'DiscreteGPU'};adapter.request_device_sync.return_value=device
        with patch.dict('sys.modules',{'wgpu':SimpleNamespace()}),patch('photo_review.gpu.gaussian_filter') as blur:
            with self.assertRaisesRegex(RuntimeError,'storage limit'):GPUScorer(pictures(40),adapter=adapter)
            blur.assert_not_called();device.destroy.assert_called_once()
    def test_initialization_failure_uses_cpu(self):
        photos=pictures(40);messages=[]
        with patch('photo_review.gpu.GPUScorer',side_effect=RuntimeError('unavailable')):
            actual=make_groups(photos,43200,90,backend='auto',progress=messages.append)
        expected=make_groups(photos,43200,90)
        self.assertEqual([[p.identity for p in g.photos] for g in actual],[[p.identity for p in g.photos] for g in expected])
        self.assertTrue(any('CPU fallback' in m for m in messages))
    def test_dispatch_failure_falls_back_and_releases_resources(self):
        photos=pictures(40)
        with patch('photo_review.gpu.GPUScorer') as cls,patch('photo_review.core.similarity',return_value=0):
            cls.return_value.info={'backend_type':'test'}
            cls.return_value.score_pairs.side_effect=RuntimeError('device lost')
            self.assertEqual(make_groups(photos,43200,60,backend='gpu'),[])
            cls.return_value.close.assert_called_once()
    def test_cancel_before_work_avoids_gpu_allocation(self):
        cancelled=threading.Event();cancelled.set()
        with patch('photo_review.gpu.GPUScorer') as cls:
            cls.return_value.info={'backend_type':'test'}
            with self.assertRaises(InterruptedError):make_groups(pictures(40),43200,60,cancelled,backend='gpu')
            cls.assert_not_called()
    def test_dense_short_time_range_uses_gpu_and_releases_it(self):
        photos=pictures(40)
        with patch('photo_review.gpu.GPUScorer') as cls,patch('photo_review.core.similarity',return_value=0):
            cls.return_value.info={'backend_type':'Vulkan'}
            cls.return_value.score_pairs.side_effect=lambda pairs,cancel:[0]*len(pairs)
            self.assertEqual(make_groups(photos,10,60,backend='auto'),[])
            cls.assert_called_once_with(photos);self.assertTrue(cls.return_value.score_pairs.called)
            cls.return_value.close.assert_called_once()
    def test_sparse_work_does_not_allocate_gpu_even_for_long_time_range(self):
        photos=pictures(40)
        with patch('photo_review.gpu.GPUScorer') as cls,patch('photo_review.core.similarity',return_value=100):
            groups=make_groups(photos,43200,60,backend='auto')
            cls.assert_not_called();self.assertEqual(len(groups),1)
    def test_cancellation_after_gpu_initialization_releases_resources(self):
        photos=pictures(40);cancelled=threading.Event()
        def dispatch(*args):cancelled.set();raise InterruptedError('cancelled fixture')
        with patch('photo_review.gpu.GPUScorer') as cls,patch('photo_review.core.similarity',return_value=0):
            cls.return_value.info={'backend_type':'Vulkan'};cls.return_value.score_pairs.side_effect=dispatch
            with self.assertRaises(InterruptedError):make_groups(photos,10,60,cancelled,backend='auto')
            cls.return_value.close.assert_called_once()
    def test_threshold_guard_uses_reference_score(self):
        photos=pictures(40)
        # 32 dissimilar anchors, then one borderline pair the GPU underestimates.
        def reference(a,b):return 60 if {a.identity,b.identity}=={'32','0'} else 0
        with patch('photo_review.gpu.GPUScorer') as cls,patch('photo_review.core.similarity',side_effect=reference):
            cls.return_value.info={'backend_type':'test'}
            cls.return_value.score_pairs.side_effect=lambda pairs,cancel:[59.99 if {a.identity,b.identity}=={'32','0'} else 0 for a,b in pairs]
            groups=make_groups(photos,43200,60,backend='gpu')
            self.assertEqual([[p.identity for p in g.photos] for g in groups],[['0','32']])

class HardwareTests(unittest.TestCase):
    def test_real_compute_matches_cpu_across_batches(self):
        from photo_review.gpu import GPUScorer
        photos=pictures(8);photos[0].gray[:]=0;photos[1].gray[:]=255;photos[2].gray=photos[3].gray.copy()
        try:scorer=GPUScorer(photos)
        except Exception as error:self.skipTest(f'Hardware GPU unavailable: {error}')
        try:
            pairs=[(photos[i%8],photos[(i//8)%8]) for i in range(300)]
            actual=scorer.score_pairs(pairs)
            self.assertEqual(len(actual),300)
            self.assertLessEqual(max(abs(score-similarity(a,b)) for score,(a,b) in zip(actual,pairs)),.02)
            cancelled=threading.Event();cancelled.set()
            with self.assertRaises(InterruptedError):scorer.score_pairs(pairs,cancelled)
        finally:scorer.close()
