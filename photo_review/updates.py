"""One startup request for a newer, published Windows GitHub release."""
from dataclasses import dataclass
import json
import re
from urllib.error import HTTPError
from urllib.parse import quote,urlsplit
from urllib.request import Request,urlopen
from .version import VERSION,REPOSITORY


def version_tuple(value):
    match=re.fullmatch(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?',str(value))
    if not match:raise ValueError('Expected a stable version such as v1.0 or v1.0.1')
    return tuple(int(part or 0) for part in match.groups())


@dataclass(frozen=True)
class Update:
    version:str
    release_url:str
    download_url:str
    release_notes:str=''
    manifest_url:str=''
    size:int=0


def parse_release(release,current=VERSION,repository=REPOSITORY):
    if not isinstance(release,dict) or release.get('draft') is not False or release.get('prerelease') is not False:return None
    tag=release.get('tag_name','')
    try:newer=version_tuple(tag)>version_tuple(current)
    except ValueError:return None
    if not newer:return None
    assets=release.get('assets',[])
    if not isinstance(assets,list):return None
    expected=f'https://github.com/{repository}/releases/download/{quote(tag,safe="")}/PhotoReview.exe'
    for asset in assets:
        if not isinstance(asset,dict) or asset.get('name')!='PhotoReview.exe' or asset.get('state')!='uploaded':continue
        if not isinstance(asset.get('size'),int) or asset['size']<=0:continue
        if asset.get('browser_download_url')!=expected:continue
        notes=release.get('body')
        notes=notes[:16000].strip() if isinstance(notes,str) else ''
        manifest_url=f'https://github.com/{repository}/releases/download/{quote(tag,safe="")}/release-manifest.json'
        has_manifest=any(isinstance(a,dict) and a.get('name')=='release-manifest.json' and a.get('state')=='uploaded' and a.get('browser_download_url')==manifest_url for a in assets)
        return Update(str(tag).removeprefix('v'),f'https://github.com/{repository}/releases/tag/{quote(tag,safe="")}',expected,notes,manifest_url if has_manifest else '',asset['size'])
    return None


def check_latest(current=VERSION,repository=REPOSITORY,opener=urlopen):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository):raise ValueError('Invalid release repository')
    request=Request(f'https://api.github.com/repos/{repository}/releases/latest',headers={
        'Accept':'application/vnd.github+json','User-Agent':f'PhotoReview/{current}',
        'X-GitHub-Api-Version':'2026-03-10'})
    try:
        with opener(request,timeout=5) as response:
            if urlsplit(response.geturl()).hostname!='api.github.com':raise ValueError('Unexpected release API redirect')
            body=response.read(256*1024+1)
            if len(body)>256*1024:raise ValueError('Release metadata exceeded size limit')
        return parse_release(json.loads(body),current,repository)
    except HTTPError as error:
        if error.code==404:return None # No published release yet.
        raise
