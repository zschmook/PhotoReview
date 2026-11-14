"""Verified downloads and a temporary copy of this EXE for portable updates."""
from dataclasses import dataclass
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler,Request,build_opener
import uuid

from .paths import COMPILED,DATA
from .updates import version_tuple
from .version import REPOSITORY

MAX_EXE_SIZE=512*1024*1024
ASSET_HOSTS={'github.com','release-assets.githubusercontent.com','objects.githubusercontent.com','github-releases.githubusercontent.com'}


def portable_executable():
    if not COMPILED or sys.platform!='win32':return None
    # Nuitka's sys.executable is in its extraction cache; argv[0] is the onefile EXE.
    path=Path(sys.argv[0]).absolute()
    return path if path.suffix.lower()=='.exe' and path.is_file() else None


def file_hash(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def fresh_environment():
    return {key:value for key,value in os.environ.items() if not key.startswith('NUITKA_ONEFILE_') and key!='NUITKA_ORIGINAL_ARGV0'}


def verify_update_signature(target,candidate,version=None,runner=subprocess.run):
    """An installed signed build can only update to the same trusted publisher."""
    environment={key:value for key,value in os.environ.items() if key.upper()!='PSMODULEPATH'}
    environment['PHOTO_REVIEW_OLD_EXE']=str(target);environment['PHOTO_REVIEW_NEW_EXE']=str(candidate)
    # A parent PowerShell 7 can export its own module path; let Windows
    # PowerShell load the matching built-in security module instead.
    powershell=Path(os.environ.get('WINDIR','C:/Windows'))/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe'
    environment['PSModulePath']=str(powershell.parent/'Modules')
    script="$ErrorActionPreference='Stop'; Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1'); $old=Get-AuthenticodeSignature -LiteralPath $env:PHOTO_REVIEW_OLD_EXE; $new=Get-AuthenticodeSignature -LiteralPath $env:PHOTO_REVIEW_NEW_EXE; $meta=(Get-Item -LiteralPath $env:PHOTO_REVIEW_NEW_EXE).VersionInfo; @{oldSubject=$old.SignerCertificate.Subject;newSubject=$new.SignerCertificate.Subject;newStatus=[string]$new.Status;product=$meta.ProductName;fileVersion=$meta.FileVersion;productVersion=$meta.ProductVersion}|ConvertTo-Json -Compress"
    result=runner([str(powershell),'-NoProfile','-NonInteractive','-Command',script],env=environment,text=True,capture_output=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=True)
    signatures=json.loads(result.stdout)
    if version is not None:
        expected='.'.join((version.split('.')+['0']*4)[:4])
        if signatures.get('product')!='Photo Review' or signatures.get('fileVersion')!=expected or signatures.get('productVersion')!=expected:
            raise ValueError('The downloaded EXE does not match the selected PhotoReview version')
    if signatures.get('oldSubject') and (signatures.get('newStatus')!='Valid' or signatures.get('newSubject')!=signatures['oldSubject']):
        raise ValueError('The update must have a valid signature from the installed app’s publisher')


def asset_url(url):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in ASSET_HOSTS or parsed.username or parsed.password or parsed.port not in (None,443):
        raise ValueError('Update download redirected outside GitHub release storage')
    return url


class ReleaseRedirects(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        asset_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def open_asset(url):
    asset_url(url)
    return build_opener(ReleaseRedirects()).open(Request(url,headers={'User-Agent':'PhotoReview-Updater'}),timeout=15)


@dataclass(frozen=True)
class StagedUpdate:
    target:Path
    candidate:Path
    helper:Path
    version:str
    sha256:str
    original_sha256:str


def stage_update(update,target,cache=DATA/'updates',cancel=None,progress=lambda text:None,opener=open_asset,signature_verifier=verify_update_signature):
    target=Path(target).absolute()
    if target.suffix.lower()!='.exe' or not target.is_file() or target.is_symlink():raise ValueError('The running portable EXE could not be identified')
    if target.anchor.startswith('\\\\'):raise ValueError('Automatic updates require a local, writable application folder')
    if getattr(target.stat(),'st_file_attributes',0)&1:raise PermissionError('The PhotoReview EXE is read-only; move it to a writable folder or download the update manually')
    expected=f'https://github.com/{REPOSITORY}/releases/download/v{update.version}/'
    if update.download_url!=expected+'PhotoReview.exe' or update.manifest_url!=expected+'release-manifest.json':raise ValueError('This release does not provide verified in-app updates')
    # Check write access before downloading or closing anything. No elevation is requested.
    with tempfile.NamedTemporaryFile(prefix='.photoreview-write-check-',dir=target.parent):pass
    original_hash=file_hash(target)
    with opener(update.manifest_url) as response:
        asset_url(response.geturl());body=response.read(16385)
        if len(body)>16384:raise ValueError('Update manifest exceeded its size limit')
    manifest=json.loads(body)
    if not isinstance(manifest,dict):raise ValueError('Invalid update manifest')
    size=manifest.get('size');digest=manifest.get('sha256','')
    if (manifest.get('repository')!=REPOSITORY or manifest.get('asset')!='PhotoReview.exe'
        or version_tuple(manifest.get('version'))!=version_tuple(update.version)
        or type(size) is not int or not 0<size<=MAX_EXE_SIZE
        or not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest)
        or (update.size and update.size!=size)):
        raise ValueError('Update manifest does not match the selected release')
    if cancel and cancel.is_set():raise InterruptedError('Update cancelled')
    cleanup_old_updates(cache)
    folder=Path(cache)/uuid.uuid4().hex;folder.mkdir(parents=True)
    candidate=folder/'PhotoReview.new.exe';helper=folder/'PhotoReview.update-helper.exe'
    try:
        received=0;hasher=hashlib.sha256();started=time.monotonic()
        with opener(update.download_url) as response,candidate.open('xb') as output:
            asset_url(response.geturl())
            while True:
                if cancel and cancel.is_set():raise InterruptedError('Update cancelled')
                block=response.read(256*1024)
                if not block:break
                received+=len(block)
                if received>size:raise ValueError('Update download is larger than expected')
                output.write(block);hasher.update(block)
                speed=received/max(.01,time.monotonic()-started)/1024**2
                progress(f'Downloading update: {received/1024**2:.1f} / {size/1024**2:.1f} MB · {received/size:.0%} · {speed:.1f} MB/s')
            output.flush();os.fsync(output.fileno())
        progress('Verifying downloaded EXE…')
        if received!=size or hasher.hexdigest()!=digest:raise ValueError('Update checksum verification failed; the current app was not changed')
        if cancel and cancel.is_set():raise InterruptedError('Update cancelled')
        signature_verifier(target,candidate,update.version)
        if cancel and cancel.is_set():raise InterruptedError('Update cancelled')
        shutil.copyfile(target,helper)
        if file_hash(helper)!=original_hash:raise ValueError('The running EXE changed while the update was downloading')
        return StagedUpdate(target,candidate,helper,update.version,digest,original_hash)
    except BaseException:
        for path in (candidate,helper):
            if path.is_file():path.unlink()
        folder.rmdir()
        raise


def start_helper(staged,resume=None,launcher=subprocess.Popen,parent_pid=None,smoke_report=None):
    if file_hash(staged.target)!=staged.original_sha256 or file_hash(staged.candidate)!=staged.sha256:raise ValueError('The update files changed before installation')
    plan=staged.helper.parent/'transaction.json'
    payload={'schema':1,'target':str(staged.target),'candidate':str(staged.candidate),'helper':str(staged.helper),
             'version':staged.version,'sha256':staged.sha256,'original_sha256':staged.original_sha256,
             'parent_pid':parent_pid if parent_pid is not None else os.getpid(),'resume':str(Path(resume).absolute()) if resume else None}
    plan.write_text(json.dumps(payload),encoding='utf-8')
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    args=[str(staged.helper),'--apply-update',str(plan)]
    if smoke_report:args+=['--smoke-test',str(smoke_report)]
    return launcher(args,cwd=staged.helper.parent,creationflags=flags,close_fds=True,env=fresh_environment())


def discard_staged(staged):
    for path in (staged.candidate,staged.helper,staged.helper.parent/'transaction.json'):
        if path.is_file():path.unlink()
    staged.helper.parent.rmdir()


def cleanup_old_updates(cache,keep=2):
    """Only remove known transaction files; never traverse arbitrary directories."""
    cache=Path(cache)
    if not cache.is_dir() or cache.is_symlink() or cache.is_junction():return
    completed=[]
    for folder in cache.iterdir():
        if not folder.is_dir() or folder.is_symlink() or folder.is_junction() or not re.fullmatch(r'[0-9a-f]{32}',folder.name):continue
        result=folder/'result.json'
        try:
            if result.is_symlink() or json.loads(result.read_text()).get('status')!='installed':continue
            completed.append(folder)
        except (OSError,ValueError,AttributeError):continue
    for folder in sorted(completed,key=lambda path:path.stat().st_mtime,reverse=True)[keep:]:
        try:
            for name in ('PhotoReview.new.exe','PhotoReview.update-helper.exe','transaction.json','result.json'):
                path=folder/name
                if path.is_file() and not path.is_symlink():path.unlink()
            folder.rmdir() # Unexpected files are deliberately left in place.
        except OSError:pass


def wait_for_process(pid,timeout=120):
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];kernel.WaitForSingleObject.restype=wintypes.DWORD
    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
    handle=kernel.OpenProcess(0x00100000,False,pid)
    if not handle:
        error=ctypes.get_last_error()
        if error==87:return # The app already exited before its helper opened the handle.
        raise ctypes.WinError(error)
    try:
        if kernel.WaitForSingleObject(handle,int(timeout*1000))!=0:raise TimeoutError('PhotoReview did not close; the current EXE was left unchanged')
    finally:kernel.CloseHandle(handle)


def replace_with_retry(source,destination,timeout=30):
    deadline=time.monotonic()+timeout
    while True:
        try:Path(source).replace(destination);return
        except PermissionError:
            if time.monotonic()>=deadline:raise
            time.sleep(.25)


def apply_update(plan_path,helper_path,wait=wait_for_process,launcher=subprocess.Popen,smoke_report=None):
    plan_path=Path(plan_path).absolute();helper_path=Path(helper_path).absolute()
    if plan_path.name!='transaction.json' or plan_path.parent!=helper_path.parent:raise ValueError('Invalid update transaction location')
    if plan_path.is_symlink() or helper_path.is_symlink():raise ValueError('Linked update files are not supported')
    plan=json.loads(plan_path.read_text(encoding='utf-8'))
    target=Path(plan['target']);candidate=Path(plan['candidate'])
    if (plan.get('schema')!=1 or not target.is_absolute() or target.suffix.lower()!='.exe'
        or target.is_symlink() or target==helper_path or candidate!=helper_path.parent/'PhotoReview.new.exe'
        or candidate.is_symlink() or Path(plan['helper'])!=helper_path
        or file_hash(helper_path)!=plan['original_sha256'] or file_hash(candidate)!=plan['sha256']):
        raise ValueError('Update transaction validation failed')
    if type(plan.get('parent_pid')) is not int or plan['parent_pid']<=0:raise ValueError('Invalid update parent process')
    if smoke_report:
        smoke_report=Path(smoke_report).absolute()
        if smoke_report.parent!=helper_path.parent or smoke_report.name!='startup-smoke.json':raise ValueError('Invalid updater smoke report location')
    wait(plan['parent_pid'])
    if file_hash(target)!=plan['original_sha256']:raise ValueError('The installed EXE changed; update was stopped')
    suffix=uuid.uuid4().hex
    incoming=target.with_name(target.name+'.new-'+suffix)
    backup=target.with_name(target.name+'.previous-'+suffix)
    moved=False
    try:
        shutil.copyfile(candidate,incoming)
        if file_hash(incoming)!=plan['sha256']:raise ValueError('Update copy verification failed')
        replace_with_retry(target,backup);moved=True
        replace_with_retry(incoming,target)
        args=[str(target)]
        resume=plan.get('resume')
        if resume:
            resume=Path(resume)
            if not resume.is_absolute() or resume.suffix.lower()!='.json':raise ValueError('Invalid saved review location')
            args+=['--resume-review',str(resume)]
        if smoke_report:args=[str(target),'--smoke-test',str(smoke_report)]
        process=launcher(args,cwd=target.parent,close_fds=True,env=fresh_environment())
        if smoke_report and process.wait(timeout=120)!=0:raise RuntimeError('Updated EXE failed its generated-data startup test')
    except BaseException:
        if moved:replace_with_retry(backup,target)
        raise
    finally:
        if incoming.is_file():incoming.unlink()
    # The helper is an exact copy of the previous EXE and is retained as a backup.
    backup.unlink();candidate.unlink()
    result={'status':'installed','version':plan['version'],'target':str(target),'previous_exe':str(helper_path)}
    (helper_path.parent/'result.json').write_text(json.dumps(result),encoding='utf-8')
    return result


def helper_main(plan_path,smoke_report=None):
    try:
        apply_update(plan_path,Path(sys.argv[0]).absolute(),smoke_report=smoke_report)
        return 0
    except Exception as error:
        message='PhotoReview could not finish updating. Your pictures and settings were not changed.\n\n'+str(error)+'\n\nPrevious EXE backup: '+str(Path(sys.argv[0]).absolute())
        if smoke_report:return 1
        if sys.platform=='win32':ctypes.windll.user32.MessageBoxW(None,message,'PhotoReview update',0x10)
        elif sys.stderr:print(message,file=sys.stderr)
        return 1
