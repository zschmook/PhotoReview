"""Small generated-data smoke test for the standalone Windows distribution."""
import json,tempfile
from pathlib import Path

def run(app,report_path):
    report_path=Path(report_path).absolute();report_path.parent.mkdir(parents=True,exist_ok=True)
    report={}
    try:
        from PIL import Image
        from .core import scan,make_groups,similarity
        from .ui import Window
        from .phones import enumerate_portable_devices
        from .gpu import GPUScorer
        import pythoncom
        from win32com.client.dynamic import Dispatch
        from win32com.shell import shell
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);photos=base/'pictures';photos.mkdir()
            for name in ('a.jpg','b.jpg'):Image.new('RGB',(100,100),'#5298b3').save(photos/name)
            items,errors=scan(photos,base/'cache');groups=make_groups(items)
            assert len(items)==2 and len(groups)==1 and not errors
            report.update(photos=len(items),groups=len(groups),cpu_score=similarity(*items))
            import wgpu # The optional backend must still be bundled.
            try:scorer=GPUScorer(items)
            except RuntimeError as error:
                report['gpu_optional_error']=str(error);scorer=None
            if scorer:
              try:
                report['gpu_score']=scorer.score_pairs([(items[0],items[1])])[0]
                report['gpu_backend']=scorer.info['backend_type'];report['gpu_device']=scorer.info['device']
                assert abs(report['gpu_score']-report['cpu_score'])<=.02
              finally:scorer.close()
            from .heic import jpeg_copy
            heic=photos/'generated.HEIC';Image.new('RGB',(40,30),'teal').save(heic,format='HEIF')
            jpeg,created=jpeg_copy(heic,base/'conversions.sqlite3')
            with Image.open(heic) as image:assert image.size==(40,30)
            with Image.open(jpeg) as image:assert image.format=='JPEG'
            report['heic_decode_and_jpeg_conversion']=bool(created and heic.exists())
            report['portable_devices']=len(enumerate_portable_devices())
            pythoncom.CoInitialize()
            try:
                application=Dispatch('Shell.Application');assert application.NameSpace(17) is not None
                operation=pythoncom.CoCreateInstance(shell.CLSID_FileOperation,None,pythoncom.CLSCTX_ALL,shell.IID_IFileOperation)
                report['shell_copy_delete_interfaces']=True # No operation is queued or performed.
            finally:pythoncom.CoUninitialize()
            window=Window(preferences=base/'preferences.json');window.draft=base/'draft.json'
            window.show();app.processEvents()
            report['icon_loaded']=not window.windowIcon().isNull();assert report['icon_loaded']
            window.grab().save(str(report_path.with_suffix('.png')));window.close();app.processEvents()
            assert all(Path(p.path).exists() for p in items)
        report.update(ok=True,original_files_accessed=False,recycled=0,downloaded=0)
    except Exception as error:
        import traceback
        report.update(ok=False,error=str(error),traceback=traceback.format_exc())
    report_path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    return 0 if report.get('ok') else 1
