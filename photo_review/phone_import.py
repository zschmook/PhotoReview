"""Read-only media discovery with device-specific folder scope."""
from pathlib import PurePosixPath,PureWindowsPath
from datetime import datetime
import sys
from time import monotonic
from .scan_policy import is_thumbnail_directory
from .phones import PortableDevice,is_apple_phone

IMAGE_EXTENSIONS={'.jpg','.jpeg','.png','.webp','.bmp','.tif','.tiff','.gif','.heic','.heif','.heics','.heifs','.hif','.avif','.dng','.cr2','.cr3','.nef','.arw','.raf','.rw2'}
VIDEO_EXTENSIONS={'.mp4','.mov','.m4v','.3gp','.3g2','.webm','.mkv','.avi','.mts','.m2ts','.mpg','.mpeg','.wmv'}
COMPANION_EXTENSIONS=VIDEO_EXTENSIONS|{'.aae','.xmp'}
SOURCE_FOLDERS=('DCIM','Pictures','Download','Documents','Movies')
_SOURCE_NAMES={name.casefold() for name in SOURCE_FOLDERS}|{'downloads'}
_OTHER_PUBLIC_FOLDERS={'android','alarms','audiobooks','music','notifications','podcasts','recordings','ringtones'}

def phone_file_name(item,display_name):
    """Shell .Name is a display label and may hide even a photo's extension."""
    try:value=item.ExtendedProperty('System.FileName')
    except Exception:value=None
    if isinstance(value,str) and value:return value
    try:extension=item.ExtendedProperty('System.FileExtension')
    except Exception:extension=None
    if isinstance(extension,str) and extension.startswith('.') and not display_name.casefold().endswith(extension.casefold()):return display_name+extension
    return display_name


def phone_file_size(item,is_video=False,cancel=None):
    value=None
    if is_video:
        try:value=item.ExtendedProperty('System.Size') # UInt64, including clips over 4 GiB.
        except Exception:pass
    if cancel and cancel.is_set():raise InterruptedError('Phone import cancelled')
    if value is None:value=item.Size
    size=int(value)
    return size if size>0 else None

def iter_shell_items(items,cancel=None,trace=None,path=''):
    """Read distinct items with Next(1), reporting progress in 32-entry chunks.

    The live MTP provider aliases every object in Next(n) to the final item,
    and also discards short results. Never request multiple native items.
    Yield immediately so the first photo is reported before the chunk ends.
    """
    ole=getattr(items,'_oleobj_',None);enum=None
    if ole is not None:
        import pythoncom
        from win32com.client.dynamic import Dispatch
        enum=timed_call(trace,'Open directory enumerator',path,lambda:ole.InvokeTypes(pythoncom.DISPID_NEWENUM,0,pythoncom.DISPATCH_METHOD|pythoncom.DISPATCH_PROPERTYGET,(13,10),()).QueryInterface(pythoncom.IID_IEnumVARIANT))
    else:iterator=iter(items)
    end=object()
    while True:
        if cancel and cancel.is_set():raise InterruptedError('Phone discovery cancelled; nothing was downloaded')
        started=monotonic()
        if trace:trace(dict(event='start',action='Read directory chunk',path=path))
        try:
            for _ in range(32):
                if cancel and cancel.is_set():raise InterruptedError('Phone discovery cancelled; nothing was downloaded')
                if enum is None:item=next(iterator,end)
                else:
                    value=enum.Next(1)
                    item=Dispatch(value[0]) if value else end
                if item is end:return
                yield item
        finally:
            if trace:trace(dict(event='finish',action='Read directory chunk',path=path,seconds=round(monotonic()-started,4)))

def timed_call(trace,action,path,call):
    started=monotonic()
    if trace:trace(dict(event='start',action=action,path=str(path)))
    try:
        value=call()
    except Exception as error:
        if trace:trace(dict(event='error',action=action,path=str(path),seconds=round(monotonic()-started,4),error=str(error)))
        raise
    if trace:trace(dict(event='finish',action=action,path=str(path),seconds=round(monotonic()-started,4)))
    return value

def is_drive_path(value):
    text=str(value)
    if text.startswith('::{') or text.casefold().startswith(('\\\\?\\usb#','\\\\?\\wpd#')):return False
    return bool(PureWindowsPath(text).drive)

def read_capture_date(item,path,trace=None,cancel=None):
    if cancel and cancel.is_set():raise InterruptedError('Phone import cancelled')
    date_property='System.Media.DateEncoded' if PurePosixPath(path).suffix.lower() in VIDEO_EXTENSIONS else 'System.Photo.DateTaken'
    try:
        value=timed_call(trace,'Read taken date',path,lambda:item.ExtendedProperty(date_property))
        return value.timestamp() if isinstance(value,datetime) else None
    except InterruptedError:raise
    except Exception:return None


def read_phone_metadata(entry,trace=None,cancel=None,include_taken=True):
    """Fetch basic import metadata, optionally deferring capture-date lookup."""
    if '_shell_item' not in entry:return entry
    result={key:value for key,value in entry.items() if key!='_shell_item'};item=entry['_shell_item'];path=entry['path']
    def read(action,call):
        if cancel and cancel.is_set():raise InterruptedError('Phone import cancelled')
        return timed_call(trace,action,path,call)
    ole=getattr(item,'_oleobj_',None)
    if ole is not None:
        from win32com.shell import shell
        # FolderItem.Path for MTP is not necessarily a parseable Shell name.
        # Preserve the actual absolute identifier instead of reparsing text.
        result['source']=read('Read phone file identifier',lambda:shell.SHGetIDListFromObject(ole))
    else:result['source']=str(read('Read source path',lambda:item.Path))
    try:
        result['size']=read('Read size',lambda:phone_file_size(item,PurePosixPath(path).suffix.lower() in VIDEO_EXTENSIONS,cancel))
    except InterruptedError:raise
    except Exception:result['size']=None
    result['taken']=read_capture_date(item,path,trace,cancel) if include_taken else None
    try:
        value=read('Read modified date',lambda:item.ModifyDate)
        result['modified']=value.timestamp() if isinstance(value,datetime) else None
    except InterruptedError:raise
    except Exception:result['modified']=None
    return result

def find_phone_shell_item(shell,device,trace=None):
    from .phones import PortableDevice,same_physical_phone
    computer=timed_call(trace,'Open Windows device list','This PC',lambda:shell.NameSpace(17))
    if computer is None:raise RuntimeError('Windows could not open the portable-device list')
    exact=[];named=[]
    items=timed_call(trace,'List Windows devices','This PC',computer.Items)
    if trace:trace(dict(event='start',action='Enumerate Windows devices',path='This PC'))
    for item in items:
        if timed_call(trace,'Check device item type','This PC',lambda:item.IsFileSystem):continue # Never open a drive.
        name=str(timed_call(trace,'Read device name','This PC',lambda:item.Name))
        try:identity=timed_call(trace,'Read device identity',name,lambda:item.ExtendedProperty('System.Devices.DeviceInstanceId'))
        except Exception:identity=None
        if identity:
            if same_physical_phone(PortableDevice(str(identity),name),device):exact.append(item)
            # A known different device must never be selected by model name.
        elif name.casefold()==device.name.casefold():named.append(item)
    if trace:trace(dict(event='finish',action='Enumerate Windows devices',path='This PC'))
    matches=exact or named
    if len(matches)!=1:raise RuntimeError('Could not uniquely locate the phone in Windows. Unlock it, select File transfer, and reconnect if necessary.')
    return matches[0]

def inventory_tree(root,progress=lambda text:None,cancel=None,include_sources=False,defer_metadata=False,trace=None,include_videos=False,device=None):
    """Scan iPhone exposed storage, or Android's selected public folders.

    iPhones can expose date folders directly under Internal Storage. Android
    app folders and siblings of its selected folders are never opened.
    """
    apple=is_apple_phone(device or PortableDevice('',str(root.Name)))
    seen=set();images=[];videos=[];media=[];errors=[];ignored_folders=[];companion_paths=[];folders=0;known_bytes=0;unknown_sizes=0;last_progress=0
    def cancelled():
        if cancel and cancel.is_set():raise InterruptedError('Phone discovery cancelled; nothing was downloaded')
    def update(parts,force=False):
        nonlocal last_progress
        now=monotonic()
        if force or now-last_progress>=.2:
            size_text='size checked during import' if defer_metadata else f'{known_bytes/1024**3:.2f} GiB known size'
            heading='Finding phone media' if include_videos else 'Finding phone pictures' if include_sources else 'Phone download preview'
            picture_word='picture' if len(images)==1 else 'pictures';video_word='video' if len(videos)==1 else 'videos';file_word='file' if len(media)==1 else 'files'
            found=f'{len(media):,} {file_word} found ({len(images):,} {picture_word}, {len(videos):,} {video_word})' if include_videos else f'{len(images):,} pictures found'
            progress(f"{heading} · {found} · {'/'.join(parts) or 'phone storage'} · {size_text} · nothing copied")
            last_progress=now
    def children(item,parts):
        nonlocal folders
        cancelled()
        location='/'.join(parts) or 'phone storage'
        if parts and is_thumbnail_directory(parts[-1]):
            ignored_folders.append(location)
            if trace:trace(dict(event='skip',action='Skip thumbnail folder',path=location))
            return
        update(parts,True)
        key=str(timed_call(trace,'Read folder path',location,lambda:item.Path)) or '/'.join(parts)
        if key in seen:return
        if is_drive_path(key):
            errors.append({'path':location,'error':'Filesystem drive container excluded'});return
        seen.add(key)
        folder=timed_call(trace,'Open folder',location,lambda:item.GetFolder)
        items=timed_call(trace,'List folder',location,folder.Items);folders+=1
        started=monotonic()
        if trace:trace(dict(event='start',action='Enumerate folder entries',path=location))
        try:
            for index,child in enumerate(iter_shell_items(items,cancel=cancel,trace=trace,path=location),1):
                cancelled();yield child
                update(parts,index%32==0)
        finally:
            if trace:trace(dict(event='finish',action='Enumerate folder entries',path=location,seconds=round(monotonic()-started,4)))
    # A device may expose the public folders directly, or expose one or more
    # storage volumes (internal storage / SD card) containing those folders.
    selected=[]
    if apple:
        selected=[(root,(),0)]
    else:
        top=[]
        try:
            for child in children(root,()):
                try:
                    if child.IsFolder:top.append((child,str(child.Name)))
                except Exception as error:errors.append({'path':'','error':str(error)})
        except InterruptedError:raise
        except Exception as error:errors.append({'path':'','error':str(error)})
        if folders==0:raise RuntimeError('Phone storage is unavailable. Unlock the phone and select File transfer.')
        if any(name.casefold() in _SOURCE_NAMES|_OTHER_PUBLIC_FOLDERS for _,name in top):
            selected=[(item,(name,),1) for item,name in top if name.casefold() in _SOURCE_NAMES]
        else:
            for storage,name in top:
                try:
                    for child in children(storage,(name,)):
                        try:
                            child_name=str(child.Name)
                            if child_name.casefold() in _SOURCE_NAMES and child.IsFolder:selected.append((child,(name,child_name),2))
                        except Exception as error:errors.append({'path':name,'error':str(error)})
                except InterruptedError:raise
                except Exception as error:errors.append({'path':name,'error':str(error)})
    priority={name.casefold():i for i,name in enumerate(SOURCE_FOLDERS)};priority['downloads']=priority['download']
    stack=list(selected) if apple else sorted(selected,key=lambda entry:priority[entry[1][-1].casefold()],reverse=True)
    while stack:
        cancelled()
        item,parts,depth=stack.pop()
        if depth>64:
            errors.append({'path':'/'.join(parts),'error':'Folder nesting limit reached'});continue
        try:
            nested=[]
            for child in children(item,parts):
                try:
                    name=str(child.Name);relative=parts+(name,)
                    if child.IsFolder:nested.append((child,relative,depth+1));continue
                    name=phone_file_name(child,name);relative=parts+(name,)
                    extension=PurePosixPath(name).suffix.lower();is_video=extension in VIDEO_EXTENSIONS
                    if extension in COMPANION_EXTENSIONS:companion_paths.append('/'.join(relative))
                    if extension not in IMAGE_EXTENSIONS and not (include_videos and is_video):continue
                    collection=videos if is_video else images
                    if defer_metadata:
                        entry={'path':'/'.join(relative),'size':None,'_shell_item':child}
                        collection.append(entry);media.append(entry)
                        unknown_sizes+=1;update(parts,len(media)==1 or len(media)%25==0);continue
                    try:
                        size=phone_file_size(child,is_video,cancel)
                    except (TypeError,ValueError,OverflowError):size=None
                    known_bytes+=size or 0;unknown_sizes+=size is None
                    entry={'path':'/'.join(relative),'size':size}
                    if include_sources:
                        entry['source']=str(child.Path)
                        date_property='System.Media.DateEncoded' if is_video else 'System.Photo.DateTaken'
                        for key,getter in [('taken',lambda:child.ExtendedProperty(date_property)),('modified',lambda:child.ModifyDate)]:
                            try:
                                value=getter()
                                entry[key]=value.timestamp() if isinstance(value,datetime) else None
                            except Exception:entry[key]=None
                    collection.append(entry);media.append(entry);update(parts,len(media)==1 or len(media)%25==0)
                except InterruptedError:raise
                except Exception as error:errors.append({'path':'/'.join(parts),'error':str(error)})
            nested.sort(key=lambda entry:(entry[1][-1].casefold()!='camera',entry[1][-1].casefold()),reverse=True)
            stack.extend(nested)
        except InterruptedError:raise
        except Exception as error:errors.append({'path':'/'.join(parts),'error':str(error)})
        update(parts,True)
    if folders==0:raise RuntimeError('Phone storage is unavailable. Unlock the phone and allow photo access.')
    return {'test_only':True,'copied':0,'image_count':len(images),'video_count':len(videos),'file_count':len(media),'include_videos':include_videos,'known_bytes':known_bytes,'unknown_sizes':unknown_sizes,'folders':folders,'storage_available':(folders>1 or bool(media)) if apple else bool(selected) or folders>1,'folder_scope':'all-exposed-storage' if apple else 'selected-public-folders','source_folders':[] if apple else list(SOURCE_FOLDERS),'selected_folders':['/'.join(parts) or 'phone storage' for _,parts,_ in selected],'ignored_folders':ignored_folders,'companion_paths':companion_paths,'metadata_deferred':defer_metadata,'errors':errors,'images':images,'videos':videos,'media':media}

def preview_phone_download(device,progress=lambda text:None,cancel=None):
    if sys.platform!='win32':raise RuntimeError('Phone import preview currently requires Windows')
    import pythoncom
    from win32com.client.dynamic import Dispatch
    pythoncom.CoInitialize()
    try:
        shell=Dispatch('Shell.Application')
        item=find_phone_shell_item(shell,device)
        return inventory_tree(item,progress,cancel,device=device)
    finally:pythoncom.CoUninitialize()
