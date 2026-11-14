"""Copy-only MTP downloads; phone cleanup is a separate confirmed operation."""
from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import sqlite3
import sys
import tempfile
import uuid
from time import monotonic
from PIL import Image
from .core import reject_links,EXTENSIONS
from .phone_import import find_phone_shell_item,inventory_tree,read_phone_metadata,read_capture_date,is_drive_path,timed_call,IMAGE_EXTENSIONS,VIDEO_EXTENSIONS
from .import_history import ImportHistory,ContentIndex
from .phones import is_apple_phone
from .heic import HEIC_EXTENSIONS,jpeg_copy

LAYOUTS={'day-tree':'%Y/%m/%d','month-tree':'%Y/%m','day-folder':'%Y-%m-%d'}


def companion_stems(paths):
    result=set()
    for path in paths:
        stem=PurePosixPath(path).with_suffix('');result.add(str(stem).casefold())
        if stem.suffix.casefold() in IMAGE_EXTENSIONS|VIDEO_EXTENSIONS:result.add(str(stem.with_suffix('')).casefold())
    return result

def safe_name(value):
    original=str(value);clean=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',original).rstrip(' .')
    if not clean or clean in ('.','..'):clean='unnamed'
    if clean.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:clean='_'+clean
    if len(clean)>100:clean=clean[:80]+Path(clean).suffix[:12]
    if clean!=original:
        path=Path(clean);clean=path.stem+'-'+hashlib.sha256(original.encode()).hexdigest()[:8]+path.suffix
    return clean

def default_destination(device,working_root):return Path(working_root)/safe_name(device.name)

def destination_inside_root(destination,working_root):
    root=Path(working_root).absolute();target=Path(destination).absolute()
    if '..' in target.parts or not target.is_relative_to(root) or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Phone pictures must stay inside the selected library folder. Use File → Change folder to select another library.')
    return target

def checked_folder(path):
    path=Path(path).absolute()
    for part in (path,*path.parents):
        if part.exists() or part.is_symlink():reject_links(part)
    path.mkdir(parents=True,exist_ok=True);reject_links(path)
    return path

def digest(path,cancel=None,progress=None):
    value=hashlib.sha256();size=0;last=monotonic()
    with Path(path).open('rb') as stream:
        while True:
            if cancel and cancel.is_set():raise InterruptedError('Phone import cancelled')
            chunk=stream.read(1024*1024)
            if not chunk:break
            value.update(chunk);size+=len(chunk)
            if progress and monotonic()-last>=.2:progress(size);last=monotonic()
    return value.hexdigest()


def phone_checksum(source,cancel=None,progress=None):
    # Share the native, read-only SHA-256 stream reader with cleanup verification.
    from .phone_cleanup import phone_digest
    return phone_digest(source,cancel,progress)


def import_status(report):
    def media(total,videos):
        text=f"{total-videos:,} {'image' if total-videos==1 else 'images'}"
        if report.get('include_videos') or report.get('video_total',0):text+=f" / {videos:,} {'video' if videos==1 else 'videos'}"
        return text
    return (f"Already exist on PC: {media(report['skipped'],report.get('videos_skipped',0))}\n"
            f"Newly downloaded: {media(report['copied'],report.get('videos_copied',0))}")

def picture_date(path,entry):
    # Decode only the local staged copy. EXIF takes precedence over Shell dates.
    fallback=entry.get('taken') or entry.get('modified')
    is_video=PurePosixPath(entry['path']).suffix.lower() in VIDEO_EXTENSIONS
    if not is_video:
        try:
            with Image.open(path) as image:
                exif=image.getexif()
                try:nested=exif.get_ifd(0x8769)
                except (ValueError,TypeError,AttributeError):nested={}
                for tag in (36867,36868):
                    raw=nested.get(tag) or exif.get(tag)
                    if raw:
                        try:return datetime.strptime(str(raw).strip('\x00 '),'%Y:%m:%d %H:%M:%S'),'EXIF date taken'
                        except ValueError:pass
        except (OSError,ValueError,Image.DecompressionBombError):pass
    if fallback:
        try:return datetime.fromtimestamp(fallback),('Phone video date' if is_video else 'Phone date taken') if entry.get('taken') else 'Phone modified time'
        except (ValueError,OverflowError,OSError):pass
    return None,'Unknown date'

def phone_shell_item(source):
    """Resolve a virtual phone file while rejecting physical filesystem aliases."""
    if sys.platform!='win32':raise RuntimeError('Phone downloads require Windows')
    from win32com.shell import shell,shellcon
    if isinstance(source,(list,tuple)):
        item=shell.SHCreateItemFromIDList(source,shell.IID_IShellItem)
    else:
        if is_drive_path(source):raise ValueError('Phone import refused a filesystem source')
        item=shell.SHCreateItemFromParsingName(source,None,shell.IID_IShellItem)
    if item.GetAttributes(shellcon.SFGAO_FILESYSTEM):
        try:physical=item.GetDisplayName(shellcon.SIGDN_FILESYSPATH)
        except Exception:physical=''
        namespace=source if isinstance(source,str) else item.GetDisplayName(shellcon.SIGDN_DESKTOPABSOLUTEPARSING)
        if is_drive_path(physical) or not str(namespace).startswith('::{'):raise ValueError('Phone import refused a filesystem source')
    return item


def copy_shell_file(source,destination,cancel=None,progress=None):
    """One Windows Shell COPY into a fresh local staging file."""
    import pythoncom
    from win32com.shell import shell,shellcon
    from win32com.server.exception import COMException
    from send2trash.win.IFileOperationProgressSink import FileOperationProgressSink
    item=phone_shell_item(source)
    target=Path(destination)
    if target.exists():raise FileExistsError(target)
    if progress is not None:
        # The same read-only stream used for checksums also provides real byte
        # progress for long videos; Shell IFileOperation counters are work units.
        context=pythoncom.CreateBindCtx();options=context.GetBindOptions();options['Mode']=0;context.SetBindOptions(options)
        stream=item.BindToHandler(context,shell.BHID_Stream,pythoncom.IID_IStream)
        copied=0;last=monotonic()
        with target.open('xb') as output:
            while True:
                if cancel and cancel.is_set():raise InterruptedError('Phone import cancelled')
                block=stream.Read(1024*1024)
                if not block:break
                output.write(block);copied+=len(block)
                if monotonic()-last>=.2:progress(copied);last=monotonic()
        progress(copied);return
    class Sink(FileOperationProgressSink):
        def PreCopyItem(self,flags,item,folder,name):
            if cancel and cancel.is_set():raise COMException(desc='Import cancelled',scode=-2147467260)
            return 0
        def UpdateProgress(self,total,done):
            if cancel and cancel.is_set():raise COMException(desc='Import cancelled',scode=-2147467260)
        def PostCopyItem(self,flags,item,folder,name,result,newly_created):
            if result<0 or not newly_created:raise COMException(desc='Windows could not confirm the copy',scode=-2147467259)
            self.newItem=newly_created.GetDisplayName(shellcon.SIGDN_FILESYSPATH)
        def PreDeleteItem(self,*args):raise COMException(desc='Phone deletion refused',scode=-2147467260)
        def PreMoveItem(self,*args):raise COMException(desc='Phone move refused',scode=-2147467260)
    sink=Sink();wrapped=pythoncom.WrapObject(sink,shell.IID_IFileOperationProgressSink)
    operation=pythoncom.CoCreateInstance(shell.CLSID_FileOperation,None,pythoncom.CLSCTX_ALL,shell.IID_IFileOperation)
    operation.SetOperationFlags(shellcon.FOF_NOERRORUI|shellcon.FOF_SILENT|shellcon.FOF_NOCONFIRMATION|shellcon.FOFX_EARLYFAILURE)
    folder=shell.SHCreateItemFromParsingName(str(target.parent),None,shell.IID_IShellItem)
    operation.CopyItem(item,folder,target.name,wrapped)
    result=operation.PerformOperations()
    if result or operation.GetAnyOperationsAborted() or not sink.newItem:raise OSError('Phone copy interrupted or unconfirmed')
    if Path(sink.newItem).resolve()!=target.resolve():raise OSError('Windows copied to an unexpected destination')

def import_entries(entries,device,destination,database,layout='day-tree',progress=lambda text:None,counts=lambda x,y:None,cancel=None,copier=copy_shell_file,trace=None,reader=None,source_kind='phone'):
    if source_kind not in ('phone','usb'):raise ValueError('Unknown import source')
    if layout not in LAYOUTS and not (source_kind=='usb' and layout=='flat'):raise ValueError('Unknown date-folder layout')
    source_label='phone' if source_kind=='phone' else 'USB'
    root=checked_folder(destination);database=Path(database);checked_folder(database.parent)
    video_total=sum(PurePosixPath(entry['path']).suffix.lower() in VIDEO_EXTENSIONS for entry in entries)
    report=dict(destination=str(root),layout=layout,total=len(entries),checked=0,new_total=0,checksum_algorithm='SHA-256',video_total=video_total,picture_total=len(entries)-video_total,videos_copied=0,videos_skipped=0,copied=0,skipped=0,bytes=0,cancelled=False,errors=[],files=[],unsupported_review=0,cleanup_candidates=[])
    report['source_kind']=source_kind;report['jpeg_copies']=0
    def stopped():
        if cancel and cancel.is_set():report['cancelled']=True
        return report['cancelled'] or report.get('stopped')
    def cleanup_candidate(entry,local,key,size,sha):
        if source_kind!='phone':return
        source=entry.get('source')
        if isinstance(source,(list,tuple)) and source and all(isinstance(part,bytes) for part in source):
            report['cleanup_candidates'].append(dict(device_id=device.identity,phone_path=entry['path'],source_pidl=[part.hex() for part in source],local_path=str(local),size=size,sha256=sha,import_identity=key))
    def skip(entry,record,key):
        history.remember(key,record['path'],record['size'],record['sha256'],'Existing PC file (SHA-256 match)')
        cleanup_candidate(entry,record['path'],key,record['size'],record['sha256']);report['skipped']+=1
        if PurePosixPath(entry['path']).suffix.lower() in VIDEO_EXTENSIONS:report['videos_skipped']+=1
    with closing(sqlite3.connect(database)) as db:
        history=ImportHistory(db);prepared=[];failures=0;counts(0,len(entries));last=monotonic()
        progress(f'Reading {source_label} details · 0 of {len(entries):,} files')
        for number,original in enumerate(entries,1):
            if stopped():return report
            try:
                relative=PurePosixPath(original['path'])
                if relative.is_absolute() or '..' in relative.parts:raise ValueError('Invalid phone path')
                entry=read_phone_metadata(original,trace,cancel,include_taken=False)
                prepared.append((entry,original.get('_shell_item')));failures=0
            except Exception as error:
                if stopped():return report
                report['errors'].append(dict(path=original.get('path',''),error=str(error)));failures+=1
                if failures>=3:report['stopped']='Stopped after three consecutive file-check errors.';return report
            if number%32==0 or monotonic()-last>=.2:
                progress(f'Reading {source_label} details · {number:,} of {len(entries):,} files');last=monotonic()
        if stopped():return report
        # Local bytes are cached only while their filesystem signature matches.
        # A known MTP source may reuse its earlier checksum for repeat imports;
        # cleanup always performs a separate fresh read of every phone original.
        sizes={entry.get('size') for entry,_ in prepared}
        local_path=None
        def local_progress(number,path):
            nonlocal local_path
            local_path=path
            progress(f"Checking PC checksums · {number:,} files checked"+(f' · {path.name}' if path else ''))
        def hash_local(path):
            return digest(path,cancel,lambda size:progress(f'Reading PC checksum: {path.name} · {size/1024**2:,.1f} MiB read'))
        content=ContentIndex(db,root,hash_local,cancel)
        progress('Checking PC checksums · 0 files checked')
        try:report['pc_files_checked']=content.scan(IMAGE_EXTENSIONS|(VIDEO_EXTENSIONS if video_total else set()),None if None in sizes else sizes,local_progress)
        except Exception as error:
            if stopped():return report
            report['errors'].append(dict(path=str(local_path or root),error=str(error)));report['stopped']='Could not finish comparing files in the target folder. Nothing was downloaded.';return report
        queue=[];pending={};failures=0;last=monotonic();report['cached_sources']=0
        def checking():progress(f"Checking {source_label} checksums · {report['checked']:,} of {len(entries):,} files · {report['skipped']:,} already exist on PC")
        checking()
        for entry,shell_item in prepared:
            if stopped():db.commit();return report
            report['checked']+=1
            try:
                relative=PurePosixPath(entry['path'])
                source_key=history.source_key(device,entry) if source_kind=='phone' else None
                cached_sha=history.source_checksum(source_key)
                cached_record=content.find(entry['size'],cached_sha) if cached_sha else None
                def read_source():
                    if reader is not None:return reader(entry['source'],cancel)
                    return phone_checksum(entry['source'],cancel,lambda size:progress(f'Reading phone checksum: {relative.name} · {size/1024**2:,.1f} MiB read'))
                if cached_record is not None:
                    size,sha=entry['size'],cached_sha;report['cached_sources']+=1
                else:
                    size,sha=timed_call(trace,f'Check {source_label} checksum',entry['path'],read_source)
                if size<=0:raise OSError('Phone file is empty')
                if entry.get('size') is not None and size!=entry['size']:raise OSError('Phone checksum size does not match the reported size')
                history.remember_source(source_key,sha)
                entry=dict(entry,size=size)
                key=hashlib.sha256(json.dumps([device.identity,str(root),entry['path'],size,sha],ensure_ascii=False).encode()).hexdigest()
                existing=content.find(size,sha)
                if existing is not None:skip(entry,existing,key)
                elif (size,sha) in pending:pending[(size,sha)]['aliases'].append((entry,key))
                else:
                    if shell_item is not None:entry['taken']=read_capture_date(shell_item,entry['path'],trace,cancel)
                    group=dict(entry=entry,key=key,sha256=sha,aliases=[]);queue.append(group);pending[(size,sha)]=group
                failures=0
            except Exception as error:
                if stopped():db.commit();return report
                report['errors'].append(dict(path=entry.get('path',''),error=str(error)));failures+=1
                if failures>=3:report['stopped']='Stopped after three consecutive checksum errors.';break
            if report['checked']%32==0 or monotonic()-last>=.2:
                db.commit();checking();last=monotonic()
        db.commit();checking();report['new_total']=len(queue)
        report['duplicate_phone_files']=sum(len(group['aliases']) for group in queue)
        report['duplicate_source_files']=report['duplicate_phone_files']
        if stopped():return report
        progress(f"Import plan · {len(queue):,} new files to download · {report['skipped']:,} already exist on PC")
        counts(0,len(queue))
        if stopped():return report
        if not queue:
            progress('No new files to download.' if not report['errors'] else 'No files queued for download. See the import report for issues.')
            return report
        with tempfile.TemporaryDirectory(prefix='.phone-import-',dir=root) as staging:
            failures=0
            for number,group in enumerate(queue,1):
                if stopped():break
                entry=group['entry'];key=group['key'];relative=PurePosixPath(entry['path']);is_video=relative.suffix.lower() in VIDEO_EXTENSIONS
                try:
                    existing=content.find(entry['size'],group['sha256'])
                    if existing is not None:
                        skip(entry,existing,key)
                        for alias,alias_key in group['aliases']:skip(alias,existing,alias_key)
                    else:
                        progress('Downloading '+relative.name)
                        stage=Path(staging)/(uuid.uuid4().hex+relative.suffix)
                        def copy_new_file():
                            if copier is copy_shell_file:
                                return copier(entry['source'],stage,cancel,progress=lambda size:progress(f"Transfer bytes · {report['bytes']+size}"))
                            return copier(entry['source'],stage,cancel)
                        timed_call(trace,'Copy phone video' if is_video else 'Copy phone picture',str(relative),copy_new_file)
                        reject_links(stage)
                        if not stage.is_file() or stage.stat().st_size!=entry['size']:raise OSError('Downloaded size does not match the phone checksum')
                        sha=digest(stage,cancel)
                        if sha!=group['sha256']:raise OSError('Downloaded checksum does not match the phone; original retained')
                        date,date_source=(None,'USB file') if layout=='flat' else picture_date(stage,entry)
                        media_root=root/'Videos' if is_video else root
                        folder=checked_folder(media_root if layout=='flat' else media_root/(date.strftime(LAYOUTS[layout]) if date else 'Unknown date'))
                        target=folder/safe_name(relative.name);stem,suffix=target.stem,target.suffix;collision=0
                        while True:
                            if target.exists():
                                collision+=1;target=folder/f'{stem}-{key[:8]}-{collision}{suffix}';continue
                            try:stage.rename(target);break # Windows rename never overwrites another file.
                            except FileExistsError:collision+=1;target=folder/f'{stem}-{key[:8]}-{collision}{suffix}'
                        record=content.checksum(target)
                        if record['sha256']!=sha:raise OSError('Saved copy changed before import completed')
                        content.add(record);history.remember(key,target,entry['size'],sha,date_source)
                        report['copied']+=1;report['bytes']+=entry['size'];report['files'].append(dict(path=str(target),size=entry['size'],sha256=sha,date_source=date_source))
                        progress(f"Transfer bytes · {report['bytes']}")
                        cleanup_candidate(entry,target,key,entry['size'],sha)
                        if relative.suffix.lower() in HEIC_EXTENSIONS:
                            progress('Creating JPEG copy: '+target.name)
                            jpeg,created=jpeg_copy(target,database.parent/'heic-conversions.sqlite3',cancel)
                            report['jpeg_copies']+=int(created)
                        if is_video:report['videos_copied']+=1
                        elif relative.suffix.lower() not in EXTENSIONS:report['unsupported_review']+=1
                        for alias,alias_key in group['aliases']:skip(alias,record,alias_key)
                    db.commit();failures=0
                except Exception as error:
                    if stopped():break
                    report['errors'].append(dict(path=entry.get('path',''),error=str(error)));failures+=1
                    if failures>=3:report['stopped']='Stopped after three consecutive copy errors.';break
                counts(number,len(queue))
                progress('Import status · '+import_status(report).replace('\n',' · '))
    stopped();return report

def download_phone(device,destination,database,layout='day-tree',progress=lambda text:None,counts=lambda x,y:None,cancel=None,working_root=None,include_videos=False):
    if sys.platform!='win32':raise RuntimeError('Phone downloads require Windows')
    if working_root is not None:destination=destination_inside_root(destination,working_root)
    import pythoncom
    from win32com.client.dynamic import Dispatch
    pythoncom.CoInitialize()
    log=None
    try:
        database=Path(database);checked_folder(database.parent)
        log=(database.parent/'phone-discovery-trace.jsonl').open('w',encoding='utf-8');started=monotonic()
        def trace(record):
            if log.closed:return
            log.write(json.dumps(dict(elapsed=round(monotonic()-started,4),**record),ensure_ascii=False)+'\n');log.flush()
            if record['event']=='start':progress(record['action']+': '+record['path'])
        progress('Locating '+device.name+' in Windows…')
        item=find_phone_shell_item(Dispatch('Shell.Application'),device,trace=trace)
        inventory=inventory_tree(item,progress,cancel,include_sources=True,defer_metadata=True,trace=trace,include_videos=include_videos,device=device)
        trace(dict(event='summary',action='Discovery complete',path=device.name,image_count=inventory['image_count'],selected_folders=inventory.get('selected_folders',[])))
        entries=inventory.get('media',inventory['images'])
        if not inventory.get('storage_available',inventory['folders']>1) and not entries:
            instruction='Unlock it and allow photo access or trust this computer' if is_apple_phone(device) else 'Unlock it and select USB File transfer'
            raise RuntimeError('The phone is detected but its storage is not available yet. '+instruction+', then use File → Download phone pictures… to try again.')
        report=import_entries(entries,device,destination,database,layout,progress,counts,cancel,trace=trace)
        report['include_videos']=bool(include_videos)
        report['errors']=inventory['errors']+report['errors'];report['device']=device.name;report['device_id']=device.identity
        backed_paths={candidate['phone_path'].casefold() for candidate in report['cleanup_candidates']}
        report['unbacked_companion_paths']=[path for path in inventory.get('companion_paths',[]) if path.casefold() not in backed_paths]
        companions=companion_stems(report['unbacked_companion_paths'])
        protected=[candidate for candidate in report['cleanup_candidates'] if str(PurePosixPath(candidate['phone_path']).with_suffix('')).casefold() in companions]
        report['cleanup_protected']=len(protected)
        report['verification_candidates']=list(report['cleanup_candidates'])
        report['cleanup_candidates']=[candidate for candidate in report['cleanup_candidates'] if str(PurePosixPath(candidate['phone_path']).with_suffix('')).casefold() not in companions]
        report['discovery']={key:value for key,value in inventory.items() if key not in ('images','videos','media','errors')}
        return report
    finally:
        if log:log.close()
        pythoncom.CoUninitialize()
