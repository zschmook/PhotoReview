"""Shared single-EXE Nuitka build; development output never goes in dist."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from photo_review.version import VERSION

ROOT=Path(__file__).resolve().parents[1]
STAGE=ROOT/'.build'/'nuitka'
RELEASE=ROOT/'dist'/'PhotoReview.exe'


def build_command(extra=(),build_id=None):
    # A fresh immutable extraction cache for each build avoids stale DLLs after
    # updates while keeping repeated launches of the same EXE fast.
    build_id=build_id or uuid.uuid4().hex
    windows_version='.'.join((VERSION.split('.')+['0']*4)[:4])
    return [sys.executable,'-m','nuitka','main.py',
        '--mode=onefile','--enable-plugin=pyside6','--include-qt-plugins=multimedia','--windows-console-mode=disable',
        '--file-version='+windows_version,'--product-version='+windows_version,'--product-name=Photo Review','--file-description=Photo Review',
        '--windows-icon-from-ico=assets/photo-review.ico','--include-data-dir=assets=assets',
        '--include-package=wgpu','--include-module=_cffi_backend','--include-distribution-metadata=wgpu',
        '--include-package=pillow_heif','--include-module=_pillow_heif',
        '--user-package-configuration-file=nuitka-package.config.yml',
        '--onefile-tempdir-spec={CACHE_DIR}/PhotoReview/runtime/'+build_id,'--onefile-cache-mode=cached',
        '--assume-yes-for-downloads','--jobs=2','--output-dir=.build/nuitka','--output-filename=PhotoReview.exe',
        '--report=artifacts/nuitka-build-report.xml',*extra]


def publish(stage=STAGE,release=RELEASE):
    stage=Path(stage);release=Path(release);binary=stage/'PhotoReview.exe'
    if not binary.is_file():raise RuntimeError('Nuitka did not produce the expected single-file EXE: '+str(binary))
    release.parent.mkdir(parents=True,exist_ok=True)
    temporary=release.with_suffix('.exe.new')
    shutil.copy2(binary,temporary);temporary.replace(release)
    return release


def archive_legacy_outputs(root=ROOT):
    root=Path(root).resolve();archive=root/'.build'/'legacy-standalone';messages=[]
    for name in ('main.build','main.dist','main.onefile-build'):
        source=root/'dist'/name;target=archive/name
        if not source.exists():continue
        if source.is_symlink() or source.is_junction():
            messages.append('Left linked legacy folder in place: '+str(source));continue
        # Verify both absolute paths before moving any directory on Windows.
        if not source.resolve().is_relative_to(root) or not target.resolve().is_relative_to(root):raise RuntimeError('Legacy build path is outside the repository')
        if target.exists():
            messages.append('Kept existing legacy folder; archive already exists: '+str(source));continue
        archive.mkdir(parents=True,exist_ok=True)
        try:source.rename(target)
        except OSError as error:messages.append('Could not archive '+str(source)+': '+str(error))
    return messages


def main():
    extra=sys.argv[1:];dry_run='--dry-run' in extra;publish_only='--publish-only' in extra
    extra=[arg for arg in extra if arg not in ('--dry-run','--publish-only')]
    protected=('--mode','--standalone','--onefile','--output-dir','--output-filename')
    if any(arg.split('=',1)[0] in protected for arg in extra):
        print('This launcher manages one-file mode and output paths. Other Nuitka options, such as --jobs=4, can be passed through.',file=sys.stderr);return 2
    environment=os.environ.copy()
    environment['NUITKA_CACHE_DIR']=str(ROOT/'.nuitka-cache')
    environment['NUITKA_CACHE_DIR_DOWNLOADS']=str(ROOT/'.nuitka-cache'/'downloads')
    command=build_command(extra,build_id='BUILD_ID' if dry_run else None)
    if dry_run:
        print(subprocess.list2cmdline(command))
        print('Published EXE:',RELEASE);print('Intermediate files:',STAGE)
        return 0
    (ROOT/'artifacts').mkdir(exist_ok=True)
    try:
        if not publish_only:
            result=subprocess.call(command,cwd=ROOT,env=environment)
            if result:return result
        published=publish()
        for message in archive_legacy_outputs():print(message)
        print('Ready:',published);print('Distribute this EXE only. Settings and cache live under %LOCALAPPDATA%/PhotoReview/data.')
        return 0
    except KeyboardInterrupt:return 130
    except OSError as error:
        print(str(error)+'\nClose Photo Review, then run ./build.sh --publish-only to publish the already-built EXE.',file=sys.stderr);return 1
    except RuntimeError as error:print(error,file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
