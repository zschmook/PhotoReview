"""Folder-scoped, time-bounded photo comparison; scanning never edits photos."""
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import numpy as np
from PIL import Image, ImageOps
from scipy.fft import dctn
from scipy.ndimage import gaussian_filter
from .scan_policy import is_thumbnail_directory
from .phone_import import VIDEO_EXTENSIONS
from .heic import HEIC_EXTENSIONS

EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}|HEIC_EXTENSIONS


def reject_links(path):
    for item in (Path(path), *Path(path).parents):
        info = item.lstat()
        if item.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Links / reparse points are excluded: ' + str(item))


def fingerprint(path):
    info = Path(path).stat(follow_symlinks=False)
    return (info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino)


@dataclass
class Photo:
    path: str
    identity: str
    size: int
    mtime: int
    device: int
    inode: int
    width: int
    height: int
    timestamp: float
    time_source: str
    phash: int
    gray: np.ndarray = field(repr=False)
    thumbnail: str = ''
    keep: bool = True
    media_kind: str = 'image'

    @property
    def is_video(self): return self.media_kind == 'video'

    @property
    def name(self): return Path(self.path).name
    @property
    def folder(self): return str(Path(self.path).parent)
    @property
    def signature(self): return (self.size, self.mtime, self.device, self.inode)


@dataclass
class Group:
    photos: list
    minimum_score: float
    skipped: bool = False

    @property
    def identity(self): return hashlib.sha256('|'.join(p.identity for p in self.photos).encode()).hexdigest()[:16]


def capture_time(image, info):
    exif = image.getexif()
    try:
        nested = exif.get_ifd(0x8769)
    except (ValueError, TypeError, AttributeError):
        nested = {}
    for tag, label in ((36867, 'EXIF date taken'), (36868, 'EXIF digitized')):
        text = nested.get(tag) or exif.get(tag)
        if text:
            try:
                dt = datetime.strptime(str(text).strip('\x00 '), '%Y:%m:%d %H:%M:%S')
                sub = nested.get(37521) or exif.get(37521)
                if tag == 36867 and sub and str(sub).isdigit():
                    dt = dt.replace(microsecond=int((str(sub)+'000000')[:6]))
                offset = nested.get(36881) or exif.get(36881)
                if offset:
                    try:
                        aware = datetime.fromisoformat(dt.isoformat()+str(offset))
                        return aware.timestamp(), label + ' + timezone'
                    except ValueError:
                        pass
                return dt.timestamp(), label + ' (local assumed)'
            except (ValueError, OverflowError, OSError):
                pass
    return info.st_mtime, 'File modified (fallback)'


def similarity(a, b):
    hash_score = 1 - (a.phash ^ b.phash).bit_count()/64
    # Loose hash gate. SSIM cannot lift a pair above the upper bound below.
    x, y = a.gray.astype(float)/255, b.gray.astype(float)/255
    ux, uy = gaussian_filter(x, 1.5), gaussian_filter(y, 1.5)
    vx = np.maximum(0, gaussian_filter(x*x, 1.5)-ux*ux)
    vy = np.maximum(0, gaussian_filter(y*y, 1.5)-uy*uy)
    covariance = gaussian_filter(x*y, 1.5)-ux*uy
    ssim = ((2*ux*uy+.01**2)*(2*covariance+.03**2))/((ux*ux+uy*uy+.01**2)*(vx+vy+.03**2))
    return round(100*(.7*hash_score+.3*max(0, float(ssim.mean()))), 2)


class HashIndex:
    """BK tree over group anchors; avoid all-pairs comparisons across a folder."""
    def __init__(self): self.root = None
    def add(self, value, index):
        if self.root is None:
            self.root = [value, [index], {}]; return
        node = self.root
        while True:
            distance = (value ^ node[0]).bit_count()
            if distance == 0: node[1].append(index); return
            if distance not in node[2]: node[2][distance] = [value, [index], {}]; return
            node = node[2][distance]
    def find(self, value, radius):
        stack = [self.root] if self.root else []
        while stack:
            node = stack.pop(); distance = (value ^ node[0]).bit_count()
            if distance <= radius: yield from node[1]
            stack.extend(child for edge, child in node[2].items() if distance-radius <= edge <= distance+radius)


def make_groups(photos, minutes=10, threshold=90, cancel=None, backend="cpu", progress=None):
    by_folder = defaultdict(list)
    for photo in photos:
        if not photo.is_video: by_folder[photo.folder].append(photo)
    result = []
    radius = min(64, max(0, math.floor(64*(100-threshold)/70)))
    for folder, items in sorted(by_folder.items()):
        groups = []; index = HashIndex()
        gpu = None;gpu_allowed=backend in ('auto','gpu')
        try:
            for photo in sorted(items, key=lambda p:(p.timestamp, p.name)):
                if cancel and cancel.is_set(): raise InterruptedError('Scan cancelled')
                added = False
                candidates=[group_id for group_id in index.find(photo.phash,radius)
                            if len(groups[group_id].photos)<64 and photo.timestamp-groups[group_id].photos[0].timestamp<=minutes*60]
                if gpu_allowed and gpu is None and len(candidates)>=32:
                    try:
                        from .gpu import GPUScorer
                        gpu=GPUScorer(items)
                        if progress:progress(f"GPU matching · {gpu.info['backend_type']} · {Path(folder).name}")
                    except Exception as error:
                        gpu_allowed=False
                        if progress:progress(f'CPU fallback · GPU unavailable: {error}')
                if gpu and len(candidates)>=32:
                    try:
                        pairs=[(photo,groups[group_id].photos[0]) for group_id in candidates]
                        scores=gpu.score_pairs(pairs,cancel)
                        # Guard threshold rounding differences with the reference CPU score.
                        candidates=[gid for gid,score,pair in zip(candidates,scores,pairs)
                                    if score>=threshold or (abs(score-threshold)<=.02 and similarity(*pair)>=threshold)]
                    except InterruptedError:
                        raise
                    except Exception as error:
                        gpu.close();gpu=None;gpu_allowed=False
                        if progress:progress(f'CPU fallback · GPU comparison failed: {error}')
                for group_id in candidates:
                    group = groups[group_id]
                    if len(group.photos) >= 64 or photo.timestamp-group.photos[0].timestamp > minutes*60:
                        continue
                    scores = []
                    for other in group.photos:
                        if 100-70*(photo.phash ^ other.phash).bit_count()/64 < threshold:
                            break
                        score = similarity(photo, other)
                        if score < threshold: break
                        scores.append(score)
                    if len(scores) == len(group.photos):
                        group.photos.append(photo); group.minimum_score=min(group.minimum_score, *scores)
                        added = True; break
                if not added:
                    index.add(photo.phash, len(groups)); groups.append(Group([photo],100))
        finally:
            if gpu:gpu.close()
        result.extend(group for group in groups if len(group.photos)>1)
    return result


def _analyze_photo(path, info, identity, thumbnails, time_range, cancel):
    if cancel and cancel.is_set():raise InterruptedError('Scan cancelled')
    signature=(info.st_size,info.st_mtime_ns,info.st_dev,info.st_ino)
    reject_links(path)
    if path.suffix.lower() in VIDEO_EXTENSIONS:
        if time_range and not time_range[0]<=info.st_mtime<time_range[1]:return None
        if fingerprint(path)!=signature:raise ValueError('Video changed during scanning')
        return Photo(str(path),identity,*signature,0,0,info.st_mtime,'File modified (fallback)',0,None,media_kind='video')
    with Image.open(path) as original:
        timestamp,time_source=capture_time(original,info)
        if time_range and not time_range[0]<=timestamp<time_range[1]:return None
        image=ImageOps.exif_transpose(original).convert('RGB')
        width,height=image.size
        gray=np.asarray(ImageOps.fit(image.convert('L'),(96,96)),dtype=np.uint8)
        small=np.asarray(image.convert('L').resize((32,32)),dtype=float)
        low=dctn(small,norm='ortho')[:8,:8].flatten();bits=low>np.median(low[1:]);bits[0]=False
        phash=sum(int(bit)<<i for i,bit in enumerate(bits))
        thumb=thumbnails/(identity+'.jpg');image.thumbnail((1100,800));image.save(thumb,quality=88)
    if fingerprint(path)!=signature:raise ValueError('Photo changed during scanning')
    return Photo(str(path),identity,*signature,width,height,timestamp,time_source,phash,gray,str(thumb))


def scan(root, data_dir, progress=lambda message:None, cancel=None, selected_paths=None, time_range=None, count_progress=None, include_videos=False):
    root=Path(root).absolute();reject_links(root)
    selected_paths={str(Path(path).absolute()) for path in selected_paths} if selected_paths is not None else None
    data_dir=Path(data_dir);data_dir.mkdir(parents=True,exist_ok=True)
    thumbnails=data_dir/'thumbnails';thumbnails.mkdir(exist_ok=True)
    db=sqlite3.connect(data_dir/'cache.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS photos(identity TEXT PRIMARY KEY, payload TEXT, gray BLOB)')
    photos=[];errors=[];inventory=[];stack=[] if is_thumbnail_directory(root.name) else [root]
    extensions=EXTENSIONS|VIDEO_EXTENSIONS if include_videos else EXTENSIONS
    noun='files' if include_videos else 'photos'
    try:
        while stack:
            if cancel and cancel.is_set():raise InterruptedError('Scan cancelled')
            folder=stack.pop()
            try:
                reject_links(folder)
                with os.scandir(folder) as entries:
                    for entry in entries:
                        path=Path(entry.path)
                        try:
                            info=entry.stat(follow_symlinks=False)
                            if entry.is_symlink() or getattr(info,'st_file_attributes',0)&0x400:
                                errors.append((str(path),'Link / reparse point skipped'));continue
                            if entry.is_dir(follow_symlinks=False):
                                if not is_thumbnail_directory(entry.name):stack.append(path)
                                continue
                            if path.suffix.lower() not in extensions:continue
                            if selected_paths is not None and str(path.absolute()) not in selected_paths:continue
                            info=path.stat(follow_symlinks=False)
                            identity=hashlib.sha256((str(path)+repr((info.st_size,info.st_mtime_ns,info.st_dev,info.st_ino))).encode()).hexdigest()
                            inventory.append((path,info,identity))
                        except (OSError,ValueError) as error:errors.append((str(path),str(error)))
            except (OSError,ValueError) as error:errors.append((str(folder),str(error)))
        total=len(inventory);completed=0;pending={};position=0
        if count_progress:count_progress(0,total)
        workers=min(4,max(1,(os.cpu_count() or 2)//2))
        progress(f'Indexed {total:,} {noun} · building previews with {workers} workers')
        with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='photo-decode') as pool:
            while position<total or pending:
                if cancel and cancel.is_set():
                    for future in pending:future.cancel()
                    raise InterruptedError('Scan cancelled')
                while position<total and len(pending)<workers*2:
                    path,info,identity=inventory[position];position+=1
                    row=db.execute('SELECT payload,gray FROM photos WHERE identity=?',(identity,)).fetchone()
                    if row:
                        payload=json.loads(row[0]);payload['gray']=np.frombuffer(row[1],dtype=np.uint8).reshape(96,96).copy() if row[1] is not None else None
                        photo=Photo(**payload)
                        if photo.is_video or Path(photo.thumbnail).is_file():
                            if not time_range or time_range[0]<=photo.timestamp<time_range[1]:photos.append(photo)
                            completed+=1;progress(f'Reading {completed:,} / {total:,} {noun} · {path.name} (cached)')
                            if count_progress:count_progress(completed,total)
                            continue
                    future=pool.submit(_analyze_photo,path,info,identity,thumbnails,time_range,cancel)
                    pending[future]=path
                if not pending:continue
                finished,_=wait(pending,return_when=FIRST_COMPLETED)
                for future in finished:
                    path=pending.pop(future)
                    try:
                        photo=future.result()
                        if photo:
                            payload={k:v for k,v in vars(photo).items() if k!='gray'}
                            db.execute('INSERT OR REPLACE INTO photos VALUES(?,?,?)',(photo.identity,json.dumps(payload),photo.gray.tobytes() if photo.gray is not None else None))
                            photos.append(photo)
                    except (OSError,ValueError,Image.DecompressionBombError) as error:errors.append((str(path),str(error)))
                    completed+=1;progress(f'Reading {completed:,} / {total:,} {noun} · {path.name}')
                    if count_progress:count_progress(completed,total)
                    if completed%100==0:db.commit()
        db.commit()
    finally:db.close()
    return sorted(photos,key=lambda photo:photo.path),errors


def save_draft(path, root, photos, groups, minutes, threshold, group_index=0, selected_paths=None, time_range=None, folder_filter=None, mode='compare', photo_index=0):
    payload=dict(version=1,root=str(root),folder_filter=folder_filter,selected_paths=selected_paths,time_range=time_range,minutes=minutes,threshold=threshold,group_index=group_index,
                 decisions={p.identity:p.keep for p in photos}, skipped=[g.identity for g in groups if g.skipped])
    payload.update(mode=mode,photo_index=photo_index)
    path=Path(path);path.parent.mkdir(exist_ok=True,parents=True);temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(payload,indent=2),encoding='utf-8');os.replace(temp,path)


def restore_draft(path, photos, groups):
    payload=json.loads(Path(path).read_text(encoding='utf-8'))
    decisions=payload.get('decisions',{})
    for photo in photos:photo.keep=bool(decisions.get(photo.identity,True))
    for group in groups:group.skipped=group.identity in payload.get('skipped',[])
    return payload


def validate_plan(photos, groups, progress=None):
    selected=[p for p in photos if not p.keep]
    # Validate kept files too: do not recycle a copy if its selected keeper vanished.
    if progress:progress(0,len(photos))
    for position,photo in enumerate(photos,1):
        reject_links(photo.path)
        if fingerprint(photo.path)!=photo.signature:raise ValueError('File changed or replaced; rescan: '+photo.path)
        if progress:progress(position,len(photos))
    return selected
