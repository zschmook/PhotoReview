"""Content index for existing PC media; history never substitutes for phone reads."""
import json
import os
import hashlib
import math
from pathlib import Path
from .core import reject_links
from .scan_policy import is_thumbnail_directory


def signature(path):
    info=Path(path).stat(follow_symlinks=False)
    return json.dumps([info.st_size,info.st_mtime_ns,info.st_ctime_ns,info.st_dev,info.st_ino])


class ImportHistory:
    def __init__(self,db):
        self.db=db
        db.execute('CREATE TABLE IF NOT EXISTS imports (identity TEXT PRIMARY KEY,path TEXT,size INTEGER,sha256 TEXT,date_source TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS saved_copy_checks (path TEXT PRIMARY KEY,signature TEXT,sha256 TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS phone_source_checks (identity TEXT PRIMARY KEY,sha256 TEXT)')
        db.commit()

    def remember(self,key,path,size,sha,date_source):
        self.db.execute('INSERT OR REPLACE INTO imports VALUES(?,?,?,?,?)',(key,str(path),size,sha,date_source))

    def source_key(self,device,entry):
        # Reuse only complete MTP metadata, scoped to this phone AND object.
        # Missing dates/sizes or changed identifiers take the fresh-read path.
        source=entry.get('source');size=entry.get('size');modified=entry.get('modified')
        if not isinstance(source,(list,tuple)) or not source or not all(isinstance(p,bytes) for p in source):return None
        if not isinstance(size,int) or size<=0 or not isinstance(modified,(int,float)) or not math.isfinite(modified) or modified<=0:return None
        value=[device.identity.casefold(),entry['path'].casefold(),[part.hex() for part in source],size,modified]
        return hashlib.sha256(json.dumps(value,ensure_ascii=False).encode()).hexdigest()

    def source_checksum(self,key):
        if key is None:return None
        row=self.db.execute('SELECT sha256 FROM phone_source_checks WHERE identity=?',(key,)).fetchone()
        return row[0] if row else None

    def remember_source(self,key,sha):
        if key is not None:self.db.execute('INSERT OR REPLACE INTO phone_source_checks VALUES(?,?)',(key,sha))


class ContentIndex:
    def __init__(self,db,root,hasher,cancel=None):
        self.db=db;self.root=Path(root);self.hasher=hasher;self.cancel=cancel;self.matches={}

    def check_cancel(self):
        if self.cancel and self.cancel.is_set():raise InterruptedError('Phone import cancelled')

    def checksum(self,path):
        self.check_cancel();reject_links(path);before=signature(path)
        cached=self.db.execute('SELECT signature,sha256 FROM saved_copy_checks WHERE path=?',(str(path),)).fetchone()
        sha=cached[1] if cached and cached[0]==before else self.hasher(path)
        self.check_cancel()
        if signature(path)!=before:raise OSError('PC file changed during checksum comparison: '+str(path))
        self.db.execute('INSERT OR REPLACE INTO saved_copy_checks VALUES(?,?,?)',(str(path),before,sha))
        return dict(path=path,size=json.loads(before)[0],sha256=sha,signature=before)

    def add(self,record):self.matches.setdefault((record['size'],record['sha256']),[]).append(record)

    def scan(self,extensions,sizes=None,progress=lambda count,path:None):
        stack=[self.root];count=0
        while stack:
            self.check_cancel();folder=stack.pop();reject_links(folder)
            with os.scandir(folder) as entries:items=sorted(entries,key=lambda item:item.name.casefold())
            for item in items:
                self.check_cancel();info=item.stat(follow_symlinks=False)
                if item.is_symlink() or getattr(info,'st_file_attributes',0)&0x400:continue
                if item.is_dir(follow_symlinks=False):
                    if not is_thumbnail_directory(item.name) and not item.name.startswith('.phone-import-'):stack.append(Path(item.path))
                elif item.is_file(follow_symlinks=False) and Path(item.name).suffix.lower() in extensions and info.st_size>0:
                    if sizes is not None and info.st_size not in sizes:continue
                    progress(count,Path(item.path));self.add(self.checksum(Path(item.path)));count+=1
                    if count%32==0:self.db.commit()
        self.db.commit();progress(count,None);return count

    def find(self,size,sha):
        for record in self.matches.get((size,sha),[]):
            self.check_cancel();path=record['path']
            try:
                reject_links(path)
                if signature(path)!=record['signature']:record.update(self.checksum(path))
                if record['size']==size and record['sha256']==sha:return record
            except (OSError,ValueError):continue
        return None
