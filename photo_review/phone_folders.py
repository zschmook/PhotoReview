"""Remember phone destinations by device identity and selected library."""
import hashlib
import os
from pathlib import Path
from .phone_download import default_destination,destination_inside_root


def _path_key(path):return os.path.normcase(str(Path(path).resolve()))


def _records(settings):
    if not isinstance(settings,dict):return []
    return [(key,value) for key,value in settings.items() if isinstance(key,str) and isinstance(value,dict)]


def _record(settings,device):
    return next((value for key,value in _records(settings) if key.casefold()==device.identity.casefold()),None)


def _saved_folder(record,working_root):
    if not record or not isinstance(record.get('folders'),dict):return None
    relative=record['folders'].get(_path_key(working_root))
    if not isinstance(relative,str) or not relative:return None
    path=Path(relative)
    if path.is_absolute() or path.drive or '..' in path.parts:return None
    try:return destination_inside_root(Path(working_root)/path,working_root)
    except (ValueError,OSError,RuntimeError):return None


def validate_phone_destination(device,working_root,destination,settings):
    target=destination_inside_root(destination,working_root)
    for key,record in _records(settings):
        if key.casefold()==device.identity.casefold():continue
        other=_saved_folder(record,working_root)
        if other is not None and _path_key(other)==_path_key(target):
            raise ValueError('This folder is already assigned to another phone. Choose a different folder for this phone.')
    return target


def phone_destination(device,working_root,settings):
    """Use the model name; add an ID only for another phone of that model."""
    record=_record(settings,device);saved=_saved_folder(record,working_root)
    if saved is not None:
        try:return validate_phone_destination(device,working_root,saved,settings)
        except ValueError:pass
    default=default_destination(device,working_root)
    same_model=any(key.casefold()!=device.identity.casefold()
                   and isinstance(other.get('name'),str)
                   and other['name'].casefold()==device.name.casefold()
                   and _saved_folder(other,working_root) is not None
                   for key,other in _records(settings))
    def available(path):
        try:validate_phone_destination(device,working_root,path,settings)
        except (ValueError,OSError,RuntimeError):return False
        return not (path.is_symlink() or path.is_junction()) and (not path.exists() or path.is_dir())
    if not same_model and available(default):return default
    if same_model:
        token=hashlib.sha256(device.identity.casefold().encode()).hexdigest()[:8]
        base=default.with_name(default.name+' - '+token)
    else:base=default
    candidate=base;number=2
    while not available(candidate):
        candidate=base.with_name(base.name+f' - {number}');number+=1
    return candidate


def remember_phone_destination(settings,device,working_root,destination,layout,include_videos=False):
    target=validate_phone_destination(device,working_root,destination,settings)
    updated=dict(_records(settings));record=dict(_record(settings,device) or {})
    for key in list(updated):
        if key.casefold()==device.identity.casefold():del updated[key]
    folders=dict(record['folders']) if isinstance(record.get('folders'),dict) else {}
    folders[_path_key(working_root)]=target.relative_to(Path(working_root).absolute()).as_posix()
    record.pop('destination',None)
    record.update(name=device.name,layout=layout,include_videos=bool(include_videos),folders=folders)
    updated[device.identity.casefold()]=record
    return updated
