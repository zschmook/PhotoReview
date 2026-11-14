import hashlib
import copy
import io
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request
from photo_review.version import VERSION,REPOSITORY
from tools.release_windows import validate_tag,release_assets,release_body,publish,verify_remote_tag,request_json,ReleaseAPIError
from tools.build_windows import build_command
from tools.recover_release import validate_saved_build,REQUIRED_STEPS


class ReleaseTests(unittest.TestCase):
    def test_release_version_matches_windows_resources_and_metadata(self):
        self.assertEqual(REPOSITORY,'zschmook/PhotoReview')
        self.assertEqual(validate_tag('v'+VERSION),'v'+VERSION)
        for bad in ('v9999.0',VERSION,'v'+VERSION+'-beta','v'+VERSION+';bad'):
            with self.assertRaises(ValueError):validate_tag(bad)
        windows_version='.'.join((VERSION.split('.')+['0']*4)[:4])
        self.assertIn('--file-version='+windows_version,build_command())
        metadata=tomllib.loads((Path(__file__).resolve().parents[1]/'pyproject.toml').read_text())
        self.assertEqual(metadata['tool']['setuptools']['dynamic']['version']['attr'],'photo_review.version.VERSION')
    def test_release_notes_link_the_matching_tags_signing_policy(self):
        tag='v'+VERSION
        self.assertIn('https://github.com/zschmook/PhotoReview/blob/'+tag+'/docs/code-signing.md',release_body(tag))
        self.assertIn('## Code signing policy',release_body(tag))
    def test_release_checksums_describe_the_actual_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);exe=folder/'PhotoReview.exe';payload=b'generated fake EXE for manifest tests';exe.write_bytes(payload)
            files=release_assets(folder);digest=hashlib.sha256(payload).hexdigest()
            self.assertEqual([p.name for p in files],['PhotoReview.exe','SHA256SUMS.txt','release-manifest.json'])
            self.assertEqual((folder/'SHA256SUMS.txt').read_text(),digest+'  PhotoReview.exe\n')
            manifest=json.loads((folder/'release-manifest.json').read_text());self.assertEqual(manifest['sha256'],digest);self.assertEqual(manifest['size'],len(payload))
            self.assertEqual(exe.read_bytes(),payload)
    def test_release_requires_an_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):release_assets(temporary)
    def test_existing_remote_tag_publishes_without_workflow_write_permission(self):
        head='a'*40;tag='v'+VERSION;calls=[]
        def opener(request,timeout):
            path=request.selector;method=request.get_method();payload=json.loads(request.data) if request.data and 'uploads.github.com' not in request.full_url else None
            calls.append((method,path,payload))
            if path.endswith('/git/ref/tags/'+tag):result={'object':{'type':'commit','sha':head}}
            elif path.endswith('/releases/tags/'+tag):raise HTTPError(request.full_url,404,'Not Found',{},io.BytesIO(b'{}'))
            elif method=='POST' and path.endswith('/releases'):
                self.assertNotIn('target_commitish',payload);result={'id':42,'draft':True,'assets':[]}
            elif method=='PATCH':result={'html_url':'https://github.com/zschmook/PhotoReview/releases/tag/'+tag}
            else:result={}
            return io.BytesIO(json.dumps(result).encode())
        with tempfile.TemporaryDirectory() as temporary:
            fake=Path(temporary)/'PhotoReview.exe';fake.write_bytes(b'tested EXE')
            with patch('tools.release_windows.subprocess.check_output',return_value=head),patch('tools.release_windows.release_assets',return_value=[fake]),patch('tools.release_windows.urlopen',side_effect=opener),patch('builtins.print'):
                publish(tag,'test-token')
        self.assertEqual(sum('/git/ref/tags/' in path for _,path,_ in calls),2)
        self.assertEqual(calls[-1][0],'PATCH')
    def test_remote_tag_movement_and_missing_tag_stop_publication(self):
        head='a'*40
        with self.assertRaisesRegex(ValueError,'no longer matches'):
            verify_remote_tag(lambda *a:{'object':{'type':'commit','sha':'b'*40}},'v'+VERSION,head)
        def missing(*args):raise HTTPError('https://api.github.com',404,'Not Found',{},io.BytesIO(b'{}'))
        with self.assertRaisesRegex(ValueError,'already exist'):
            verify_remote_tag(missing,'v'+VERSION,head)
        calls=[]
        def annotated(method,path):
            calls.append(path)
            return {'object':{'type':'tag','sha':'b'*40}} if len(calls)==1 else {'object':{'type':'commit','sha':head}}
        verify_remote_tag(annotated,'v'+VERSION,head);self.assertEqual(len(calls),2)
    def test_permission_error_reports_the_operation_and_redacts_credentials(self):
        request=Request('https://api.github.com/repos/zschmook/PhotoReview/releases',data=b'{}',method='POST')
        error=HTTPError(request.full_url,403,'Forbidden',{},io.BytesIO(b'{"message":"Resource not accessible by integration test-token"}'))
        with patch('tools.release_windows.urlopen',side_effect=error):
            with self.assertRaises(ReleaseAPIError) as caught:request_json(request,60,'test-token')
        self.assertIn('POST /repos/zschmook/PhotoReview/releases: HTTP 403',str(caught.exception))
        self.assertIn('Resource not accessible by integration',str(caught.exception));self.assertNotIn('test-token',str(caught.exception))
    def test_recovery_requires_a_matching_tested_unexpired_artifact(self):
        tag='v'+VERSION;head='a'*40
        valid={'run':{'repository':{'full_name':REPOSITORY},'path':'.github/workflows/release-windows.yml','status':'completed','event':'push','head_sha':head},
               'jobs':{'jobs':[{'steps':[{'name':name,'conclusion':'success'} for name in REQUIRED_STEPS]}]},
               'artifacts':{'artifacts':[{'id':123,'name':'photoreview-tested-'+tag,'expired':False,'size_in_bytes':100}]}}
        def api_for(data):
            return lambda path:data['jobs'] if '/jobs?' in path else data['artifacts'] if '/artifacts?' in path else data['run']
        self.assertEqual(validate_saved_build(tag,'42',api_for(valid),head),123)
        cases=[]
        for key,value in (('repository',{'full_name':'other/repo'}),('head_sha','b'*40),('status','in_progress'),('event','pull_request')):
            data=copy.deepcopy(valid);data['run'][key]=value;cases.append(data)
        data=copy.deepcopy(valid);data['jobs']['jobs'][0]['steps'][0]['conclusion']='failure';cases.append(data)
        data=copy.deepcopy(valid);data['artifacts']['artifacts'][0]['expired']=True;cases.append(data)
        data=copy.deepcopy(valid);data['artifacts']['artifacts'][0]['name']='unrelated';cases.append(data)
        for data in cases:
            with self.subTest(data=data),self.assertRaises(ValueError):validate_saved_build(tag,'42',api_for(data),head)
    def test_recovery_does_not_add_a_broken_policy_link_to_an_older_tag(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'RELEASE_NOTES.md').write_text('Original release notes\n')
            with patch('tools.release_windows.ROOT',root):self.assertEqual(release_body('v'+VERSION),'Original release notes\n')
