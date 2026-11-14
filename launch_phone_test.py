import sys,json,argparse
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from photo_review import ui,recycle
from photo_review.paths import acquire_profile_guard,reset_user_data

def prepare_test_profile(profile,reset=False):
    profile=Path(profile);guard=acquire_profile_guard(profile)
    try:
        backup=reset_user_data(profile) if reset else None
        profile.mkdir(parents=True,exist_ok=True)
        (profile/'REVIEW_ONLY').write_text('Phone-download test: local recycling and phone deletion are disabled.\n')
        (profile/'PHONE_DELETE_DISABLED').write_text('Phone originals cannot be deleted in this isolated test profile.\n')
        return guard,backup
    except Exception:guard.close();raise

def main():
    parser=argparse.ArgumentParser(description='Real phone downloads with deletion disabled in an isolated test profile')
    parser.add_argument('-reset','--reset',action='store_true',help='Archive the phone-test profile and reopen the first-run wizard')
    args=parser.parse_args()
    # This test profile is separate from both normal EXE and Python profiles.
    profile=Path(__file__).resolve().parent/'data'/'phone-test';guard,backup=prepare_test_profile(profile,args.reset)
    ui.DATA=profile;recycle.REVIEW_LOCK=profile/'REVIEW_ONLY'
    app=QApplication(sys.argv);app.setApplicationName('Photo Review');app.setFont(QFont('Segoe UI',10));app.aboutToQuit.connect(guard.close)
    window=ui.Window(preferences=profile/'preferences.json');window.review_lock=recycle.REVIEW_LOCK
    window.setWindowTitle('Photo Review — phone test — deletion disabled')
    if args.reset:window.show_welcome()
    else:
        waiting=profile/'waiting-for-phone';waiting.mkdir(exist_ok=True)
        window.root=waiting;window.review_radio.setChecked(True);window.render()
    window.show()
    window.phone_detection.setChecked(True)
    (profile/'session.json').write_text(json.dumps(dict(deletion_disabled=True,profile=str(profile),window_title=window.windowTitle(),reset=args.reset,archived_profile=str(backup) if backup else None,first_run=window.root is None)))
    try:return app.exec()
    finally:guard.close()

if __name__=='__main__':raise SystemExit(main())
