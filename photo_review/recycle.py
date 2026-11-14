"""Explicit Windows recycling with no permanent-delete fallback."""
import json
import os
from pathlib import Path
from .core import fingerprint, reject_links, validate_plan

from .paths import DATA

REVIEW_LOCK = DATA/'REVIEW_ONLY'


def recycle_one(path):
    if os.name!='nt': raise RuntimeError('Recycling is Windows-only.')
    import pythoncom
    import win32file
    from win32com.shell import shell,shellcon
    from win32com.server.exception import COMException
    from send2trash.win.IFileOperationProgressSink import FileOperationProgressSink
    volume=win32file.GetVolumePathName(str(Path(path).absolute()))
    if win32file.GetDriveType(volume)!=3:
        raise ValueError('This build only recycles files on local fixed drives. Network/removable sources are review-only.')
    class Sink(FileOperationProgressSink):
        def PreDeleteItem(self, flags, item):
            if not flags & shellcon.TSF_DELETE_RECYCLE_IF_POSSIBLE:
                raise COMException(desc='Permanent deletion refused',scode=-2147467260)
            return 0
        def PostDeleteItem(self, flags, item, result, newly_created):
            if result < 0 or not newly_created:
                raise COMException(desc='Windows did not confirm a Recycle Bin destination',scode=-2147467259)
            self.newItem=newly_created.GetDisplayName(shellcon.SIGDN_FILESYSPATH)
    pythoncom.CoInitialize()
    try:
        sink=Sink(); wrapped=pythoncom.WrapObject(sink,shell.IID_IFileOperationProgressSink)
        operation=pythoncom.CoCreateInstance(shell.CLSID_FileOperation,None,pythoncom.CLSCTX_ALL,shell.IID_IFileOperation)
        operation.SetOperationFlags(shellcon.FOF_NOERRORUI|shellcon.FOF_SILENT|shellcon.FOF_NOCONFIRMATION|shellcon.FOFX_EARLYFAILURE|0x20000000|0x80000)
        operation.DeleteItem(shell.SHCreateItemFromParsingName(str(path),None,shell.IID_IShellItem),wrapped)
        result=operation.PerformOperations()
        if result or operation.GetAnyOperationsAborted() or not sink.newItem:
            raise RuntimeError('Recycling was aborted or could not be verified.')
        return sink.newItem
    finally:pythoncom.CoUninitialize()


def apply_plan(photos,groups,journal,recycler=recycle_one,progress=None,validation_progress=None):
    # Temporary user-requested review-only lock, including already-open windows.
    if REVIEW_LOCK.exists():
        raise RuntimeError('Review-only mode: no files will be recycled or deleted.')
    selected=validate_plan(photos,groups,progress=validation_progress)
    if progress:progress(0,len(selected),None)
    result=[];journal=Path(journal);journal.parent.mkdir(exist_ok=True,parents=True)
    with journal.open('a',encoding='utf-8') as log:
        for photo in selected:
            reject_links(photo.path)
            if fingerprint(photo.path)!=photo.signature:raise ValueError('File changed before recycle: '+photo.path)
            record=dict(path=photo.path,identity=photo.identity,status='planned')
            log.write(json.dumps(record)+'\n');log.flush();os.fsync(log.fileno())
            try:
                recycled_path=recycler(photo.path)
                if Path(photo.path).exists():raise RuntimeError('Source still exists after recycle operation')
                record.update(status='recycled',recycle_path=recycled_path)
            except Exception as error:
                record.update(status='failed',error=str(error));log.write(json.dumps(record)+'\n');log.flush()
                raise
            log.write(json.dumps(record)+'\n');log.flush();result.append(record)
            if progress:progress(len(result),len(selected),photo)
    return result
