"""Keep bundled assets separate from persistent per-user application data."""
from pathlib import Path
from contextlib import closing
import json
import hashlib
import uuid
from datetime import datetime
import os
import shutil
import sqlite3
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
COMPILED='__compiled__' in globals()


def data_directory(compiled=COMPILED,environment=None):
    if not compiled:return ROOT/'data'
    environment=os.environ if environment is None else environment
    local=environment.get('LOCALAPPDATA') or Path.home()/'AppData'/'Local'
    return Path(local)/'PhotoReview'/'data'


DATA=data_directory()
INITIALIZED='.profile-initialized.json'


def _mark_initialized(destination):
    temporary=destination/(INITIALIZED+'.tmp')
    temporary.write_text(json.dumps({'schema':1}),encoding='utf-8');temporary.replace(destination/INITIALIZED)


class ProfileGuard:
    def __init__(self,kernel,handle):self.kernel=kernel;self.handle=handle
    def close(self):
        if self.handle is not None:
            self.kernel.ReleaseMutex(self.handle);self.kernel.CloseHandle(self.handle);self.handle=None


def acquire_profile_guard(directory=DATA):
    """Prevent another normal app/reset from changing the same live profile."""
    if sys.platform!='win32':raise RuntimeError('Photo Review currently requires Windows')
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR];kernel.CreateMutexW.restype=wintypes.HANDLE
    kernel.ReleaseMutex.argtypes=[wintypes.HANDLE];kernel.ReleaseMutex.restype=wintypes.BOOL
    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
    key=hashlib.sha256(os.path.normcase(str(Path(directory).resolve())).encode()).hexdigest()
    ctypes.set_last_error(0);handle=kernel.CreateMutexW(None,True,'Local\\PhotoReview.Profile.'+key)
    error=ctypes.get_last_error()
    if not handle:raise ctypes.WinError(error)
    if error==183:
        kernel.CloseHandle(handle)
        raise RuntimeError('Photo Review is already using this profile. Close it before starting another instance or using -reset.')
    return ProfileGuard(kernel,handle)


def reset_user_data(directory=DATA):
    """Archive app state only; keep bundled runtime and original photos intact."""
    directory=Path(directory).absolute();parent=directory.parent.resolve();backup=None
    if directory.is_symlink() or directory.is_junction():raise RuntimeError('Refusing to reset a linked profile directory')
    if directory.resolve().parent!=parent:raise RuntimeError('Profile directory is outside its expected parent')
    if directory.exists():
        if not directory.is_dir():raise RuntimeError('Profile path is not a directory')
        backup_root=parent/(directory.name+'-reset-backups')
        if backup_root.is_symlink() or backup_root.is_junction():raise RuntimeError('Refusing to use a linked reset backup directory')
        backup=backup_root/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
        # Check resolved source and destination before the Windows directory move.
        if not backup.resolve().is_relative_to(parent):raise RuntimeError('Reset backup is outside the expected parent')
        backup_root.mkdir(parents=True,exist_ok=True);directory.rename(backup)
    directory.mkdir(parents=True,exist_ok=True)
    _mark_initialized(directory) # A deliberate reset must not re-import old settings.
    return backup


def prepare_user_data(compiled=COMPILED,destination=None,executable_dir=None):
    """Copy an existing standalone profile once; leave all original data intact."""
    if not compiled:return None
    destination=Path(destination) if destination is not None else DATA
    if (destination/INITIALIZED).is_file():return None
    destination.mkdir(parents=True,exist_ok=True)
    # SQLite releases this startup lock even if a process crashes mid-migration.
    with closing(sqlite3.connect(destination/'.profile-lock.sqlite3',timeout=30)) as lock:
        lock.execute('BEGIN EXCLUSIVE')
        if (destination/INITIALIZED).is_file():return None
        source=_migrate_profile(destination,executable_dir)
        _mark_initialized(destination)
        return source


def _migrate_profile(destination,executable_dir):
    if any((destination/name).exists() for name in ('preferences.json','review-draft.json','cache.sqlite3','REVIEW_ONLY','migration-source.json')):return None
    executable_dir=Path(executable_dir) if executable_dir is not None else Path(sys.argv[0]).resolve().parent
    candidates=[executable_dir/'data',executable_dir/'main.dist'/'data',
                executable_dir.parent/'.build'/'legacy-standalone'/'main.dist'/'data']
    if destination.name=='data':candidates.insert(0,destination.parent)
    source=next((path for path in candidates if (path/'preferences.json').is_file() or (path/'review-draft.json').is_file()),None)
    if source is None:
        destination.mkdir(parents=True,exist_ok=True);return None
    from .core import reject_links
    reject_links(source)
    destination.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='migration-',dir=destination) as temporary:
        stage=Path(temporary)
        for path in [*source.glob('*.json'),*source.glob('*.jsonl'),source/'REVIEW_ONLY']:
            if not path.is_file():continue
            reject_links(path)
            if path.suffix=='.json':json.loads(path.read_text(encoding='utf-8'))
            shutil.copy2(path,stage/path.name)
        copied=set();thumbs=source/'thumbnails'
        if thumbs.exists():
            reject_links(thumbs);(stage/'thumbnails').mkdir()
            for path in thumbs.glob('*.jpg'):
                if not path.is_file():continue
                reject_links(path);shutil.copy2(path,stage/'thumbnails'/path.name);copied.add(path.name)
        database=source/'cache.sqlite3'
        if database.exists():
            reject_links(database)
            with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as old,closing(sqlite3.connect(stage/'cache.sqlite3')) as new:
                old.backup(new)
                if new.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='photos'").fetchone():
                    for identity,payload in new.execute('SELECT identity,payload FROM photos').fetchall():
                        values=json.loads(payload);name=Path(values.get('thumbnail','')).name
                        if name in copied:
                            values['thumbnail']=str(destination/'thumbnails'/name)
                            new.execute('UPDATE photos SET payload=? WHERE identity=?',(json.dumps(values),identity))
                new.commit()
        (stage/'migration-source.json').write_text(json.dumps({'source':str(source)}),encoding='utf-8')
        for path in stage.iterdir():
            target=destination/path.name
            if target.exists():raise RuntimeError('Application data changed during migration; close other Photo Review windows and retry.')
            path.replace(target)
    return source
