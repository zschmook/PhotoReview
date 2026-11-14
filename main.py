import argparse
import atexit
import json
import os
from pathlib import Path
import sys


def main():
    parser=argparse.ArgumentParser(description='Photo Review desktop app')
    parser.add_argument('--demo',action='store_true',help='Open the repository sample set')
    parser.add_argument('--render-dir',type=Path,help='Render actual Qt widgets offscreen; no desktop window or file recycling')
    parser.add_argument('--selection',type=Path,help='Use an explicit photo-selection manifest, without scanning other image contents')
    parser.add_argument('--similarity',type=int,default=90,choices=range(60,101),metavar='60..100')
    parser.add_argument('--resume-review',type=Path,help='Resume a saved review and its photo selections')
    parser.add_argument('--all-samples',action='store_true',help='Review all photos below the repository samples folder')
    parser.add_argument('--matcher',choices=('cpu','auto','gpu'),default='auto',help='Optional cross-vendor GPU matching prototype')
    parser.add_argument('--smoke-test',type=Path,help='Validate packaged dependencies with tiny generated images and write a JSON report')
    parser.add_argument('-reset','--reset',action='store_true',help='Archive app settings/cache and start the first-run wizard again; original photos are untouched')
    parser.add_argument('--apply-update',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.apply_update:
        from photo_review.self_update import helper_main
        return helper_main(args.apply_update,smoke_report=args.smoke_test)
    if args.reset and (args.render_dir or args.smoke_test or args.resume_review or args.selection or args.all_samples or args.demo):
        parser.error('-reset starts a fresh profile; use it without review, render, demo, or smoke-test arguments')
    os.environ['PHOTO_REVIEW_MATCHER']=args.matcher
    if args.render_dir or args.smoke_test:os.environ['QT_QPA_PLATFORM']='offscreen'
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont,QFontDatabase
    from photo_review.ui import Window,ROOT,DATA,ReviewDialog,demo_paths
    from photo_review.core import scan,make_groups
    app=QApplication(sys.argv);app.setApplicationName('Photo Review');app.setFont(QFont('Segoe UI',10))
    from photo_review.version import VERSION
    app.setApplicationVersion(VERSION)
    if args.render_dir or args.smoke_test:
        for font in ('segoeui.ttf','seguisb.ttf','segoeuib.ttf'):
            QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/font))
    if args.smoke_test:
        from photo_review.smoke import run
        return run(app,args.smoke_test)
    if not args.render_dir:
        from photo_review.paths import prepare_user_data,acquire_profile_guard,reset_user_data
        try:
            guard=acquire_profile_guard();atexit.register(guard.close);app.aboutToQuit.connect(guard.close)
            if args.reset:
                backup=reset_user_data()
                if backup and sys.stdout is not None:print('Previous app profile archived:',backup)
            prepare_user_data()
        except Exception as error:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(None,'Could not start Photo Review',str(error))
            return 1
    window=Window()
    window.threshold.setValue(args.similarity)
    selection=json.loads(args.selection.read_text(encoding='utf-8')) if args.selection else None
    chosen=selection['paths'] if selection else None if args.all_samples else demo_paths()
    time_range=(selection['start_timestamp'],selection['end_timestamp']) if selection else None
    if args.render_dir:
        window.draft=DATA/('all-samples-render-draft.json' if args.all_samples else 'recent-month-draft.json' if selection else 'render-draft.json')
        window.selection_paths=chosen;window.time_range=time_range
        window.root=ROOT/'samples';window.photos,window.errors=scan(window.root,DATA,selected_paths=chosen,time_range=time_range)
        window.groups=make_groups(window.photos,threshold=args.similarity);window.match_key=window.matching_key();window.render();window.show();app.processEvents();window.fit();app.processEvents()
        args.render_dir.mkdir(exist_ok=True,parents=True)
        window.grab().save(str(args.render_dir/'01-slideshow.png'))
        if window.groups:
            group=window.groups[0]
            if not selection and not args.all_samples:
                group.photos[2].keep=False;group.photos[3].keep=False
            window.photo_index=min(2,len(group.photos)-1);window.compare_button.setChecked(True);window.change_compare();app.processEvents();window.fit();app.processEvents()
            window.grab().save(str(args.render_dir/'02-compare.png'))
        dialog=ReviewDialog(window);dialog.show();app.processEvents();dialog.grab().save(str(args.render_dir/'03-review-changes.png'))
        print(f'Rendered 3 actual Qt screenshots to {args.render_dir.resolve()}')
        print(f'{len(window.photos):,} photos, {len(window.groups):,} matching sets')
        dialog.close();window.close();return 0
    window.show()
    from PySide6.QtCore import QTimer
    QTimer.singleShot(0,window.start_usb_detection)
    QTimer.singleShot(1500,window.start_update_check)
    if args.resume_review:window.resume(str(args.resume_review))
    elif selection:window.start_scan(ROOT/'samples',selected_paths=chosen,time_range=time_range)
    elif args.all_samples:window.start_scan(ROOT/'samples')
    elif args.demo:window.load_demo()
    else:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0,window.startup_folder)
    return app.exec()


if __name__=='__main__':raise SystemExit(main())
