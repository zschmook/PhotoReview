"""HEIC decoding and non-destructive JPEG copies; originals remain untouched."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from PIL import Image,ImageOps
from pillow_heif import register_heif_opener
from .scan_policy import is_thumbnail_directory

register_heif_opener(thumbnails=False)
HEIC_EXTENSIONS={'.heic','.heif','.heics','.heifs','.hif'}


def signature(path):
    from .core import reject_links,fingerprint
    reject_links(path)
    return fingerprint(path)


def jpeg_copy(source,database,cancel=None):
    """Return (path, created). Reuse only a recorded, unchanged conversion."""
    source=Path(source).absolute()
    if source.suffix.lower() not in HEIC_EXTENSIONS:raise ValueError('Expected a HEIC/HEIF photo')
    def check():
        if cancel and cancel.is_set():raise InterruptedError('JPEG conversion cancelled')
    check();before=signature(source);database=Path(database);database.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(database)) as db:
        db.execute('CREATE TABLE IF NOT EXISTS conversions(source TEXT PRIMARY KEY, signature TEXT, target TEXT, target_signature TEXT)')
        row=db.execute('SELECT signature,target,target_signature FROM conversions WHERE source=?',(str(source),)).fetchone()
        if row and json.loads(row[0])==list(before):
            try:
                if list(signature(row[1]))==json.loads(row[2]):return Path(row[1]),False
            except (OSError,ValueError):pass
        check()
        with Image.open(source) as original:
            upright=ImageOps.exif_transpose(original)
            image=upright.convert('RGB')
            if upright.mode=='RGBA':
                image=Image.new('RGB',upright.size,'white');image.paste(upright,mask=upright.getchannel('A'))
            exif=upright.getexif();exif[274]=1
            options=dict(quality=95,subsampling=0,exif=exif.tobytes())
            if upright.info.get('icc_profile'):options['icc_profile']=upright.info['icc_profile']
            check()
            descriptor,name=tempfile.mkstemp(prefix='.photo-review-jpeg-',suffix='.tmp',dir=source.parent)
            os.close(descriptor);temporary=Path(name)
            try:
                image.save(temporary,format='JPEG',**options)
                with Image.open(temporary) as result:result.verify()
                check()
                if signature(source)!=before:raise ValueError('HEIC changed during conversion; original retained')
                index=0
                while True:
                    suffix='' if index==0 else '-'+str(index)
                    target=source.with_name(source.name+suffix+'.jpg')
                    if os.path.lexists(target):index+=1;continue
                    # Windows rename publishes atomically and never replaces an existing file.
                    try:temporary.rename(target);break
                    except FileExistsError:index+=1
                info=source.stat();os.utime(target,ns=(info.st_atime_ns,info.st_mtime_ns))
                db.execute('INSERT OR REPLACE INTO conversions VALUES(?,?,?,?)',(str(source),json.dumps(before),str(target),json.dumps(signature(target))));db.commit()
                return target,True
            finally:temporary.unlink(missing_ok=True)


def convert_folder(root,database,progress=lambda text:None,counts=lambda x,y:None,cancel=None):
    """Convert an existing local album recursively; skip links and thumbnails."""
    from .core import reject_links
    root=Path(root).absolute();reject_links(root);stack=[] if is_thumbnail_directory(root.name) else [root];files=[]
    report=dict(converted=0,existing=0,cancelled=False,errors=[])
    def check():
        if cancel and cancel.is_set():raise InterruptedError('JPEG conversion cancelled')
    try:
        while stack:
            check();folder=stack.pop();progress('Finding HEIC photos: '+str(folder))
            try:
                reject_links(folder)
                with os.scandir(folder) as entries:
                    for entry in entries:
                        check();info=entry.stat(follow_symlinks=False)
                        if entry.is_symlink() or getattr(info,'st_file_attributes',0)&0x400:continue
                        if entry.is_dir(follow_symlinks=False):
                            if not is_thumbnail_directory(entry.name):stack.append(Path(entry.path))
                        elif Path(entry.name).suffix.lower() in HEIC_EXTENSIONS:files.append(Path(entry.path))
            except (OSError,ValueError) as error:report['errors'].append((str(folder),str(error)))
        counts(0,len(files))
        for number,path in enumerate(files,1):
            check();progress('Converting HEIC to JPEG: '+path.name)
            try:
                _,created=jpeg_copy(path,database,cancel);report['converted' if created else 'existing']+=1
            except (OSError,ValueError) as error:report['errors'].append((str(path),str(error)))
            counts(number,len(files))
    except InterruptedError:report['cancelled']=True
    return report
