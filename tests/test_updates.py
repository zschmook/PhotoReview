import copy
import io
import json
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError,URLError
from photo_review.updates import parse_release,check_latest,version_tuple,Update


def release(tag='v1.1'):
    return dict(tag_name=tag,draft=False,prerelease=False,assets=[dict(name='PhotoReview.exe',state='uploaded',size=100, browser_download_url=f'https://github.com/zschmook/PhotoReview/releases/download/{tag}/PhotoReview.exe')])


class UpdateTests(unittest.TestCase):
    def test_versions_compare_numerically_and_normalize_v1_0(self):
        self.assertEqual(version_tuple('v1.0'),version_tuple('1.0.0'))
        self.assertGreater(version_tuple('1.10'),version_tuple('1.9.9'))
        for bad in ('latest','v1.1-beta','1.0.exe','1.0<script>','1.01'):
            with self.assertRaises(ValueError):version_tuple(bad)
    def test_only_new_stable_published_exe_is_offered(self):
        self.assertEqual(parse_release(release()).version,'1.1')
        for payload in (release('v1.0'),release('v0.9'),release('v1.0.0'),release('v1.1-beta')):self.assertIsNone(parse_release(payload))
        for key,value in (('draft',True),('prerelease',True),('assets',[])):
            payload=release();payload[key]=value;self.assertIsNone(parse_release(payload))
    def test_asset_url_cannot_redirect_to_another_repo_or_site(self):
        for url in ('https://example.com/PhotoReview.exe','https://github.com/other/repo/releases/download/v1.1/PhotoReview.exe','http://github.com/zschmook/PhotoReview/releases/download/v1.1/PhotoReview.exe'):
            payload=release();payload['assets'][0]['browser_download_url']=url;self.assertIsNone(parse_release(payload))
        for key,value in (('size',0),('state','new'),('name','source.zip')):
            payload=release();payload['assets'][0][key]=value;self.assertIsNone(parse_release(payload))
    def test_one_public_request_has_timeout_and_bounded_payload(self):
        class Response(io.BytesIO):
            def geturl(self):return 'https://api.github.com/repos/zschmook/PhotoReview/releases/latest'
        calls=[]
        def opener(request,timeout):calls.append((request,timeout));return Response(json.dumps(release()).encode())
        self.assertEqual(check_latest(opener=opener).version,'1.1');self.assertEqual(len(calls),1);self.assertEqual(calls[0][1],5)
        self.assertNotIn('Authorization',dict(calls[0][0].header_items()))
        with self.assertRaisesRegex(ValueError,'size limit'):check_latest(opener=lambda *a,**kw:Response(b' '*(256*1024+1)))
    def test_no_release_and_network_failure(self):
        def missing(*a,**kw):raise HTTPError('https://api.github.com',404,'missing',{},None)
        self.assertIsNone(check_latest(opener=missing))
        def offline(*a,**kw):raise URLError('offline')
        with self.assertRaises(URLError):check_latest(opener=offline)


class StartupUpdateUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        from photo_review.ui import Window
        self.temp=tempfile.TemporaryDirectory();base=Path(self.temp.name)
        self.window=Window(preferences=base/'prefs.json');self.window.draft=base/'draft.json'
    def tearDown(self):
        if self.window.update_notice:self.window.update_notice.reject()
        self.window.close();self.app.processEvents();self.temp.cleanup()
    def finish_worker(self):
        self.assertTrue(self.window.update_worker.wait(2000));self.app.processEvents()
    def test_startup_runs_once_and_network_failure_does_not_interrupt(self):
        with patch('photo_review.updates.check_latest',side_effect=URLError('offline')) as check:
            self.window.start_update_check();self.finish_worker();self.window.start_update_check()
            self.assertEqual(check.call_count,1);self.assertIsNone(self.window.update_notice)
    def test_notice_waits_for_folder_and_only_download_click_opens_browser(self):
        from PySide6.QtCore import QTimer
        update=parse_release(release())
        self.window.update_check_complete(update);self.assertIsNone(self.window.update_notice)
        self.window.root=Path(self.temp.name);self.window.show_update_if_ready();self.assertIsNotNone(self.window.update_notice)
        with patch('PySide6.QtGui.QDesktopServices.openUrl') as opened:
            button=next(b for b in self.window.update_notice.buttons() if b.text()=='Download update')
            button.click();self.app.processEvents();self.assertEqual(opened.call_count,1)
            self.assertEqual(opened.call_args.args[0].toString(),update.download_url)
