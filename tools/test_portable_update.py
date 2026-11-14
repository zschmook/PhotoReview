"""Exercise the real compiled helper/replacement using copies in a temporary folder."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from photo_review.self_update import StagedUpdate,file_hash,start_helper,verify_update_signature
from photo_review.version import VERSION


def main():
    parser=argparse.ArgumentParser();parser.add_argument('exe',type=Path);parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    report={'ok':False,'original_files_accessed':False,'recompiled':False}
    try:
        with tempfile.TemporaryDirectory(prefix='photoreview-updater-') as temporary:
            base=Path(temporary).resolve()
            assert base.is_relative_to(Path(tempfile.gettempdir()).resolve()) and base.name.startswith('photoreview-updater-')
            target=base/'portable app'/'PhotoReview.exe';target.parent.mkdir();stage=base/'update staging';stage.mkdir()
            candidate=stage/'PhotoReview.new.exe';helper=stage/'PhotoReview.update-helper.exe'
            for path in (target,candidate,helper):shutil.copyfile(args.exe,path)
            verify_update_signature(target,candidate,version=VERSION)
            checksum=file_hash(target);inode=target.stat().st_ino
            # A separate process holds the EXE without delete sharing, as a running
            # Windows executable does. The updater must wait for its exit.
            holder_script='''import ctypes,sys,time
from ctypes import wintypes
kernel=ctypes.WinDLL("kernel32",use_last_error=True)
kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
kernel.CreateFileW.restype=wintypes.HANDLE
kernel.CloseHandle.argtypes=[wintypes.HANDLE]
handle=kernel.CreateFileW(sys.argv[1],0x80000000,1,None,3,0,None)
assert handle not in (None,ctypes.c_void_p(-1).value)
print("locked",flush=True)
time.sleep(5)
kernel.CloseHandle(handle)
'''
            holder=subprocess.Popen([sys.executable,'-u','-c',holder_script,str(target)],stdout=subprocess.PIPE,text=True)
            try:
                assert holder.stdout.readline().strip()=='locked'
                holder.stdout.close()
                staged=StagedUpdate(target,candidate,helper,VERSION,checksum,checksum)
                startup_report=stage/'startup-smoke.json'
                process=start_helper(staged,parent_pid=holder.pid,smoke_report=startup_report)
                assert process.wait(timeout=180)==0,'Compiled update helper did not succeed'
                assert holder.wait(timeout=10)==0
                startup=json.loads(startup_report.read_text(encoding='utf-8'));assert startup['ok']
                transaction=json.loads((stage/'result.json').read_text());assert transaction['status']=='installed'
                assert target.stat().st_ino!=inode,'Portable EXE was not replaced'
                assert file_hash(target)==checksum and file_hash(helper)==checksum
                assert not candidate.exists()
                assert not list(target.parent.glob('*.previous-*')) and not list(target.parent.glob('*.new-*'))
                report.update(ok=True,helper_waited_for_exit=True,replaced_same_path=True,restarted_packaged_app=True,previous_exe_preserved=True,startup=startup)
            finally:
                if holder.poll() is None:holder.wait(timeout=10)
    except Exception as error:
        import traceback
        report.update(error=str(error),traceback=traceback.format_exc())
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2));return 0 if report['ok'] else 1


if __name__=='__main__':raise SystemExit(main())
