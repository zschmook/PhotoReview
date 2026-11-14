"""Validate a version tag and publish a tested EXE to a draft-first release."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from photo_review.version import VERSION,REPOSITORY
from photo_review.updates import version_tuple


class ReleaseAPIError(RuntimeError):
    pass


def request_json(request,timeout,token):
    try:
        with urlopen(request,timeout=timeout) as response:body=response.read()
        return json.loads(body) if body else None
    except HTTPError as error:
        if error.code==404:raise
        try:message=json.loads(error.read(16384)).get('message','')
        except (ValueError,AttributeError):message=''
        message=' '.join(str(message).replace(token,'[redacted]').split())[:500]
        path=request.selector.split('?',1)[0]
        raise ReleaseAPIError(f'{request.get_method()} {path}: HTTP {error.code}'+(': '+message if message else '')) from None


def validate_tag(tag):
    if not re.fullmatch(r'v\d+\.\d+(?:\.\d+)?',tag) or version_tuple(tag)!=version_tuple(VERSION):
        raise ValueError(f'Release tag must match app version {VERSION}; got {tag!r}')
    return tag


def release_body(tag):
    validate_tag(tag)
    notes=(ROOT/'RELEASE_NOTES.md').read_text(encoding='utf-8').rstrip()
    policy=f'https://github.com/{REPOSITORY}/blob/{quote(tag,safe="")}/docs/code-signing.md'
    return notes+'\n\n## Code signing policy\n\nSee the [code signing policy]('+policy+') for provider status, release approval, and privacy information.\n'


def release_assets(dist=ROOT/'dist'):
    dist=Path(dist);exe=dist/'PhotoReview.exe'
    if not exe.is_file() or exe.stat().st_size<=0:raise ValueError('A built PhotoReview.exe is required')
    with exe.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    checksum=dist/'SHA256SUMS.txt';checksum.write_text(digest+'  PhotoReview.exe\n',encoding='ascii')
    manifest=dist/'release-manifest.json'
    manifest.write_text(json.dumps(dict(version=VERSION,repository=REPOSITORY,asset='PhotoReview.exe',sha256=digest,size=exe.stat().st_size),indent=2)+'\n',encoding='utf-8')
    return [exe,checksum,manifest]


def verify_remote_tag(api,tag,head):
    try:reference=api('GET','/git/ref/tags/'+quote(tag,safe=''))
    except HTTPError as error:
        if error.code==404:raise ValueError('The release tag must already exist on GitHub') from None
        raise
    target=reference.get('object',{})
    for _ in range(10):
        sha=target.get('sha','')
        if not re.fullmatch(r'[0-9a-f]{40,64}',sha):raise ValueError('Invalid remote tag target')
        if target.get('type')=='commit':
            if sha!=head:raise ValueError('Remote release tag no longer matches the tested checkout')
            return
        if target.get('type')!='tag':raise ValueError('Remote release tag does not resolve to a commit')
        target=api('GET','/git/tags/'+sha).get('object',{})
    raise ValueError('Remote release tag nesting exceeds the supported limit')


def publish(tag,token):
    validate_tag(tag)
    if not token:raise ValueError('GITHUB_TOKEN is required for publishing')
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    tagged=subprocess.check_output(['git','rev-list','-n','1',tag],cwd=ROOT,text=True).strip()
    if head!=tagged:raise ValueError('Build checkout does not match the release tag')
    files=release_assets()
    def api(method,path,payload=None):
        data=json.dumps(payload).encode() if payload is not None else None
        request=Request('https://api.github.com/repos/'+REPOSITORY+path,data=data,method=method,headers={
            'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','Content-Type':'application/json',
            'X-GitHub-Api-Version':'2026-03-10','User-Agent':'PhotoReview-Release'})
        return request_json(request,60,token)
    verify_remote_tag(api,tag,head)
    try:release=api('GET','/releases/tags/'+quote(tag,safe=''))
    except HTTPError as error:
        if error.code!=404:raise
        # The remote tag above already pins the tested commit. Supplying its SHA
        # as target_commitish needlessly triggers workflow-write permission checks
        # if main's workflow changes while this build is running.
        release=api('POST','/releases',dict(tag_name=tag,name='Photo Review '+tag,body=release_body(tag),draft=True,prerelease=False))
    if not release.get('draft'):raise ValueError('This release is already published; bump the version instead of replacing it')
    release_id=int(release['id'])
    for asset in release.get('assets',[]):
        if asset.get('name') in {file.name for file in files}:api('DELETE','/releases/assets/'+str(int(asset['id'])))
    for file in files:
        # GitHub release assets use a fixed host; never trust an upload URL from metadata.
        request=Request(f'https://uploads.github.com/repos/{REPOSITORY}/releases/{release_id}/assets?name={quote(file.name)}',data=file.read_bytes(),method='POST',headers={
            'Authorization':'Bearer '+token,'Content-Type':'application/octet-stream','User-Agent':'PhotoReview-Release'})
        request_json(request,300,token)
    verify_remote_tag(api,tag,head)
    result=api('PATCH','/releases/'+str(release_id),dict(draft=False,make_latest='true'))
    print('Published: '+result['html_url'])


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--tag',required=True);parser.add_argument('--validate',action='store_true')
    args=parser.parse_args()
    try:
        validate_tag(args.tag)
        if args.validate:print('Validated '+args.tag);return 0
        publish(args.tag,os.environ.get('GITHUB_TOKEN',''));return 0
    except (OSError,ValueError,ReleaseAPIError,subprocess.CalledProcessError) as error:
        print('Release failed: '+str(error),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
