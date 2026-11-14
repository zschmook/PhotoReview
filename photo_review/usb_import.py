"""Removable-drive detection and copy-only, checksum-based USB imports."""
from dataclasses import dataclass
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import os
from pathlib import Path
import sys
from time import monotonic
from .core import reject_links
from .phones import PhoneWatcher
from .phone_import import IMAGE_EXTENSIONS,VIDEO_EXTENSIONS
from .phone_download import import_entries
from .import_history import signature
from .scan_policy import is_thumbnail_directory


@dataclass(frozen=True)
class USBDrive:
    identity:str
    name:str
    root:str


def windows_drives(api=None):
    if api is None:
        if sys.platform!='win32':raise RuntimeError('USB detection requires Windows')
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.GetDriveTypeW.argtypes=[wintypes.LPCWSTR];api.GetDriveTypeW.restype=wintypes.UINT
        api.GetLogicalDrives.restype=wintypes.DWORD
        api.GetVolumeInformationW.argtypes=[wintypes.LPCWSTR,wintypes.LPWSTR,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),ctypes.POINTER(wintypes.DWORD),ctypes.POINTER(wintypes.DWORD),wintypes.LPWSTR,wintypes.DWORD]
        api.GetVolumeInformationW.restype=wintypes.BOOL
        api.GetVolumeNameForVolumeMountPointW.argtypes=[wintypes.LPCWSTR,wintypes.LPWSTR,wintypes.DWORD]
        api.GetVolumeNameForVolumeMountPointW.restype=wintypes.BOOL
        api.SetThreadErrorMode.argtypes=[wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)];api.SetThreadErrorMode.restype=wintypes.BOOL
    return api


def read_usb_drive(root,api=None):
    """Read volume metadata only; fixed/network disks and empty readers are excluded."""
    api=windows_drives(api)
    if api.GetDriveTypeW(root)!=2:return None # DRIVE_REMOVABLE
    old_mode=wintypes.DWORD();changed=hasattr(api,'SetThreadErrorMode') and api.SetThreadErrorMode(0x8001,ctypes.byref(old_mode))
    try:
        label=ctypes.create_unicode_buffer(261);serial=wintypes.DWORD();maximum=wintypes.DWORD();flags=wintypes.DWORD()
        if not api.GetVolumeInformationW(root,label,len(label),ctypes.byref(serial),ctypes.byref(maximum),ctypes.byref(flags),None,0):return None
        guid=ctypes.create_unicode_buffer(64)
        if not api.GetVolumeNameForVolumeMountPointW(root,guid,len(guid)):return None
        identity=f'{guid.value.casefold()}:{serial.value:08x}'
        return USBDrive(identity,(label.value or 'USB drive')+' ('+root[:2]+')',root)
    finally:
        if changed:api.SetThreadErrorMode(old_mode.value,None)


def enumerate_usb_drives(api=None):
    api=windows_drives(api);mask=api.GetLogicalDrives()
    if not mask:raise OSError('Windows could not list drive letters')
    result=[]
    for index in range(26):
        if mask&(1<<index):
            drive=read_usb_drive(chr(65+index)+':\\',api)
            if drive is not None:result.append(drive)
    return result


class USBWatcher(PhoneWatcher):
    def __init__(self,parent=None,reader=None,interval=2):
        super().__init__(parent,reader or enumerate_usb_drives,interval)


def validate_usb_drive(drive):
    current=read_usb_drive(drive.root)
    if current is None or current.identity!=drive.identity:raise OSError('USB drive was removed or replaced. Reconnect it and try again.')


def check_cancel(cancel):
    if cancel and cancel.is_set():raise InterruptedError('USB import cancelled')


def inventory_usb(drive,include_videos=False,progress=lambda text:None,cancel=None,validator=validate_usb_drive):
    """Search the whole USB folder tree without the phone folder allowlist."""
    root=Path(drive.root).absolute();validator(drive);reject_links(root)
    entries=[];errors=[];stack=[root];last=monotonic();reported_first=False
    allowed=IMAGE_EXTENSIONS|(VIDEO_EXTENSIONS if include_videos else set())
    while stack:
        check_cancel(cancel);validator(drive);folder=stack.pop()
        progress(f'Finding USB pictures · {len(entries):,} files found · {folder}')
        try:
            reject_links(folder)
            with os.scandir(folder) as iterator:
                for item in iterator:
                    check_cancel(cancel)
                    try:
                        info=item.stat(follow_symlinks=False)
                        if item.is_symlink() or getattr(info,'st_file_attributes',0)&0x400:continue
                        if item.is_dir(follow_symlinks=False):
                            if not is_thumbnail_directory(item.name) and item.name.casefold() not in ('$recycle.bin','system volume information','recycler','recycled'):stack.append(Path(item.path))
                        elif item.is_file(follow_symlinks=False) and Path(item.name).suffix.lower() in allowed:
                            entries.append(dict(path=Path(item.path).relative_to(root).as_posix(),source=str(Path(item.path)),size=info.st_size,modified=info.st_mtime,taken=None))
                    except OSError as error:errors.append(dict(path=str(item.path),error=str(error)))
                    if (entries and not reported_first) or monotonic()-last>=.2:
                        progress(f'Finding USB pictures · {len(entries):,} files found · {folder}');last=monotonic();reported_first=bool(entries)
        except (OSError,ValueError) as error:
            if isinstance(error,InterruptedError):raise
            errors.append(dict(path=str(folder),error=str(error)))
    progress(f'Finding USB pictures · {len(entries):,} files found')
    return entries,errors


@contextmanager
def usb_source(source,drive,cancel,validator):
    check_cancel(cancel);validator(drive)
    root=Path(drive.root).absolute();path=Path(source)
    if not path.is_absolute() or '..' in path.parts or not path.is_relative_to(root):raise ValueError('USB source is outside the selected drive')
    reject_links(path);before=signature(path)
    with path.open('rb') as stream:yield path,stream
    check_cancel(cancel);validator(drive)
    if signature(path)!=before:raise OSError('USB source changed while it was read')


def import_usb(drive,destination,database,include_videos=False,progress=lambda text:None,counts=lambda x,y:None,cancel=None,validator=validate_usb_drive):
    root=Path(drive.root).absolute();target=Path(destination).absolute()
    # Validate before creating a destination/database or opening source files.
    reject_links(root)
    if target.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(target.resolve()):raise ValueError('Choose a pictures folder outside the USB drive before importing')
    try:entries,errors=inventory_usb(drive,include_videos,progress,cancel,validator)
    except InterruptedError:entries=[];errors=[]
    def reader(source,cancel):
        with usb_source(source,drive,cancel,validator) as (path,stream):
            import hashlib
            sha=hashlib.sha256();size=0;last=monotonic()
            while True:
                check_cancel(cancel);block=stream.read(1024*1024)
                if not block:break
                sha.update(block);size+=len(block)
                if monotonic()-last>=.2:progress(f'Reading USB checksum: {path.name} · {size/1024**2:,.1f} MiB read');last=monotonic()
            return size,sha.hexdigest()
    def copier(source,destination,cancel):
        with usb_source(source,drive,cancel,validator) as (path,stream),Path(destination).open('xb') as output:
            size=0;last=monotonic()
            while True:
                check_cancel(cancel);block=stream.read(1024*1024)
                if not block:break
                output.write(block);size+=len(block)
                if monotonic()-last>=.2:progress(f'Copying USB file: {path.name} · {size/1024**2:,.1f} MiB copied');last=monotonic()
    report=import_entries(entries,drive,target,database,'flat',progress,counts,cancel,copier,reader=reader,source_kind='usb')
    report.update(device=drive.name,device_id=drive.identity,source_root=str(root),include_videos=include_videos)
    report['errors']=errors+report['errors']
    return report
