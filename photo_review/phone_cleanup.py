"""Explicit phone-original removal after exact byte verification of backups."""
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
from .core import reject_links
from .phone_download import destination_inside_root,phone_shell_item,companion_stems
from .phone_import import SOURCE_FOLDERS,IMAGE_EXTENSIONS,VIDEO_EXTENSIONS
from .phones import PortableDevice,enumerate_portable_devices,same_physical_phone,is_apple_phone
from .scan_policy import is_thumbnail_directory


def cleanup_available(report):
    return (report.get('source_kind','phone')=='phone' and not report.get('cancelled') and not report.get('errors') and not report.get('stopped')
            and isinstance(report.get('total'),int) and report['total']>0
            and report.get('copied',0)+report.get('skipped',0)==report['total']
            and bool(report.get('cleanup_candidates')))


def verification_plan(device,report,working_root):
    payload=dict(device_id=device.identity.casefold(),root=str(Path(working_root).resolve()),destination=str(Path(report['destination']).resolve()),total=report['total'],include_videos=bool(report.get('include_videos',report.get('video_total',0)>0)),files=report.get('verification_candidates',report['cleanup_candidates']),removals=report['cleanup_candidates'],companions=report.get('unbacked_companion_paths',report.get('discovery',{}).get('companion_paths',[])))
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()


def verification_complete(verification,device,report,working_root):
    return (isinstance(verification,dict) and verification.get('mode')=='verification'
            and not verification.get('errors') and not verification.get('cancelled')
            and verification.get('verified')==report.get('total')
            and verification.get('total')==report.get('total')
            and verification.get('plan')==verification_plan(device,report,working_root))


def verify_phone_cleanup(device,download_report,working_root,journal,progress=lambda text:None,counts=lambda x,y:None,cancel=None,confirmed=False,reader=None,devices=enumerate_portable_devices):
    """After the first confirmation, checksum the whole batch without deleting."""
    return _process_phone_cleanup(device,download_report,working_root,journal,progress,counts,cancel,confirmed,reader or phone_digest,delete_phone_file,devices,verify_only=True)


def cleanup_phone(device,download_report,working_root,journal,progress=lambda text:None,counts=lambda x,y:None,cancel=None,confirmed=False,reader=None,deleter=None,devices=enumerate_portable_devices,verification=None):
    """Require the full checksum result and second confirmation, then recheck."""
    if not confirmed:raise ValueError('Phone cleanup requires explicit confirmation')
    if not cleanup_available(download_report):raise ValueError('A complete, error-free download is required before phone cleanup')
    if not verification_complete(verification,device,download_report,working_root):
        raise ValueError('Successful checksum verification of every downloaded picture is required before the final deletion confirmation')
    return _process_phone_cleanup(device,download_report,working_root,journal,progress,counts,cancel,confirmed,reader or phone_digest,deleter or delete_phone_file,devices)


def source_identifier(candidate):
    value=candidate.get('source_pidl')
    if not isinstance(value,list) or not value or any(not isinstance(part,str) or not part for part in value):
        raise ValueError('Missing phone file identifier; original retained')
    return [bytes.fromhex(part) for part in value]


@contextmanager
def locked_backup(path,root):
    """Hold a read-only Windows handle that denies writes and deletion."""
    import msvcrt
    import win32file,win32con
    target=destination_inside_root(path,root);reject_links(target)
    handle=win32file.CreateFile(str(target),win32con.GENERIC_READ,win32con.FILE_SHARE_READ,None,win32con.OPEN_EXISTING,win32file.FILE_FLAG_OPEN_REPARSE_POINT|win32con.FILE_FLAG_SEQUENTIAL_SCAN,None)
    try:
        if win32file.GetFileInformationByHandle(handle)[0]&0x400:raise ValueError('Linked backup refused')
        final=win32file.GetFinalPathNameByHandle(handle,0)
        if final.startswith('\\\\?\\UNC\\'):final='\\\\'+final[8:]
        elif final.startswith('\\\\?\\'):final=final[4:]
        if not Path(final).is_relative_to(Path(root).resolve()):raise ValueError('Backup is outside the download folder')
        descriptor=msvcrt.open_osfhandle(handle.Detach(),os.O_RDONLY|os.O_BINARY)
        with os.fdopen(descriptor,'rb') as stream:
            reject_links(target);yield stream
    finally:handle.Close()


def backup_digest(stream,cancel=None):
    value=hashlib.sha256();size=0
    while True:
        if cancel and cancel.is_set():raise InterruptedError('Phone cleanup cancelled')
        block=stream.read(1024*1024)
        if not block:return size,value.hexdigest()
        value.update(block);size+=len(block)


def phone_digest(source,cancel=None,progress=None):
    import pythoncom
    from win32com.shell import shell
    from time import monotonic
    if cancel and cancel.is_set():raise InterruptedError('Phone checksum cancelled')
    item=phone_shell_item(source)
    context=pythoncom.CreateBindCtx();options=context.GetBindOptions();options['Mode']=0;context.SetBindOptions(options)
    stream=item.BindToHandler(context,shell.BHID_Stream,pythoncom.IID_IStream)
    value=hashlib.sha256();size=0;last=monotonic()
    while True:
        if cancel and cancel.is_set():raise InterruptedError('Phone checksum cancelled')
        block=stream.Read(1024*1024)
        if not block:return size,value.hexdigest()
        value.update(block);size+=len(block)
        if progress and monotonic()-last>=.2:progress(size);last=monotonic()


def delete_phone_file(source,cancel=None,before_delete=lambda:None):
    """Delete one exact virtual leaf; never delete folders or physical files."""
    import pythoncom
    from win32com.shell import shell,shellcon
    from win32com.server.exception import COMException
    from send2trash.win.IFileOperationProgressSink import FileOperationProgressSink
    if not isinstance(source,(list,tuple)) or not source:raise ValueError('An absolute phone identifier is required')
    if cancel and cancel.is_set():raise InterruptedError('Phone cleanup cancelled')
    item=phone_shell_item(source)
    if item.GetAttributes(shellcon.SFGAO_FOLDER|shellcon.SFGAO_LINK):raise ValueError('Phone folder/link deletion refused')
    if not item.GetAttributes(shellcon.SFGAO_CANDELETE):raise ValueError('The phone does not allow deleting this picture over USB')
    class Sink(FileOperationProgressSink):
        confirmed=False
        def PreDeleteItem(self,flags,actual):
            if cancel and cancel.is_set():raise COMException(desc='Phone cleanup cancelled',scode=-2147467260)
            if shell.SHGetIDListFromObject(actual)!=list(source):raise COMException(desc='Unexpected phone deletion target',scode=-2147467260)
            before_delete();return 0
        def PostDeleteItem(self,flags,actual,result,newly_created):
            if result!=0:raise COMException(desc='Phone deletion was not confirmed',scode=-2147467259)
            self.confirmed=True
        def PreCopyItem(self,*args):raise COMException(desc='Unexpected copy refused',scode=-2147467260)
        def PreMoveItem(self,*args):raise COMException(desc='Unexpected move refused',scode=-2147467260)
        def PreRenameItem(self,*args):raise COMException(desc='Unexpected rename refused',scode=-2147467260)
    before_delete()
    sink=Sink();wrapped=pythoncom.WrapObject(sink,shell.IID_IFileOperationProgressSink)
    operation=pythoncom.CoCreateInstance(shell.CLSID_FileOperation,None,pythoncom.CLSCTX_ALL,shell.IID_IFileOperation)
    operation.SetOperationFlags(shellcon.FOF_NOERRORUI|shellcon.FOF_SILENT|shellcon.FOF_NOCONFIRMATION|shellcon.FOFX_EARLYFAILURE|shellcon.FOF_NORECURSION)
    operation.DeleteItem(item,wrapped)
    result=operation.PerformOperations()
    if result or operation.GetAnyOperationsAborted() or not sink.confirmed:raise OSError('Phone deletion interrupted or unconfirmed')


def _process_phone_cleanup(device,download_report,working_root,journal,progress,counts,cancel,confirmed,reader,deleter,devices,verify_only=False):
    if not confirmed:raise ValueError('Phone cleanup requires explicit confirmation')
    if not cleanup_available(download_report):raise ValueError('A complete, error-free download is required before phone cleanup')
    journal=Path(journal)
    if (journal.parent/'PHONE_DELETE_DISABLED').exists():raise ValueError('Phone cleanup is disabled in this profile')
    destination=destination_inside_root(download_report['destination'],working_root)
    candidates=download_report.get('verification_candidates',download_report['cleanup_candidates']) if verify_only else download_report['cleanup_candidates']
    if verify_only and len(candidates)!=download_report['total']:raise ValueError('Every downloaded picture must be included in checksum verification')
    if verify_only:
        checked={hashlib.sha256(json.dumps(candidate,sort_keys=True).encode()).digest() for candidate in candidates}
        if any(hashlib.sha256(json.dumps(candidate,sort_keys=True).encode()).digest() not in checked for candidate in download_report['cleanup_candidates']):
            raise ValueError('Every deletion candidate must be included in checksum verification')
    protected=companion_stems(download_report.get('unbacked_companion_paths',download_report.get('discovery',{}).get('companion_paths',[])))
    report=dict(mode='verification' if verify_only else 'cleanup',plan=verification_plan(device,download_report,working_root),device=device.name,device_id=device.identity,total=len(candidates),verified=0,deleted=0,bytes=0,cancelled=False,errors=[],files=[])
    import pythoncom
    pythoncom.CoInitialize()
    try:
        def connected():
            if cancel and cancel.is_set():raise InterruptedError('Phone cleanup cancelled')
            if not any(same_physical_phone(device,current) for current in devices()):raise OSError('The original phone is disconnected; cleanup stopped')
        if cancel and cancel.is_set():report['cancelled']=True;return report
        connected();journal.parent.mkdir(parents=True,exist_ok=True)
        with journal.open('a',encoding='utf-8') as log:
            def record(state,candidate,**extra):
                value=dict(time=datetime.now(timezone.utc).isoformat(),state=state,device_id=device.identity,phone_path=candidate.get('phone_path'),local_path=candidate.get('local_path'),**extra)
                log.write(json.dumps(value,ensure_ascii=False)+'\n');log.flush();os.fsync(log.fileno())
            counts(0,len(candidates))
            seen=set()
            for index,candidate in enumerate(candidates):
                if cancel and cancel.is_set():report['cancelled']=True;break
                try:
                    connected()
                    if candidate.get('device_id','').casefold()!=device.identity.casefold():raise ValueError('Download belongs to a different phone')
                    path=PurePosixPath(candidate['phone_path'])
                    allowed=IMAGE_EXTENSIONS|(VIDEO_EXTENSIONS if download_report.get('include_videos',download_report.get('video_total',0)>0) else set())
                    if path.is_absolute() or '..' in path.parts or path.suffix.lower() not in allowed:raise ValueError('Invalid phone media path')
                    if not is_apple_phone(device) and not any(part.casefold() in {name.casefold() for name in SOURCE_FOLDERS}|{'downloads'} for part in path.parts[:2]):raise ValueError('Phone picture is outside the selected public folders')
                    if any(is_thumbnail_directory(part) for part in path.parts[:-1]):raise ValueError('Thumbnail deletion refused')
                    if not verify_only and str(path.with_suffix('')).casefold() in protected:raise ValueError('Photo has an uncopied video/edit companion; original retained')
                    identifier=source_identifier(candidate);key=tuple(identifier)
                    if key in seen:raise ValueError('Duplicate phone file identifier')
                    seen.add(key)
                    if not isinstance(candidate.get('size'),int) or candidate['size']<=0 or not re.fullmatch('[0-9a-f]{64}',candidate.get('sha256','')):raise ValueError('Incomplete backup verification record')
                    expected=(candidate['size'],candidate['sha256'])
                    progress('Checking saved copy: '+path.name)
                    with locked_backup(candidate['local_path'],destination) as backup:
                        if backup_digest(backup,cancel)!=expected:raise ValueError('Saved copy changed or is incomplete; phone original retained')
                        progress('Verifying phone original: '+path.name)
                        if reader(identifier,cancel)!=expected:raise ValueError('Phone original does not exactly match its saved copy; original retained')
                        connected();record('verified',candidate,sha256=expected[1],size=expected[0]);report['verified']+=1
                        if verify_only:
                            report['bytes']+=expected[0];report['files'].append(candidate['phone_path'])
                        else:
                            progress('Deleting from phone: '+path.name)
                            record('planned',candidate)
                            deleter(identifier,cancel,connected)
                            # Record confirmed deletion even if Cancel was pressed
                            # while Windows was completing this one operation.
                            report['deleted']+=1;report['bytes']+=expected[0];report['files'].append(candidate['phone_path'])
                            record('deleted',candidate)
                    counts(index+1,len(candidates))
                except Exception as error:
                    if cancel and cancel.is_set():report['cancelled']=True
                    else:report['errors'].append(dict(path=candidate.get('phone_path',''),error=str(error)))
                    try:record('stopped',candidate,error=str(error))
                    except OSError:pass
                    break
        return report
    finally:pythoncom.CoUninitialize()
