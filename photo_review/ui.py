"""Native Qt desktop interface. No web server or container."""
from datetime import datetime
from pathlib import Path
import json
import threading
import os
from time import monotonic
from PySide6.QtCore import Qt,QSize,QThread,Signal,QTimer,QStandardPaths,QRectF,QAbstractListModel,QModelIndex,QEvent,QRect,QPointF
from PySide6.QtGui import QAction,QKeySequence,QPixmap,QColor,QBrush,QFont,QIcon,QPainter,QPen,QPolygonF
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,
    QPushButton,QFrame,QSlider,QListWidget,QListWidgetItem,QListView,QSplitter,QCheckBox,QScrollArea,QLineEdit,QComboBox,
    QDialog,QTableWidget,QTableWidgetItem,QHeaderView,QFileDialog,QMessageBox,QGraphicsView,QRadioButton,QButtonGroup,
    QGraphicsScene,QAbstractItemView,QProgressBar,QTreeWidget,QTreeWidgetItem,QStackedWidget,QStyledItemDelegate,QStyle,QStyleOptionButton)
from .core import Group,scan,make_groups,similarity,save_draft,restore_draft,validate_plan
from .recycle import apply_plan

from .paths import ROOT,DATA
from .version import VERSION


def demo_paths():
    # Keep sample/demo operations bounded even after a real camera roll is added.
    return [str(path.absolute()) for folder in ('01 - Lakeside afternoon','02 - Forest walk','03 - Weekend coast')
            for path in (ROOT/'demo-assets'/folder).glob('*.jpg')] + [str(path.absolute()) for path in (ROOT/'demo-assets').glob('*-source.jpg')]


STYLE='''
* {font-family:"Segoe UI";font-size:13px;color:#243247;}
QMainWindow,QDialog {background:#f5f7fa;}
QMenuBar {background:#ffffff;padding:5px 12px;border-bottom:1px solid #e2e7ee;}
QMenuBar::item {padding:5px 14px;} QMenuBar::item:selected {background:#eaf1fc;border-radius:4px;}
QMenu {background:white;border:1px solid #dce2eb;padding:6px;} QMenu::item {padding:7px 28px;}
QPushButton {background:white;border:1px solid #d4dce7;border-radius:5px;padding:8px 13px;}
QPushButton:hover {background:#eef4ff;border-color:#9ab8ed;}
QPushButton:pressed,QPushButton:checked {background:#e3edfd;border-color:#5385d8;color:#124b9b;}
QPushButton:disabled {color:#a4adba;background:#f6f7f9;border-color:#e1e5eb;}
QPushButton#primary {background:#2563cf;color:white;border:1px solid #2563cf;font-weight:600;}
QPushButton#primary:hover {background:#1d54b7;} QPushButton#primary:disabled {background:#b2c4df;border-color:#b2c4df;}
QPushButton#quiet {border:none;background:transparent;color:#4d6079;}
QFrame#ribbon {background:white;border-bottom:1px solid #dce3ed;}
QLabel#eyebrow {color:#738197;font-size:10px;font-weight:700;letter-spacing:1px;}
QLabel#title {font-size:22px;font-weight:600;color:#18283e;}
QLabel#muted {color:#748196;font-size:12px;}
QLabel#small {color:#748196;font-size:11px;}
QLabel#badge {background:#e9f0ff;color:#235eb9;border-radius:4px;padding:5px 8px;font-size:11px;}
QLabel#green {color:#2d765b;background:#eaf5ee;border-radius:4px;padding:6px 9px;font-size:11px;}
QLabel#warning {color:#a55226;background:#fff2e7;border-radius:5px;padding:9px;font-size:12px;}
QLabel#danger {color:#b13b44;background:#fff0f0;border-radius:4px;padding:7px;font-size:12px;}
QListWidget {background:#f5f7fa;border:none;outline:none;padding:4px;}
QListWidget::item {border:1px solid transparent;border-radius:6px;margin:3px 0;padding:10px 10px;}
QListWidget::item:selected {background:#e6efff;border-color:#c6d8f6;color:#1f4d91;}
QListWidget::item:hover:!selected {background:#edf0f5;}
QFrame#sidebar {background:#f5f7fa;border-right:1px solid #dce3ed;}
QFrame#inspector {background:white;border-left:1px solid #dce3ed;}
QFrame#photoCard {background:white;border:1px solid #dce3ed;border-radius:7px;}
QGraphicsView {background:#17212d;border:none;}
QFrame#tile {background:white;border:1px solid #dce3ed;border-radius:6px;}
QFrame#tile[selected="true"] {border:2px solid #3274da;background:#f0f5ff;}
QFrame#tile[removed="true"] {border:1px solid #e6b0b6;background:#fff4f4;}
QCheckBox {spacing:9px;} QCheckBox::indicator {width:17px;height:17px;}
QScrollArea {border:none;background:transparent;}
QSlider::groove:horizontal {height:4px;background:#e1e7ef;border-radius:2px;}
QSlider::sub-page:horizontal {background:#5b8bd9;border-radius:2px;}
QSlider::handle:horizontal {background:white;border:2px solid #3978d2;width:12px;height:12px;margin:-5px 0;border-radius:7px;}
QStatusBar {background:white;border-top:1px solid #dce3ed;color:#6e7c90;padding:3px;}
QTableWidget {background:white;border:1px solid #dce3ed;border-radius:5px;gridline-color:#edf0f4;}
QHeaderView::section {background:#f2f5f9;padding:10px;border:none;border-bottom:1px solid #dce3ed;color:#64748b;font-weight:600;}
QProgressBar {border:none;background:#e4ebf5;border-radius:3px;height:5px;}QProgressBar::chunk{background:#3274da;border-radius:3px;}
'''


def label(text='',name=None):
    item=QLabel(text)
    if name:item.setObjectName(name)
    return item


def button(text,handler=None,primary=False):
    item=QPushButton(text)
    if handler:item.clicked.connect(handler)
    if primary:item.setObjectName('primary')
    return item


def format_size(value):
    return f'{value/1024**2:.1f} MB' if value>=1024**2 else f'{value/1024:.0f} KB'


def media_count(count,noun='photos'):return f'{count:,} {noun[:-1] if count==1 else noun}'


def phone_media_noun(report,count=None):
    word='file' if report.get('video_total',0) else 'picture'
    return word if count==1 else word+'s'


def phone_import_status(report):
    from .phone_download import import_status
    return import_status(report)


class Worker(QThread):
    result=Signal(object)
    error=Signal(str)
    progress=Signal(str)
    counts=Signal(int,int)
    def __init__(self,job,counted=False):super().__init__();self.job=job;self.counted=counted
    def run(self):
        try:self.result.emit(self.job(self.progress.emit,self.counts.emit) if self.counted else self.job(self.progress.emit))
        except Exception as error:self.error.emit(str(error))


class Spinner(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.setFixedSize(44,44);self.angle=0
        self.timer=QTimer(self);self.timer.setInterval(35);self.timer.timeout.connect(self.advance)
    def advance(self):self.angle=(self.angle+12)%360;self.update()
    def showEvent(self,event):super().showEvent(event);self.timer.start()
    def hideEvent(self,event):self.timer.stop();super().hideEvent(event)
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen=QPen(QColor('#dce6f6'),4);painter.setPen(pen);painter.drawEllipse(QRectF(5,5,34,34))
        pen.setColor(QColor('#2563cf'));pen.setCapStyle(Qt.PenCapStyle.RoundCap);painter.setPen(pen)
        painter.drawArc(QRectF(5,5,34,34),-self.angle*16,105*16)


class BusyDialog(QDialog):
    def __init__(self,parent,cancel):
        super().__init__(parent);self.cancel=cancel;self.completed=False
        self.setWindowTitle('Loading pictures');self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint,False);self.setFixedWidth(470)
        box=QVBoxLayout(self);box.setContentsMargins(25,24,25,22);box.setSpacing(14)
        row=QHBoxLayout();self.spinner=Spinner(self);row.addWidget(self.spinner)
        self.counter=label('Finding pictures…','title');self.counter.setWordWrap(True);row.addWidget(self.counter,1);box.addLayout(row)
        self.detail=label('Checking the selected folder.','muted');self.detail.setTextFormat(Qt.TextFormat.PlainText);self.detail.setWordWrap(True);box.addWidget(self.detail)
        actions=QHBoxLayout();actions.addStretch();self.cancel_button=button('Cancel',self.reject);actions.addWidget(self.cancel_button);box.addLayout(actions)
    def update_counts(self,completed,total):
        if not self.cancel.is_set():self.counter.setText(f'Scanning {completed:,} of {total:,} pictures')
    def update_progress(self,message):
        if self.cancel.is_set():return
        self.detail.setText(message)
        if message.startswith(('Grouping','Regrouping','GPU matching','CPU fallback')):self.counter.setText('Comparing pictures…')
    def reject(self):
        if self.completed:super().reject();return
        self.cancel.set();self.counter.setText('Cancelling…');self.cancel_button.setEnabled(False)
    def closeEvent(self,event):
        if self.completed:super().closeEvent(event)
        else:self.reject();event.ignore()
    def finish(self):self.completed=True;self.spinner.timer.stop();self.accept()


class HeicConversionProgressDialog(BusyDialog):
    def __init__(self,parent,cancel):
        super().__init__(parent,cancel);self.setWindowTitle('Creating JPEG copies')
        self.counter.setText('Finding HEIC photos…');self.detail.setText('HEIC originals will be kept.')
    def update_counts(self,completed,total):
        if not self.cancel.is_set():self.counter.setText(f'Converting {completed:,} of {total:,} HEIC photos')


class RecycleProgressDialog(BusyDialog):
    def __init__(self,parent):
        super().__init__(parent,threading.Event());self.setWindowTitle('Processing files')
        self.counter.setText('Checking files…');self.detail.setText('Verifying your files before recycling.');self.cancel_button.hide()
    def update_counts(self,completed,total):self.counter.setText(f'Recycling {completed:,} of {total:,} files')
    def update_progress(self,message):
        if message.startswith(('Checking ','Updating ')):self.counter.setText(message)
        else:self.detail.setText(message)
    def reject(self):
        if self.completed:super().reject()


class DownloadProgressDialog(BusyDialog):
    def __init__(self,parent,cancel):
        QDialog.__init__(self,parent);self.cancel=cancel;self.completed=False;self.spinner=Spinner(self);self.spinner.hide()
        self.setWindowTitle('Downloading phone files');self.setWindowModality(Qt.WindowModality.ApplicationModal);self.setFixedWidth(570)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint,False)
        self.importing=False;self.existing='';self.source_label='phone'
        self.transfer_started=None;self.transfer_bytes=0;self.file_total=0
        box=QVBoxLayout(self);box.setContentsMargins(26,24,26,24);box.setSpacing(16)
        self.heading=label('Finding files on phone','title');box.addWidget(self.heading)
        self.existing_label=label('Already on PC: Checking…');self.existing_label.setWordWrap(True);self.existing_label.setStyleSheet('font-size:16px;font-weight:600;');box.addWidget(self.existing_label)
        row=QHBoxLayout();self.counter=label('Listing files — total not known yet');self.counter.setWordWrap(True);self.counter.setStyleSheet('font-size:19px;font-weight:600;');row.addWidget(self.counter,1)
        self.percent=label('');row.addWidget(self.percent);box.addLayout(row)
        self.meter=QProgressBar();self.meter.setRange(0,0);self.meter.setTextVisible(False);self.meter.setFixedHeight(8);box.addWidget(self.meter)
        row=QHBoxLayout();self.speed=label('— MB/s · No files downloading yet');self.speed.setStyleSheet('font-size:17px;font-weight:600;');row.addWidget(self.speed,1)
        self.elapsed=label('','muted');row.addWidget(self.elapsed);box.addLayout(row)
        self.transfer_summary='';self.stats=label('','muted');self.stats.setWordWrap(True);box.addWidget(self.stats)
        self.detail_heading=label('CURRENT FOLDER','eyebrow');box.addWidget(self.detail_heading)
        self.detail=label('Waiting for the phone…');self.detail.setWordWrap(True);self.detail.setTextFormat(Qt.TextFormat.PlainText);box.addWidget(self.detail)
        self.connection_note=label('Keep your phone unlocked and connected.\nOriginals stay on your phone.','muted');box.addWidget(self.connection_note)
        actions=QHBoxLayout();actions.addStretch();self.cancel_button=button('Cancel',self.reject);actions.addWidget(self.cancel_button);box.addLayout(actions)
        self.speed_timer=QTimer(self);self.speed_timer.setInterval(500);self.speed_timer.timeout.connect(self.update_speed)
    def update_counts(self,completed,total):
        if self.cancel.is_set():return
        if total<=0:
            self.counter.setText('No new files to download' if self.importing else 'Listing files — total not known yet');self.percent.clear();self.meter.setRange(0,0);return
        self.counter.setText(f'Downloading: {completed:,} / {total:,}' if self.importing else f'Reading phone details: {completed:,} of {total:,} files')
        self.meter.setRange(0,total);self.meter.setValue(completed);self.percent.setText(f'{100*completed/total:.0f}%')
    def update_progress(self,message):
        if self.cancel.is_set():return
        if message.startswith('Transfer bytes · '):
            self.transfer_bytes=int(message.split(' · ')[1]);self.update_speed()
            return
        parts=message.split(' · ')
        if message.startswith(('Finding phone pictures · ','Finding phone media · ','Finding USB pictures · ')):
            self.counter.setText(parts[1]);self.detail.setText(parts[2] if len(parts)>2 else 'Listing files…')
        elif message.startswith(('Reading phone details · ','Checking phone checksums · ','Checking PC checksums · ','Reading USB details · ','Checking USB checksums · ')):
            self.heading.setText('Checking existing files');self.counter.setText(message.replace(' · ',': ',1).split(' · ',1)[0]);self.detail_heading.setText('CURRENT FILE')
            if len(parts)>2:
                if parts[2].endswith('already exist on PC'):self.existing_label.setText('Already on PC: '+parts[2].removesuffix(' already exist on PC')+' files')
                else:self.detail.setText(parts[2])
            self.percent.clear();self.meter.setRange(0,0)
        elif message.startswith('Import plan · '):
            self.importing=True;self.existing=parts[2];self.file_total=int(parts[1].split(' new files')[0].replace(',',''))
            self.heading.setText('Importing USB files' if self.source_label=='USB' else 'Downloading phone files');self.existing_label.setText('Already on PC: '+self.existing.removesuffix(' already exist on PC')+' files');self.detail_heading.setText('CURRENT FILE')
            self.transfer_started=monotonic();self.transfer_bytes=0;self.update_counts(0,self.file_total);self.update_speed();self.speed_timer.start()
        elif message.startswith('Import status · '):
            self.existing_label.setText(parts[1].replace('Already exist on PC:','Already on PC:'))
            self.transfer_summary=parts[2].replace('Newly downloaded: ','') if len(parts)>2 else '';self.update_speed()
        elif message.startswith('No new files to download.'):
            self.heading.setText('All files already on PC');self.counter.setText('No new files to download');self.meter.setRange(0,1);self.meter.setValue(1);self.percent.clear();self.speed.setText('— MB/s · Nothing to download');self.speed_timer.stop()
        elif message.startswith(('Downloading ','Copy phone picture: ','Copy phone video: ')):
            self.detail.setText(message)
        else:self.detail.setText(message)
    def update_speed(self):
        if self.transfer_started is None:return
        elapsed=max(.001,monotonic()-self.transfer_started) if self.transfer_started is not None else .001
        self.speed.setText(f'{self.transfer_bytes/1_000_000/elapsed:.2f} MB/s avg' if self.transfer_bytes else '— MB/s · Waiting for transfer')
        seconds=int(elapsed);self.elapsed.setText(f'Elapsed {seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}')
        self.stats.setText(format_size(self.transfer_bytes)+' downloaded'+(' · '+self.transfer_summary if self.transfer_summary else ''))
    def finish(self):self.speed_timer.stop();super().finish()


class USBImportProgressDialog(DownloadProgressDialog):
    def __init__(self,parent,cancel):
        super().__init__(parent,cancel);self.setWindowTitle('Importing USB pictures')
        self.source_label='USB';self.heading.setText('Finding files on USB drive');self.connection_note.setText('Keep your USB drive connected.\nOriginals stay on the drive.')


class USBImportDialog(QDialog):
    def __init__(self,parent,drive,destination,include_videos=False):
        super().__init__(parent);self.setWindowTitle('USB drive connected');self.resize(620,300)
        box=QVBoxLayout(self);box.setContentsMargins(24,22,24,22);box.setSpacing(14)
        heading=label('Import pictures from '+drive.name+'?','title');heading.setTextFormat(Qt.TextFormat.PlainText);heading.setWordWrap(True);box.addWidget(heading)
        note=label('Existing pictures are checked first. Only new files are imported.');note.setWordWrap(True);box.addWidget(note)
        scope=label('Searches all folders on the USB drive, including subfolders.','muted');scope.setWordWrap(True);box.addWidget(scope)
        box.addWidget(label('SAVE PICTURES TO','eyebrow'))
        self.destination_label=label(str(destination));self.destination_label.setTextFormat(Qt.TextFormat.PlainText);self.destination_label.setWordWrap(True);box.addWidget(self.destination_label)
        description=label('Pictures go directly into this folder. Use File → Change folder to choose another library.','muted');description.setWordWrap(True);box.addWidget(description)
        self.include_videos=QCheckBox('Also import videos');self.include_videos.setChecked(bool(include_videos));box.addWidget(self.include_videos)
        self.video_note=label('Videos: '+str(Path(destination)/'Videos')+'\nVideos are not included in photo comparison.','muted');self.video_note.setTextFormat(Qt.TextFormat.PlainText);self.video_note.setWordWrap(True);box.addWidget(self.video_note)
        keep=label('Keep the USB drive connected until importing finishes. Originals stay on the USB drive.','warning');keep.setWordWrap(True);box.addWidget(keep)
        actions=QHBoxLayout();actions.addWidget(button('Not now',self.reject));actions.addStretch();self.import_button=button('Import pictures',self.accept,True);actions.addWidget(self.import_button);box.addLayout(actions)
        def videos_changed(checked):
            self.video_note.setVisible(checked);self.import_button.setText('Import pictures and videos' if checked else 'Import pictures')
        self.include_videos.toggled.connect(videos_changed);videos_changed(self.include_videos.isChecked())


class PhoneVerificationProgressDialog(BusyDialog):
    def __init__(self,parent,cancel):
        super().__init__(parent,cancel);self.setWindowTitle('Checking all phone files')
        self.counter.setText('Checking file checksums…');self.detail.setText('No phone originals are removed during this check.')
    def update_counts(self,completed,total):
        if not self.cancel.is_set():self.counter.setText(f'Checksums matched: {completed:,} of {total:,} files')


class PhoneCleanupProgressDialog(BusyDialog):
    def __init__(self,parent,cancel):
        super().__init__(parent,cancel);self.setWindowTitle('Removing phone originals')
        self.counter.setText('Verifying downloaded pictures…');self.detail.setText('Keep the phone unlocked and connected. Saved copies stay on this computer.')
    def update_counts(self,completed,total):
        if not self.cancel.is_set():self.counter.setText(f'Processed {completed:,} of {total:,} files')


class PhoneReadyDialog(QDialog):
    def __init__(self,parent,device):
        from .phones import is_apple_phone
        super().__init__(parent);self.setWindowTitle('Phone detected');self.resize(510,240)
        box=QVBoxLayout(self);box.setContentsMargins(24,22,24,22);box.setSpacing(16)
        heading=label(device.name+' detected','title');heading.setTextFormat(Qt.TextFormat.PlainText);heading.setWordWrap(True);box.addWidget(heading)
        apple=is_apple_phone(device)
        note=label('Unlock your iPhone and allow photo access or trust this computer if prompted. Keep it unlocked while Windows connects.' if apple else 'Unlock your phone. In its USB options, select File transfer (sometimes called MTP).');note.setWordWrap(True);box.addWidget(note)
        note=label('Press OK when your iPhone is unlocked and photo access is allowed.' if apple else 'Press OK when your phone is unlocked and in USB mode.');note.setWordWrap(True);note.setStyleSheet('font-weight:600;');box.addWidget(note)
        box.addWidget(label('Take your time. No photos are scanned or downloaded until you continue.','muted'))
        row=QHBoxLayout();row.addWidget(button('Not now',self.reject));row.addStretch();self.ok_button=button('OK',self.accept,True);self.ok_button.setDefault(True);row.addWidget(self.ok_button);box.addLayout(row)


class PhoneDownloadDialog(QDialog):
    def __init__(self,parent,device,destination,layout='day-tree',include_videos=False):
        from .phones import is_apple_phone
        super().__init__(parent);self.device=device;self.setWindowTitle('Download phone pictures');self.resize(640,400)
        box=QVBoxLayout(self);box.setContentsMargins(24,22,24,22);box.setSpacing(14)
        heading=label('Download pictures from '+device.name+'?','title');heading.setTextFormat(Qt.TextFormat.PlainText);heading.setWordWrap(True);box.addWidget(heading)
        intro=label('Compare phone files with this folder using checksums, then download only files that are missing.','muted');intro.setWordWrap(True);box.addWidget(intro)
        scope=label('Search all photo-accessible iPhone folders and subfolders, including date folders under Internal Storage. Thumbnail folders are skipped.' if is_apple_phone(device) else 'Search only: DCIM, Pictures, Download, Documents, and Movies (including subfolders). Thumbnail folders are skipped.','muted');scope.setWordWrap(True);box.addWidget(scope)
        box.addWidget(label('SAVE PICTURES TO','eyebrow'));self.destination=QLineEdit(str(destination));box.addWidget(self.destination)
        location_note=label('You can edit this folder inside your selected library. Use File → Change folder to work somewhere else.','muted');location_note.setWordWrap(True);box.addWidget(location_note)
        row=QHBoxLayout();row.addWidget(label('Organize by'));self.layout_choice=QComboBox()
        for title,value in [('Year / Month / Day','day-tree'),('Year / Month','month-tree'),('Date folder (YYYY-MM-DD)','day-folder')]:self.layout_choice.addItem(title,value)
        self.layout_choice.setCurrentIndex(max(0,self.layout_choice.findData(layout)));row.addWidget(self.layout_choice);row.addStretch();box.addLayout(row)
        self.include_videos=QCheckBox('Also download videos');self.include_videos.setChecked(bool(include_videos));box.addWidget(self.include_videos)
        self.example=label('','muted');self.example.setWordWrap(True);box.addWidget(self.example)
        self.video_example=label('','muted');self.video_example.setWordWrap(True);box.addWidget(self.video_example)
        self.include_videos.toggled.connect(self.update_example)
        self.destination.textChanged.connect(self.update_example);self.layout_choice.currentIndexChanged.connect(self.update_example);self.update_example()
        note=label('This might take a long time. Do not unplug your phone.\nFiles are copied; phone originals stay unchanged during download. Capture dates are used first, with modified time as a fallback.','warning');note.setWordWrap(True);note.setMinimumHeight(76);box.addWidget(note)
        row=QHBoxLayout();row.addWidget(button('Not now',self.reject));row.addStretch();self.download=button('Download pictures and videos' if self.include_videos.isChecked() else 'Download pictures',self.accept,True);row.addWidget(self.download);box.addLayout(row)
        self.include_videos.toggled.connect(lambda checked:self.download.setText('Download pictures and videos' if checked else 'Download pictures'))
        self.destination.textChanged.connect(lambda text:self.download.setEnabled(bool(text.strip())))
    def accept(self):
        from .phone_folders import validate_phone_destination
        if not self.destination.text().strip():return
        owner=self.parentWidget()
        try:validate_phone_destination(self.device,owner.root,self.destination.text().strip(),owner.read_preferences().get('phone_imports',{}))
        except ValueError as error:QMessageBox.warning(self,'Choose a library folder',str(error));return
        super().accept()
    def update_example(self,*args):
        from .phone_download import LAYOUTS
        sample=datetime.now().strftime(LAYOUTS[self.layout_choice.currentData()])
        self.example.setText('Example: '+str(Path(self.destination.text())/sample))
        self.video_example.setText('Videos: '+str(Path(self.destination.text())/'Videos'/sample)+'\nVideos are downloaded only; they are not included in photo comparison.')
        self.video_example.setVisible(self.include_videos.isChecked())


class PhoneCleanupDialog(QDialog):
    def __init__(self,parent,device,report):
        super().__init__(parent);self.setWindowTitle('Phone download complete');self.resize(850,560)
        candidates=report['cleanup_candidates'];count=len(candidates)
        box=QVBoxLayout(self);box.setContentsMargins(24,22,24,22);box.setSpacing(14)
        title=label('Remove downloaded '+phone_media_noun(report)+' from '+device.name+'?','title');title.setTextFormat(Qt.TextFormat.PlainText);title.setWordWrap(True);box.addWidget(title)
        total=report.get('total',len(report.get('verification_candidates',candidates)))
        summary=label(phone_import_status(report));summary.setWordWrap(True);box.addWidget(summary)
        location=label('Saved copies: '+report['destination'],'muted');location.setTextFormat(Qt.TextFormat.PlainText);location.setWordWrap(True);box.addWidget(location)
        note=label(f'All {total:,} saved files, including previous imports, will be checksum-verified against the phone. A second confirmation will appear before any originals are removed. Keep the phone unlocked and connected.');note.setWordWrap(True);box.addWidget(note)
        self.table=QTableWidget(count,2);self.table.setHorizontalHeaderLabels(['Phone original','Saved copy'])
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.verticalHeader().hide();self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row,candidate in enumerate(candidates):
            for column,value in enumerate((candidate['phone_path'],candidate['local_path'])):
                display=value
                if column==1:
                    try:display=str(Path(value).relative_to(Path(report['destination'])))
                    except ValueError:pass
                item=QTableWidgetItem(display);item.setToolTip(value);self.table.setItem(row,column,item)
        box.addWidget(self.table,1)
        if report.get('cleanup_protected'):
            protected_count=report['cleanup_protected'];noun=phone_media_noun(report,protected_count)
            protected=label(f"{protected_count:,} {noun} with uncopied video/edit companions will stay on the phone.",'muted');protected.setWordWrap(True);box.addWidget(protected)
        warning=label('Phone deletion does not use the Windows Recycle Bin and may be permanent. Saved copies will remain in your selected folder.','warning');warning.setWordWrap(True);box.addWidget(warning)
        self.confirm=QCheckBox(f'I want to remove these {count:,} {phone_media_noun(report,count)} from the phone.');self.confirm.setStyleSheet('font-weight:700;font-size:14px;');box.addWidget(self.confirm)
        actions=QHBoxLayout();self.keep_button=button('Keep on phone',self.reject);self.keep_button.setDefault(True);actions.addWidget(self.keep_button);actions.addStretch()
        self.verify_button=button('Verify all files' if report.get('video_total',0) else 'Verify all pictures',self.accept,True);self.verify_button.setAutoDefault(False);self.verify_button.setEnabled(False)
        actions.addWidget(self.verify_button);box.addLayout(actions)
        self.confirm.toggled.connect(self.verify_button.setEnabled)
    def accept(self):
        if self.confirm.isChecked():super().accept()


class PhoneCleanupFinalDialog(QMessageBox):
    def __init__(self,parent,device,report,verification):
        super().__init__(parent);self.setWindowTitle('Final confirmation');self.setIcon(QMessageBox.Icon.Warning)
        self.setTextFormat(Qt.TextFormat.PlainText)
        count=len(report['cleanup_candidates']);noun=phone_media_noun(report,count)
        self.setText(f'Delete {count:,} {noun} from {device.name}?')
        self.setInformativeText(f"All {verification['verified']:,} file checksums match the saved copies.\n\nPhone deletion may be permanent and does not use the Windows Recycle Bin.\n\nSaved copies will remain at:\n{report['destination']}")
        self.cancel_button=self.addButton('Cancel',QMessageBox.ButtonRole.RejectRole)
        self.delete_button=self.addButton(f'Delete {count:,} {noun}',QMessageBox.ButtonRole.DestructiveRole)
        self.delete_button.setAutoDefault(False)
        self.delete_button.setStyleSheet('QPushButton {background:#b91c1c;color:white;}')
        self.setDefaultButton(self.cancel_button);self.setEscapeButton(self.cancel_button)


class PhotoListModel(QAbstractListModel):
    """Qt creates only visible thumbnail cells, even for a whole camera roll."""
    keep_changed=Signal(int,bool)
    def __init__(self,parent=None):super().__init__(parent);self.photos=[];self.icons={}
    def set_photos(self,photos):
        if photos is self.photos:return
        self.beginResetModel();self.photos=photos;self.icons.clear();self.endResetModel()
    def rowCount(self,parent=QModelIndex()):return 0 if parent.isValid() else len(self.photos)
    def data(self,index,role):
        if not index.isValid() or not 0<=index.row()<len(self.photos):return None
        photo=self.photos[index.row()]
        if role==Qt.ItemDataRole.DisplayRole:return 'Keep'
        if role==Qt.ItemDataRole.DecorationRole:
            if photo.identity not in self.icons:
                if len(self.icons)>=128:self.icons.pop(next(iter(self.icons)))
                self.icons[photo.identity]=video_icon() if photo.is_video else QIcon(photo.thumbnail)
            return self.icons[photo.identity]
        if role==Qt.ItemDataRole.CheckStateRole:return Qt.CheckState.Checked if photo.keep else Qt.CheckState.Unchecked
        if role==Qt.ItemDataRole.ToolTipRole:return photo.name
        if role==Qt.ItemDataRole.SizeHintRole:return QSize(132,122)
    def flags(self,index):return super().flags(index)|Qt.ItemFlag.ItemIsUserCheckable
    def setData(self,index,value,role):
        if role!=Qt.ItemDataRole.CheckStateRole or not index.isValid() or not 0<=index.row()<len(self.photos):return False
        self.keep_changed.emit(index.row(),value in (Qt.CheckState.Checked,Qt.CheckState.Checked.value))
        self.dataChanged.emit(index,index,[role]);return True


class PhotoThumbnailDelegate(QStyledItemDelegate):
    def paint(self,painter,option,index):
        painter.save();painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        kept=index.data(Qt.ItemDataRole.CheckStateRole)==Qt.CheckState.Checked
        selected=bool(option.state&QStyle.StateFlag.State_Selected)
        painter.fillRect(option.rect,QColor('#f5f7fa'))
        card=QRectF(option.rect.adjusted(2,2,-2,-2))
        painter.setBrush(QColor('#eef4ff' if selected else '#ffffff' if kept else '#fff4f4'))
        painter.setPen(QPen(QColor('#3274da' if selected else '#d4dce7' if kept else '#e6b0b6'),2 if selected else 1));painter.drawRoundedRect(card,5,5)
        image=index.data(Qt.ItemDataRole.DecorationRole).pixmap(QSize(120,82))
        painter.drawPixmap(option.rect.left()+(option.rect.width()-image.width())//2,option.rect.top()+7+(82-image.height())//2,image)
        check=QStyleOptionButton();check.rect=QRect(option.rect.left()+9,option.rect.bottom()-25,17,17)
        check.state=QStyle.StateFlag.State_Enabled|(QStyle.StateFlag.State_On if kept else QStyle.StateFlag.State_Off)
        QApplication.style().drawControl(QStyle.ControlElement.CE_CheckBox,check,painter)
        painter.setPen(QColor('#243247'));painter.drawText(option.rect.adjusted(33,92,-5,-5),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,'Keep');painter.restore()
    def editorEvent(self,event,model,option,index):
        if event.type()==QEvent.Type.MouseButtonRelease and event.button()==Qt.MouseButton.LeftButton:
            footer=QRect(option.rect.left(),option.rect.bottom()-31,option.rect.width(),31)
            if footer.contains(event.position().toPoint()):
                checked=index.data(Qt.ItemDataRole.CheckStateRole)==Qt.CheckState.Checked
                return model.setData(index,Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked,Qt.ItemDataRole.CheckStateRole)
        return False


def video_icon():
    pixmap=QPixmap(120,82);pixmap.fill(QColor('#17212d'));painter=QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing);painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor('white'))
    painter.drawPolygon(QPolygonF([QPointF(49,27),QPointF(49,55),QPointF(74,41)]));painter.end()
    return QIcon(pixmap)


class PhotoView(QGraphicsView):
    def __init__(self):
        super().__init__();self.scene=QGraphicsScene(self);self.setScene(self.scene)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag);self.photo=None;self.fitted=True
    def load(self,photo):
        self.photo=photo;self.scene.clear();self.scene.addPixmap(QPixmap(photo.thumbnail))
        self.scene.setSceneRect(self.scene.itemsBoundingRect());self.fit()
    def fit(self):
        self.fitted=True
        if self.scene.items():self.fitInView(self.scene.sceneRect(),Qt.AspectRatioMode.KeepAspectRatio)
    def actual(self):
        if self.photo and not self.photo.is_video:
            from .heic import HEIC_EXTENSIONS
            if Path(self.photo.path).suffix.lower() in HEIC_EXTENSIONS:
                from PIL import Image,ImageOps
                from PIL.ImageQt import ImageQt
                with Image.open(self.photo.path) as image:pixmap=QPixmap.fromImage(ImageQt(ImageOps.exif_transpose(image).convert('RGB')))
            else:pixmap=QPixmap(self.photo.path)
            self.scene.clear();self.scene.addPixmap(pixmap);self.scene.setSceneRect(self.scene.itemsBoundingRect())
            self.resetTransform();self.fitted=False
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self.fitted:self.fit()
        self.position_navigation()
    def install_navigation(self,previous,next_image):
        self.image_back=button('‹',previous);self.image_next=button('›',next_image)
        for arrow,description in [(self.image_back,'Previous image (Left arrow)'),(self.image_next,'Next image (Right arrow)')]:
            arrow.setParent(self.viewport());arrow.setFixedSize(48,68);arrow.setToolTip(description);arrow.setAccessibleName(description)
            arrow.setStyleSheet('QPushButton {font-size:40px;color:white;background:rgba(20,30,43,180);border:1px solid rgba(255,255,255,90);border-radius:7px;padding:0;} QPushButton:hover {background:rgba(48,91,150,220);} QPushButton:disabled {color:rgba(255,255,255,65);border-color:rgba(255,255,255,25);background:rgba(20,30,43,90);}')
            arrow.show();arrow.raise_()
        self.position_navigation()
    def position_navigation(self):
        if not hasattr(self,'image_back'):return
        y=max(0,(self.viewport().height()-68)//2)
        self.image_back.move(14,y);self.image_next.move(max(14,self.viewport().width()-62),y)
        self.image_back.raise_();self.image_next.raise_()
    def wheelEvent(self,event):
        self.fitted=False;scale=1.18 if event.angleDelta().y()>0 else 1/1.18
        self.scale(scale,scale)


class PhotoPanel(QFrame):
    dimensions_changed=Signal(int,int)
    def __init__(self):
        super().__init__();self.setObjectName('photoCard')
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(0)
        self.view=PhotoView();layout.addWidget(self.view,1)
        self.video_controls=None;self.loaded_identity=None
        foot=QWidget();row=QHBoxLayout(foot);row.setContentsMargins(13,10,13,10)
        self.caption=label();self.position=label('','muted');row.addWidget(self.caption,1);row.addWidget(self.position)
        layout.addWidget(foot)
    def load(self,photo,position):
        if self.loaded_identity!=photo.identity:
            self.clear();self.view.photo=photo
            if photo.is_video:
                if self.video_controls is None:
                    from .media_preview import VideoControls
                    self.video_controls=VideoControls(self.view,self);self.layout().insertWidget(1,self.video_controls)
                    self.video_controls.dimensions_changed.connect(self.dimensions_changed)
                self.video_controls.show();self.video_controls.load(photo.path)
            else:self.view.load(photo)
            self.loaded_identity=photo.identity
        self.caption.setText(photo.name);self.position.setText(position)
    def pause(self):
        if self.video_controls:self.video_controls.pause()
    def clear(self):
        if self.video_controls:self.video_controls.release();self.video_controls.hide()
        self.view.scene.clear();self.view.photo=None;self.loaded_identity=None
    def hideEvent(self,event):self.pause();super().hideEvent(event)
    def closeEvent(self,event):self.clear();super().closeEvent(event)


class ReviewDialog(QDialog):
    def __init__(self,window):
        super().__init__(window);self.owner=window;self.test_mode=window.review_lock.exists();self.setWindowTitle('Review changes — Photo Review')
        self.resize(960,min(840,max(500,window.screen().availableGeometry().height()-60)))
        self.preview_row=None;self.preview_photo=None;self.preview_panel=None;self.preview_keep=None
        self.processing_dialog=None;self.operation_records=[];self.processing_result=None;self.processing_error=None
        self.review_photos=[p for p in window.photos if not p.keep]
        self.noun='files' if any(p.is_video for p in window.photos) else 'photos'
        self.photos_by_id={p.identity:p for p in self.review_photos}
        layout=QVBoxLayout(self);layout.setContentsMargins(28,25,28,25);layout.setSpacing(15)
        layout.addWidget(label('ONE LAST LOOK','eyebrow'));layout.addWidget(label('Review your selections','title'))
        self.summary_values={};row=QHBoxLayout()
        removed=self.pending_photos()
        for title,value in [('KEEP',media_count(len(window.photos)-len(removed),self.noun)),('RECYCLE',media_count(len(removed),self.noun)),('SPACE TO RECOVER',format_size(sum(p.size for p in removed)))]:
            card=QFrame();card.setObjectName('photoCard');box=QVBoxLayout(card);box.setContentsMargins(16,14,16,14)
            box.addWidget(label(title,'eyebrow'));self.summary_values[title]=label(value,'title');box.addWidget(self.summary_values[title]);row.addWidget(card)
        layout.addLayout(row)
        note=label('Select a row to preview the file. Checked means Keep; unchecked means Recycle.','muted');note.setWordWrap(True);layout.addWidget(note)
        self.table=QTableWidget(len(self.review_photos),4);self.table.setHorizontalHeaderLabels(['File' if self.noun=='files' else 'Photo','Folder','Size','Action'])
        self.table.verticalHeader().hide();self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for col in (0,2,3):self.table.horizontalHeader().setSectionResizeMode(col,QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        for i,photo in enumerate(self.review_photos):
            for j,text in enumerate((photo.name,Path(photo.folder).name,format_size(photo.size),'Recycle')):
                item=QTableWidgetItem(text);item.setData(Qt.ItemDataRole.UserRole,photo.identity);item.setToolTip(photo.path);self.table.setItem(i,j,item)
            self.table.setRowHeight(i,42);self.update_action_row(photo)
        layout.addWidget(self.table,1)
        self.notice=label('REVIEW-ONLY TEST: accepting will not move, recycle, or delete any photos.' if self.test_mode else 'Unchecked files will go to the Windows Recycle Bin. Nothing has been moved yet.','warning');self.notice.setWordWrap(True);layout.addWidget(self.notice)
        self.confirm=QCheckBox(self.confirmation_text(len(removed)));self.confirm.setStyleSheet('font-weight:700;font-size:14px;');self.confirm.setEnabled(bool(removed));layout.addWidget(self.confirm)
        actions=QHBoxLayout();self.back_button=button('Back to review',self.reject);actions.addWidget(self.back_button);actions.addStretch()
        self.accept_button=button('Test selections' if self.test_mode else 'Accept && Recycle',self.apply,True);self.accept_button.setEnabled(False);actions.addWidget(self.accept_button);layout.addLayout(actions)
        self.confirm.toggled.connect(lambda checked:self.accept_button.setEnabled(checked and bool(self.pending_photos())))
        self.table.currentCellChanged.connect(self.preview_selected)
    def pending_photos(self):return [p for p in self.owner.photos if not p.keep]
    def confirmation_text(self,count):return 'I reviewed '+('this ' if count==1 else 'these ')+media_count(count,self.noun)+' and want to move '+('it' if count==1 else 'them')+' to the Recycle Bin.'
    def photo_row(self,identity):
        for row in range(self.table.rowCount()):
            item=self.table.item(row,0)
            if item and item.data(Qt.ItemDataRole.UserRole)==identity:return row
        return None
    def update_action_row(self,photo):
        row=self.photo_row(photo.identity)
        if row is None:return
        item=self.table.item(row,3);item.setText('Keep' if photo.keep else 'Recycle')
        item.setForeground(QBrush(QColor('#2d765b' if photo.keep else '#b13b44')))
    def preview_selected(self,row,column,previous_row,previous_column):
        item=self.table.item(row,0)
        identity=item.data(Qt.ItemDataRole.UserRole) if item else None
        photo=self.photos_by_id.get(identity)
        if photo is None or photo is self.preview_photo:return
        blocked=self.table.blockSignals(True)
        try:
            if self.preview_photo is not None:
                previous=self.photo_row(self.preview_photo.identity)
                if previous is not None:
                    filename=self.table.item(previous,0);font=filename.font();font.setBold(False);filename.setFont(font)
            if self.preview_panel:self.preview_panel.clear()
            if self.preview_row is not None:self.table.removeRow(self.preview_row)
            row=self.photo_row(identity)
            self.table.setCurrentCell(row,0)
            filename=self.table.item(row,0);font=filename.font();font.setBold(True);filename.setFont(font)
            self.preview_row=row+1;self.preview_photo=photo
            self.table.insertRow(self.preview_row);self.table.setSpan(self.preview_row,0,1,4)
            for col in range(4):
                spacer=QTableWidgetItem();spacer.setFlags(Qt.ItemFlag.NoItemFlags);self.table.setItem(self.preview_row,col,spacer)
            preview_height=min(276,max(160,self.table.viewport().height()-42))
            self.preview_panel=PhotoPanel();self.preview_panel.view.setFixedHeight(max(100,preview_height-56-(48 if photo.is_video else 0)))
            self.preview_panel.load(photo,'');self.preview_panel.position.hide()
            if self.preview_panel.view.scene.itemsBoundingRect().isEmpty():
                message=self.preview_panel.view.scene.addText('Preview unavailable');message.setDefaultTextColor(QColor('white'));self.preview_panel.view.fit()
            self.preview_status=label(('Keeping this video' if photo.is_video else 'Keeping this photo') if photo.keep else 'Marked for recycling','green' if photo.keep else 'danger')
            self.preview_keep=QCheckBox('Keep');self.preview_keep.setAccessibleName('Keep '+photo.name);self.preview_keep.setChecked(photo.keep)
            footer=self.preview_panel.position.parentWidget().layout();footer.addWidget(self.preview_status);footer.addWidget(self.preview_keep)
            self.preview_keep.toggled.connect(lambda checked,p=photo:self.set_review_keep(p,checked))
            self.table.setCellWidget(self.preview_row,0,self.preview_panel);self.table.setRowHeight(self.preview_row,preview_height)
            self.table.scrollToItem(self.table.item(row,0),QAbstractItemView.ScrollHint.PositionAtTop)
        finally:self.table.blockSignals(blocked)
    def set_review_keep(self,photo,checked):
        photo.keep=checked
        for group in self.owner.groups:
            if any(p.identity==photo.identity for p in group.photos):group.skipped=False
        self.update_action_row(photo)
        self.preview_status.setText(('Keeping this video' if photo.is_video else 'Keeping this photo') if checked else 'Marked for recycling')
        self.preview_status.setObjectName('green' if checked else 'danger');self.preview_status.style().unpolish(self.preview_status);self.preview_status.style().polish(self.preview_status)
        removed=self.pending_photos()
        self.summary_values['KEEP'].setText(media_count(len(self.owner.photos)-len(removed),self.noun))
        self.summary_values['RECYCLE'].setText(media_count(len(removed),self.noun))
        self.summary_values['SPACE TO RECOVER'].setText(format_size(sum(p.size for p in removed)))
        self.confirm.setChecked(False);self.confirm.setEnabled(bool(removed));self.confirm.setText(self.confirmation_text(len(removed)))
        self.accept_button.setEnabled(False)
        self.owner.render();self.owner.save()
    def apply(self):
        if hasattr(self,'worker') and self.worker.isRunning():return
        test_mode=self.test_mode or self.owner.review_lock.exists()
        if not test_mode and (not self.confirm.isChecked() or not self.pending_photos()):return
        self.owner.current.clear();self.owner.reference.clear()
        if self.preview_panel:self.preview_panel.clear()
        self.accept_button.setEnabled(False);self.confirm.setEnabled(False);self.table.setEnabled(False)
        self.back_button.setEnabled(False)
        journal=DATA/('recycle-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.jsonl')
        self.notice.setText('Checking test selections…' if test_mode else 'Moving selected files to the Windows Recycle Bin…')
        photos=list(self.owner.photos);groups=list(self.owner.groups);compare=self.owner.mode=='compare'
        minutes,threshold=self.owner.minutes.value(),self.owner.threshold.value()
        self.operation_records=[];self.processing_result=None;self.processing_error=None
        self.processing_dialog=RecycleProgressDialog(self);self.processing_dialog.show()
        def job(progress,counts):
            check_progress=lambda x,y:progress(f'Checking {x:,} of {y:,} files')
            if test_mode:
                selected=validate_plan(photos,groups,progress=check_progress)
                return dict(test=True,count=len(selected),bytes=sum(p.size for p in selected))
            def verified(completed,total,photo):
                if photo is not None:self.operation_records.append(photo.identity);progress(photo.name)
                counts(completed,total)
            records=apply_plan(photos,groups,journal,progress=verified,validation_progress=check_progress)
            remaining=[p for p in photos if p.keep]
            progress('Updating the picture list…')
            matches=make_groups(remaining,minutes,threshold,backend=os.environ.get('PHOTO_REVIEW_MATCHER','auto'),progress=progress) if compare else []
            return dict(records=records,photos=remaining,groups=matches,bytes=sum(p.size for p in photos if not p.keep))
        self.worker=Worker(job,counted=True)
        self.worker.progress.connect(self.processing_dialog.update_progress);self.worker.counts.connect(self.processing_dialog.update_counts)
        self.worker.result.connect(lambda result:setattr(self,'processing_result',result))
        self.worker.error.connect(lambda error:setattr(self,'processing_error',error))
        self.worker.finished.connect(self.processing_finished);self.worker.start()
    def close_processing(self):
        if self.processing_dialog:self.processing_dialog.finish();self.processing_dialog.deleteLater();self.processing_dialog=None
    def processing_finished(self):
        self.close_processing();self.back_button.setEnabled(True)
        if self.processing_error:self.failed(self.processing_error)
        elif self.processing_result is not None:self.complete(self.processing_result)
    def complete(self,result):
        if result.get('test'):
            QMessageBox.information(self,'Test complete',f"You would have cleared {result['bytes']/1_000_000:,.2f} MB by recycling {result['count']:,} photos.\n\nThis was a test. No photos were moved, recycled, or deleted.")
            self.accept();return
        self.notice.setText(f"{len(result['records'])} {self.noun} recycled. The operation journal is saved in data/.")
        self.owner.photos=result['photos'];self.owner.groups=result['groups']
        self.owner.match_key=self.owner.matching_key() if self.owner.mode=='compare' else None;self.owner.mode_positions.clear()
        self.owner.group_index=0;self.owner.photo_index=0;self.owner.render();self.owner.save();self.confirm.hide()
        self.accept_button.setText('Done');self.accept_button.setEnabled(True);self.accept_button.clicked.disconnect();self.accept_button.clicked.connect(self.accept)
        self.success_dialog=QMessageBox(self);self.success_dialog.setWindowTitle('Photos recycled');self.success_dialog.setIcon(QMessageBox.Icon.Information)
        self.success_dialog.setText('Success!')
        count=len(result['records']);noun=('file' if count==1 else 'files') if self.noun=='files' else ('photo' if count==1 else 'photos')
        self.success_dialog.setInformativeText(f"{count:,} {noun} ({format_size(result['bytes'])}) moved to the Recycle Bin.\n\nClose the app or keep sorting?")
        self.close_app_button=self.success_dialog.addButton('Close app',QMessageBox.ButtonRole.RejectRole)
        self.keep_sorting_button=self.success_dialog.addButton('Keep sorting',QMessageBox.ButtonRole.AcceptRole)
        self.success_dialog.setDefaultButton(self.keep_sorting_button);self.success_dialog.setEscapeButton(self.keep_sorting_button)
        self.success_dialog.finished.connect(self.success_finished);self.success_dialog.open()
    def success_finished(self,result):
        close=self.success_dialog.clickedButton() is self.close_app_button
        self.accept()
        if close:self.owner.close()
        else:QTimer.singleShot(0,self.owner.offer_device_imports)
    def failed(self,error):
        if self.operation_records:
            completed=set(self.operation_records);self.owner.photos=[p for p in self.owner.photos if p.identity not in completed]
            remaining=[]
            for group in self.owner.groups:
                items=[p for p in group.photos if p.identity not in completed]
                if len(items)>1:remaining.append(Group(items,group.minimum_score,group.skipped))
            self.owner.groups=remaining;self.owner.match_key=None;self.owner.mode_positions.clear()
            self.owner.group_index=0;self.owner.photo_index=0;self.owner.render();self.owner.save()
        count=len(self.operation_records);noun='photo' if count==1 else 'photos'
        self.notice.setText(f'Stopped after recycling {count:,} {noun}: {error}\nReturn to review and check the operation journal before trying again.')
    def reject(self):
        if hasattr(self,'worker') and self.worker.isRunning():return
        self.close_processing()
        super().reject()
        QTimer.singleShot(0,self.owner.offer_device_imports)
    def done(self,result):
        if self.preview_panel:self.preview_panel.clear()
        super().done(result)
    def closeEvent(self,event):
        if hasattr(self,'worker') and self.worker.isRunning():event.ignore()
        else:super().closeEvent(event)


class Window(QMainWindow):
    def __init__(self,preferences=None):
        super().__init__();self.setWindowIcon(QIcon(str(ROOT/'assets'/'photo-review.ico')));self.setWindowTitle('Photo Review v'+VERSION);self.resize(1720,990);self.setMinimumSize(1340,800)
        self.photos=[];self.groups=[];self.errors=[];self.group_index=0;self.photo_index=0;self.root=None;self.selection_paths=None;self.time_range=None;self.folder_filter=None;self.tree_signature=None
        self.mode='compare';self.match_key=None;self.mode_positions={};self.busy_dialog=None;self.review_media='all'
        self.cancel=threading.Event();self.worker=None;self.draft=DATA/'review-draft.json';self.preferences=Path(preferences) if preferences is not None else DATA/'preferences.json';self.rebuilding=False
        self.review_lock=DATA/'REVIEW_ONLY'
        self.phone_watcher=None;self.phone_watchers=[];self.closing=False
        self.connected_phones=[];self.offered_phones=set();self.phone_prompt_open=False;self.after_job=None
        self.usb_watcher=None;self.usb_watchers=[];self.connected_usb=[];self.offered_usb=set();self.usb_prompt_open=False
        self.update_checked=False;self.update_worker=None;self.pending_update=None;self.update_notice=None
        self.update_display_timer=QTimer(self);self.update_display_timer.setInterval(500);self.update_display_timer.timeout.connect(self.show_update_if_ready)
        self.setStyleSheet(STYLE);self.build_menu()
        host=QWidget();self.setCentralWidget(host);layout=QVBoxLayout(host);layout.setContentsMargins(0,0,0,0);layout.setSpacing(0)
        layout.addWidget(self.ribbon())
        self.progress=QProgressBar();self.progress.setMaximumHeight(5);self.progress.setRange(0,0);self.progress.hide();layout.addWidget(self.progress)
        body=QSplitter(Qt.Orientation.Horizontal);body.setChildrenCollapsible(False);layout.addWidget(body,1)
        body.addWidget(self.folder_sidebar());body.addWidget(self.workspace());body.addWidget(self.inspector());body.setSizes([265,1210,245])
        review_host=self.takeCentralWidget()
        self.pages=QStackedWidget();self.welcome_page=QWidget();welcome_layout=QVBoxLayout(self.welcome_page)
        welcome_layout.addStretch();center=QHBoxLayout();center.addStretch()
        self.start_panel.setMaximumWidth(640);center.addWidget(self.start_panel);center.addStretch()
        welcome_layout.addLayout(center);welcome_layout.addStretch()
        self.pages.addWidget(self.welcome_page);self.pages.addWidget(review_host);self.setCentralWidget(self.pages)
        self.statusBar().showMessage('Choose where your pictures are stored to begin')
        self.add_shortcut('Right',lambda:self.navigate_photo(1));self.add_shortcut('Left',lambda:self.navigate_photo(-1))
        self.add_shortcut('Down',lambda:self.navigate_group(1));self.add_shortcut('Up',lambda:self.navigate_group(-1))
        self.add_shortcut('Space',self.toggle_keep);self.add_shortcut('Ctrl+S',self.save)
        self.phone_status=label('Phone detection off','muted');self.statusBar().addPermanentWidget(self.phone_status)
        settings=self.menuBar().addMenu('Settings');self.phone_action=QAction('Enable phone detection',self);self.phone_action.setCheckable(True);settings.addAction(self.phone_action)
        self.phone_action.toggled.connect(self.phone_detection.setChecked)
        self.phone_detection.toggled.connect(self.set_phone_detection)
        self.phone_detection.setChecked(bool(self.read_preferences().get('phone_detection_enabled',False)))
        self.usb_action=QAction('Detect USB drives',self);self.usb_action.setCheckable(True);self.usb_action.setChecked(bool(self.read_preferences().get('usb_detection_enabled',True)));settings.addAction(self.usb_action)
        self.usb_status=label('USB detection on' if self.usb_action.isChecked() else 'USB detection off','muted');self.statusBar().addPermanentWidget(self.usb_status)
        self.usb_action.toggled.connect(self.set_usb_detection)
        if self.read_preferences().get('review_mode')=='review':self.review_radio.setChecked(True)
        self.render()
    def start_update_check(self):
        if self.update_checked or self.closing:return
        self.update_checked=True
        from .updates import check_latest
        self.update_worker=Worker(lambda progress:check_latest())
        self.update_worker.result.connect(self.update_check_complete)
        # Offline, throttled or malformed responses never interrupt reviewing.
        self.update_worker.error.connect(lambda error:None)
        self.update_worker.start()
    def update_check_complete(self,update):
        if update is not None and not self.closing:
            self.pending_update=update;self.update_display_timer.start();self.show_update_if_ready()
    def show_update_if_ready(self):
        if self.closing:self.update_display_timer.stop();return
        if self.pending_update is None:self.update_display_timer.stop();return
        if not self.root or (self.worker and self.worker.isRunning()) or QApplication.activeModalWidget() is not None:return
        update=self.pending_update;self.pending_update=None;self.update_display_timer.stop()
        self.update_notice=QMessageBox(self);self.update_notice.setWindowTitle('Photo Review update available')
        self.update_notice.setIcon(QMessageBox.Icon.Information);self.update_notice.setTextFormat(Qt.TextFormat.PlainText)
        self.update_notice.setText('Photo Review v'+update.version+' is available.')
        self.update_notice.setInformativeText('You have v'+VERSION+'. Download the new EXE, then close Photo Review and replace the old EXE. Your saved settings stay on this computer.')
        download=self.update_notice.addButton('Download update',QMessageBox.ButtonRole.AcceptRole)
        later=self.update_notice.addButton('Later',QMessageBox.ButtonRole.RejectRole);self.update_notice.setDefaultButton(later)
        def selected(button):
            if button is download:
                from PySide6.QtCore import QUrl
                from PySide6.QtGui import QDesktopServices
                QDesktopServices.openUrl(QUrl(update.download_url))
        self.update_notice.buttonClicked.connect(selected);self.update_notice.open()
    def read_preferences(self):
        try:
            result=json.loads(self.preferences.read_text(encoding='utf-8'))
            return result if isinstance(result,dict) else {}
        except (OSError,ValueError):return {}
    def write_preferences(self,**changes):
        values=self.read_preferences();values.update(changes)
        self.preferences.parent.mkdir(parents=True,exist_ok=True);temporary=self.preferences.with_suffix('.tmp')
        temporary.write_text(json.dumps(values),encoding='utf-8');temporary.replace(self.preferences)
    def set_phone_status(self,text):
        self.phone_status.setText(text);self.phone_note.setText(text)
    def start_usb_detection(self):self.set_usb_detection(self.usb_action.isChecked())
    def set_usb_detection(self,enabled):
        blocked=self.usb_action.blockSignals(True);self.usb_action.setChecked(enabled);self.usb_action.blockSignals(blocked)
        try:self.write_preferences(usb_detection_enabled=enabled)
        except OSError:self.statusBar().showMessage('Could not save USB detection preference')
        if self.usb_watcher:self.usb_watcher.stop();self.usb_watcher=None
        if not enabled:
            self.connected_usb=[];self.offered_usb.clear();self.usb_status.setText('USB detection off');return
        self.usb_status.setText('Waiting for a USB drive')
        from .usb_import import USBWatcher
        watcher=USBWatcher(self);self.usb_watcher=watcher;self.usb_watchers.append(watcher)
        watcher.devices_changed.connect(lambda drives:self.usb_devices_changed(drives) if self.usb_watcher is watcher else None)
        watcher.detection_error.connect(lambda message:self.usb_status.setText('USB detection unavailable: '+message) if self.usb_watcher is watcher else None)
        watcher.finished.connect(lambda:self.usb_watcher_finished(watcher));watcher.start()
    def usb_devices_changed(self,drives):
        self.connected_usb=drives;self.offered_usb.intersection_update({drive.identity.casefold() for drive in drives})
        self.usb_status.setText('USB connected: '+', '.join(drive.name for drive in drives) if drives else 'Waiting for a USB drive')
        QTimer.singleShot(0,self.offer_device_imports)
    def offer_device_imports(self):
        self.offer_phone_download();self.offer_usb_import()
    def offer_usb_import(self):
        if self.closing or self.phone_prompt_open or self.usb_prompt_open or not self.root or (self.worker and self.worker.isRunning()) or QApplication.activeModalWidget() is not None:return
        drive=next((drive for drive in self.connected_usb if drive.identity.casefold() not in self.offered_usb),None)
        if drive is None:return
        self.offered_usb.add(drive.identity.casefold());self.usb_prompt_open=True;working_root=Path(self.root)
        try:
            prompt=USBImportDialog(self,drive,working_root,self.read_preferences().get('usb_include_videos',False))
            accepted=prompt.exec()==QDialog.DialogCode.Accepted
        finally:self.usb_prompt_open=False
        if accepted and not self.closing:
            if not any(current.identity==drive.identity and current.root==drive.root for current in self.connected_usb):
                QMessageBox.warning(self,'USB drive disconnected','Reconnect the USB drive and try importing again.');QTimer.singleShot(0,self.offer_device_imports);return
            include_videos=prompt.include_videos.isChecked();self.write_preferences(usb_include_videos=include_videos)
            self.start_usb_import(drive,working_root,include_videos)
        QTimer.singleShot(0,self.offer_device_imports)
    def choose_usb_import(self):
        if self.worker and self.worker.isRunning():return
        if not self.connected_usb:
            QMessageBox.information(self,'Connect a USB drive','Enable Detect USB drives in Settings, then connect a USB thumb drive.');return
        self.offered_usb.clear();self.offer_usb_import()
    def start_usb_import(self,drive,working_root,include_videos=False):
        if self.worker and self.worker.isRunning():return
        from .usb_import import import_usb
        if not self.root or Path(working_root)!=Path(self.root):return
        working_root=Path(working_root)
        def job(progress,counts):return import_usb(drive,working_root,DATA/'phone-imports.sqlite3',include_videos,progress,counts,self.cancel)
        def ready(report):
            DATA.mkdir(parents=True,exist_ok=True);(DATA/'usb-import-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            self.after_job=lambda:self.finish_usb_import(report,working_root)
        self.run_job(job,ready,counted=True,dialog_class=USBImportProgressDialog,accept_cancelled=True)
    def finish_usb_import(self,report,working_root):
        message=phone_import_status(report)+f"\nIssues: {len(report['errors']):,}.\n\nOriginals were left on the USB drive."
        if report.get('stopped'):message+='\n\n'+report['stopped']
        if report['errors']:
            first=report['errors'][0];message+='\n\nFirst issue: '+first['path']+'\n'+first['error']
        if report.get('jpeg_copies'):message+=f"\nJPEG copies created: {report['jpeg_copies']:,}. HEIC originals were kept."
        if report.get('unsupported_review'):message+=f"\n\n{report['unsupported_review']:,} imported files use formats the reviewer cannot display yet (such as unsupported RAW formats). They were still saved."
        if report['errors'] or report.get('stopped'):QMessageBox.warning(self,'USB import finished with issues',message)
        else:QMessageBox.information(self,'USB import cancelled' if report['cancelled'] else 'USB import complete',message)
        if not self.closing:self.start_scan(working_root)
    def usb_watcher_finished(self,watcher):
        if watcher in self.usb_watchers:self.usb_watchers.remove(watcher)
        if self.usb_watcher is watcher:self.usb_watcher=None
        watcher.deleteLater()
        if self.closing:self.close()
    def set_phone_detection(self,enabled):
        self.phone_action.setChecked(enabled)
        try:self.write_preferences(phone_detection_enabled=enabled)
        except OSError:self.statusBar().showMessage('Could not save phone detection preference')
        if self.phone_watcher:
            self.phone_watcher.stop();self.phone_watcher=None
        if not enabled:
            self.connected_phones=[];self.offered_phones.clear()
            self.set_phone_status('Phone detection off');return
        from .phones import PhoneWatcher
        watcher=PhoneWatcher(self);self.phone_watcher=watcher;self.phone_watchers.append(watcher)
        watcher.devices_changed.connect(lambda devices:self.phone_devices_changed(devices) if self.phone_watcher is watcher else None)
        watcher.detection_error.connect(lambda message:self.set_phone_status('Phone detection unavailable: '+message) if self.phone_watcher is watcher else None)
        watcher.finished.connect(lambda:self.phone_watcher_finished(watcher))
        self.set_phone_status('Waiting for a phone — unlock it and choose File transfer.');watcher.start()
    def phone_devices_changed(self,devices):
        self.connected_phones=devices
        self.offered_phones.intersection_update({d.identity.casefold() for d in devices})
        QTimer.singleShot(0,self.offer_device_imports)
        if devices:self.set_phone_status('Phone / portable device connected: '+', '.join(d.name for d in devices))
        else:self.set_phone_status('Waiting for a phone — unlock it and choose File transfer.')
    def offer_phone_download(self):
        if self.closing or self.phone_prompt_open or self.usb_prompt_open or not self.root or (self.worker and self.worker.isRunning()) or QApplication.activeModalWidget() is not None:return
        device=next((d for d in self.connected_phones if d.identity.casefold() not in self.offered_phones),None)
        if device is None:return
        self.offered_phones.add(device.identity.casefold());self.phone_prompt_open=True
        from .phone_folders import phone_destination,remember_phone_destination
        from .phones import same_physical_phone
        working_root=Path(self.root)
        settings=self.read_preferences().get('phone_imports',{})
        saved=next((value for key,value in settings.items() if key.casefold()==device.identity.casefold() and isinstance(value,dict)),{}) if isinstance(settings,dict) else {}
        try:
            if PhoneReadyDialog(self,device).exec()!=QDialog.DialogCode.Accepted:return
            prompt=PhoneDownloadDialog(self,device,phone_destination(device,working_root,settings),saved.get('layout','day-tree'),include_videos=saved.get('include_videos',False))
            accepted=prompt.exec()==QDialog.DialogCode.Accepted
        finally:
            self.phone_prompt_open=False
            # Switching USB mode can re-enumerate the same phone with a new ID
            # while the user is answering the readiness prompt.
            self.offered_phones.update(d.identity.casefold() for d in self.connected_phones if same_physical_phone(d,device))
        if accepted and not self.closing:
            destination=Path(prompt.destination.text().strip());layout=prompt.layout_choice.currentData()
            include_videos=prompt.include_videos.isChecked()
            try:settings=remember_phone_destination(self.read_preferences().get('phone_imports',{}),device,working_root,destination,layout,include_videos)
            except ValueError as error:QMessageBox.warning(self,'Choose a phone folder',str(error));return
            self.write_preferences(phone_imports=settings);self.start_phone_download(device,destination,layout,include_videos=include_videos)
    def start_phone_download(self,device,destination,layout='day-tree',include_videos=False):
        if self.worker and self.worker.isRunning():return
        from .phone_download import download_phone
        from .phone_folders import validate_phone_destination
        if not self.root:return
        working_root=Path(self.root)
        try:destination=validate_phone_destination(device,working_root,destination,self.read_preferences().get('phone_imports',{}))
        except ValueError as error:QMessageBox.warning(self,'Choose a library folder',str(error));return
        def job(progress,counts):return download_phone(device,destination,DATA/'phone-imports.sqlite3',layout,progress,counts,self.cancel,working_root=working_root,include_videos=include_videos)
        def ready(report):
            DATA.mkdir(parents=True,exist_ok=True);(DATA/'phone-import-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            self.after_job=lambda:self.finish_phone_download(report,working_root,device)
        self.run_job(job,ready,counted=True,dialog_class=DownloadProgressDialog,accept_cancelled=True)
    def choose_phone_download(self):
        if self.worker and self.worker.isRunning():return
        if not self.connected_phones:
            QMessageBox.information(self,'Connect a phone','Enable phone detection in Settings, then connect and unlock your phone and choose File transfer.');return
        self.offered_phones.clear();self.offer_phone_download()
    def finish_phone_download(self,report,working_root,device=None):
        from .phone_cleanup import cleanup_available
        if device is not None and cleanup_available(report) and not (DATA/'PHONE_DELETE_DISABLED').exists() and not self.closing:
            prompt=PhoneCleanupDialog(self,device,report)
            if prompt.exec()==QDialog.DialogCode.Accepted:
                self.start_phone_verification(device,report,working_root,confirmed=True);return
            self.start_scan(working_root);return
        message=phone_import_status(report)+f"\nDownloaded size: {format_size(report['bytes'])}.\nIssues: {len(report['errors']):,}.\n\nPhone originals were left untouched."
        if report.get('stopped'):message+='\n\n'+report['stopped']
        if report['errors']:
            first=report['errors'][0];message+='\n\nFirst issue: '+first['path']+'\n'+first['error']
        if report.get('jpeg_copies'):message+=f"\nJPEG copies created: {report['jpeg_copies']:,}. HEIC originals were kept."
        if report.get('unsupported_review'):message+=f"\n\n{report['unsupported_review']:,} downloaded files use formats the reviewer cannot display yet (such as unsupported RAW formats). They were still saved."
        if report.get('cleanup_protected'):message+=f"\n\n{report['cleanup_protected']:,} pictures have uncopied video/edit companions and will stay on the phone."
        if report.get('stopped'):QMessageBox.warning(self,'Phone import stopped',message)
        elif report['errors']:QMessageBox.warning(self,'Phone import finished with issues',message)
        else:QMessageBox.information(self,'Import cancelled' if report['cancelled'] else 'Phone import complete',message)
        if not self.closing:self.start_scan(working_root)
    def start_phone_verification(self,device,report,working_root,confirmed=False):
        if not confirmed or self.closing or (self.worker and self.worker.isRunning()):return
        from .phone_cleanup import verify_phone_cleanup
        journal=DATA/('phone-verification-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.jsonl')
        def job(progress,counts):return verify_phone_cleanup(device,report,working_root,journal,progress,counts,self.cancel,confirmed=True)
        def ready(result):
            DATA.mkdir(parents=True,exist_ok=True)
            (DATA/'phone-verification-report.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            self.after_job=lambda:self.finish_phone_verification(device,report,working_root,result)
        self.run_job(job,ready,counted=True,dialog_class=PhoneVerificationProgressDialog,accept_cancelled=True)
    def finish_phone_verification(self,device,report,working_root,verification):
        from .phone_cleanup import verification_complete
        if self.closing:return
        if not verification_complete(verification,device,report,working_root):
            message=f"Checksums matched: {verification.get('verified',0):,} of {report['total']:,}.\nNo phone originals were deleted."
            if verification.get('errors'):
                first=verification['errors'][0];message+='\n\n'+first['path']+'\n'+first['error']
                QMessageBox.warning(self,'Phone checksum verification stopped',message)
            else:QMessageBox.information(self,'Phone checksum verification cancelled',message)
            self.start_scan(working_root);return
        final=PhoneCleanupFinalDialog(self,device,report,verification);final.exec()
        if final.clickedButton() is final.delete_button:
            self.start_phone_cleanup(device,report,working_root,confirmed=True,verification=verification)
        elif not self.closing:self.start_scan(working_root)
    def start_phone_cleanup(self,device,report,working_root,confirmed=False,verification=None):
        if not confirmed or verification is None or self.closing or (self.worker and self.worker.isRunning()):return
        from .phone_cleanup import cleanup_phone
        journal=DATA/('phone-cleanup-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.jsonl')
        def job(progress,counts):return cleanup_phone(device,report,working_root,journal,progress,counts,self.cancel,confirmed=True,verification=verification)
        def ready(result):
            DATA.mkdir(parents=True,exist_ok=True)
            (DATA/'phone-cleanup-report.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            self.after_job=lambda:self.finish_phone_cleanup(result,working_root)
        self.run_job(job,ready,counted=True,dialog_class=PhoneCleanupProgressDialog,accept_cancelled=True)
    def finish_phone_cleanup(self,report,working_root):
        message=f"Confirmed removed from phone: {report['deleted']:,} files ({format_size(report['bytes'])}).\nSaved copies remain in your selected folder."
        if report['errors']:
            first=report['errors'][0];message+='\n\nCleanup stopped: '+first['path']+'\n'+first['error']
            QMessageBox.warning(self,'Phone cleanup stopped',message)
        else:QMessageBox.information(self,'Phone cleanup cancelled' if report['cancelled'] else 'Phone cleanup complete',message)
        if not self.closing:self.start_scan(working_root)
    def start_phone_preview(self,device):
        if self.worker and self.worker.isRunning():return
        from .phone_import import preview_phone_download
        self.cancel.clear();destination=str(self.root)
        def job(progress):return preview_phone_download(device,progress,self.cancel)
        def ready(report):
            report.update(device=device.name,destination=destination)
            report_path=DATA/'phone-import-test-report.json'
            report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(json.dumps(report,indent=2),encoding='utf-8')
            suffix=f"\n{report['unknown_sizes']:,} images have unknown sizes." if report['unknown_sizes'] else ''
            if report['errors']:suffix+=f"\n{len(report['errors']):,} folders/items could not be read; this inventory is incomplete."
            if report['image_count']==0:suffix+='\nIf you expected pictures, unlock the phone and select File transfer, then reconnect.'
            QMessageBox.information(self,'Phone download test',f"Would download {report['image_count']:,} images ({report['known_bytes']/1024**3:.2f} GiB known size) to:\n{destination}{suffix}\n\nNothing was downloaded or deleted. Review currently includes only images already in that folder.")
        self.run_job(job,ready)
    def phone_watcher_finished(self,watcher):
        if watcher in self.phone_watchers:self.phone_watchers.remove(watcher)
        if self.phone_watcher is watcher:self.phone_watcher=None
        watcher.deleteLater()
        if self.closing:self.close()
    def add_shortcut(self,key,callback):
        action=QAction(self);action.setShortcut(QKeySequence(key));action.triggered.connect(lambda:callback() if not (self.worker and self.worker.isRunning()) else None);self.addAction(action)
    def build_menu(self):
        self.match_actions=[]
        for name,items in [('File',[('Change folder…',self.choose_folder),('Download phone pictures…',self.choose_phone_download),('Import from USB drive…',self.choose_usb_import),('Convert HEIC to JPEG copies…',self.convert_heic),('Open sample review',self.load_demo),('Resume review…',self.resume),('Save review',self.save),('Exit',self.close)]),('Review',[('Previous image',lambda:self.navigate_photo(-1)),('Next image',lambda:self.navigate_photo(1)),('Previous set',lambda:self.navigate_group(-1)),('Next set',lambda:self.navigate_group(1)),('Keep whole group',self.keep_group),('Skip group',self.skip_group),('Review changes…',self.review)]),('View',[('Fit photo',self.fit),('Actual size',self.actual)]),('Help',[('About Photo Review',self.about)])]:
            menu=self.menuBar().addMenu(name)
            for title,callback in items:
                if title=='Open sample review' and not (ROOT/'demo-assets').is_dir():continue
                action=menu.addAction(title,callback)
                if title in ('Previous set','Next set','Keep whole group','Skip group'):self.match_actions.append(action)
    def ribbon(self):
        frame=QFrame();frame.setObjectName('ribbon');row=QHBoxLayout(frame);row.setContentsMargins(20,13,20,12);row.setSpacing(20)
        brand=QVBoxLayout();brand.addWidget(label('PHOTO REVIEW','eyebrow'))
        self.compare_radio=QRadioButton('Compare');self.review_radio=QRadioButton('Review');self.mode_buttons=QButtonGroup(self)
        self.mode_buttons.addButton(self.compare_radio);self.mode_buttons.addButton(self.review_radio);self.compare_radio.setChecked(True)
        brand.addWidget(self.compare_radio);brand.addWidget(self.review_radio);row.addLayout(brand)
        self.review_radio.toggled.connect(lambda checked:self.set_mode('review' if checked else 'compare'))
        row.addWidget(self.separator())
        library=QVBoxLayout();buttons=QHBoxLayout();buttons.addWidget(button('Change folder',self.choose_folder))
        if (ROOT/'demo-assets').is_dir():buttons.addWidget(button('Sample review',self.load_demo))
        library.addLayout(buttons);library.addWidget(label('LIBRARY','small'));row.addLayout(library)
        self.matching_separator=self.separator();row.addWidget(self.matching_separator)
        self.matching_controls=QWidget();settings=QVBoxLayout(self.matching_controls);settings.setContentsMargins(0,0,0,0);sliders=QHBoxLayout();sliders.setSpacing(24)
        self.minutes=QSlider(Qt.Orientation.Horizontal);self.minutes.setRange(1,30*24*60);self.minutes.setValue(10);self.minutes.setFixedWidth(145)
        self.threshold=QSlider(Qt.Orientation.Horizontal);self.threshold.setRange(60,100);self.threshold.setValue(90);self.threshold.setFixedWidth(145)
        self.time_text=label('Within 10 minutes');self.score_text=label('Similarity ≥ 90')
        for slider,text in [(self.minutes,self.time_text),(self.threshold,self.score_text)]:
            col=QVBoxLayout();col.setSpacing(3);col.addWidget(text);col.addWidget(slider);sliders.addLayout(col)
        settings.addLayout(sliders);settings.addWidget(label('MATCHING  ·  Same folder only','small'));row.addWidget(self.matching_controls)
        self.regroup_timer=QTimer(self);self.regroup_timer.setSingleShot(True);self.regroup_timer.setInterval(450);self.regroup_timer.timeout.connect(self.regroup)
        self.minutes.valueChanged.connect(self.settings_changed);self.threshold.valueChanged.connect(self.settings_changed)
        row.addStretch();self.finish_button=button('Review changes',self.review,True);row.addWidget(self.finish_button)
        return frame
    def separator(self):
        line=QFrame();line.setFrameShape(QFrame.Shape.VLine);line.setStyleSheet('color:#e0e5ed;');return line
    def folder_sidebar(self):
        frame=QFrame();frame.setObjectName('sidebar');frame.setMinimumWidth(225);frame.setMaximumWidth(310)
        box=QVBoxLayout(frame);box.setContentsMargins(12,22,8,15);box.setSpacing(12)
        self.sidebar_splitter=QSplitter(Qt.Orientation.Vertical);self.sidebar_splitter.setChildrenCollapsible(False);box.addWidget(self.sidebar_splitter,1)
        folders=QWidget();folder_box=QVBoxLayout(folders);folder_box.setContentsMargins(0,0,0,0);folder_box.setSpacing(12)
        folder_box.addWidget(label('FOLDERS','eyebrow'));self.folder_hint=label('Browse photos and videos','muted');folder_box.addWidget(self.folder_hint)
        self.folder_tree=QTreeWidget();self.folder_tree.setHeaderHidden(True);self.folder_tree.setIndentation(15)
        self.folder_tree.setStyleSheet('QTreeWidget {background:transparent;border:none;outline:none;} QTreeWidget::item {padding:7px 2px;} QTreeWidget::item:selected {background:#e2ecfd;color:#1e56a7;border-radius:4px;}')
        self.folder_tree.itemClicked.connect(self.select_folder);folder_box.addWidget(self.folder_tree,1)
        self.sidebar_splitter.addWidget(folders);self.sidebar_splitter.addWidget(self.sidebar());self.sidebar_splitter.setSizes([230,500])
        return frame
    def visible_indices(self):
        if self.mode=='review':return [0] if self.review_group().photos else []
        key=(id(self.groups),len(self.groups),self.folder_filter)
        if getattr(self,'visible_cache_key',None)==key:return self.visible_cache
        folder=Path(self.folder_filter) if self.folder_filter else None
        self.visible_cache=[i for i,group in enumerate(self.groups) if folder is None or Path(group.photos[0].folder).is_relative_to(folder)]
        self.visible_cache_key=key
        return self.visible_cache
    def select_folder(self,item,column=0):
        if self.rebuilding:return
        path=item.data(0,Qt.ItemDataRole.UserRole)
        self.folder_filter=None if self.root and path==str(self.root) else path
        visible=self.visible_indices();self.group_index=visible[0] if visible else 0;self.photo_index=0
        self.render();self.save()
    def sync_folder_tree(self):
        items=self.mode_items()
        signature=(str(self.root),self.mode,tuple(photo.identity for photo in items))
        if signature==self.tree_signature:return
        self.tree_signature=signature;self.folder_tree.blockSignals(True);self.folder_tree.clear();self.folder_nodes={}
        try:
            if not self.root:return
            counts={};root=Path(self.root)
            for photo in items:
                folder=Path(photo.folder)
                while folder.is_relative_to(root):
                    counts[folder]=counts.get(folder,0)+1
                    if folder==root:break
                    folder=folder.parent
            self.folder_photo_counts={str(folder):count for folder,count in counts.items()}
            top=QTreeWidgetItem([f'All samples ({len(items):,})' if root.name=='samples' else f'{root.name} ({len(items):,})'])
            top.setData(0,Qt.ItemDataRole.UserRole,str(root));top.setToolTip(0,str(root));self.folder_tree.addTopLevelItem(top);self.folder_nodes[str(root)]=top;top.setExpanded(True)
            for folder,count in sorted(counts.items(),key=lambda row:row[0].parts):
                if folder==root:continue
                parent=self.folder_nodes.get(str(folder.parent))
                if parent is None:continue
                name=folder.name
                if len(name)==7 and name[4]=='-' and name.replace('-','').isdigit():
                    name=datetime.strptime(name,'%Y-%m').strftime('%m · %B')
                node=QTreeWidgetItem([f'{name} ({count:,})']);node.setData(0,Qt.ItemDataRole.UserRole,str(folder));node.setToolTip(0,str(folder));parent.addChild(node);self.folder_nodes[str(folder)]=node
                node.setExpanded(folder.name=='Pictures' or folder.name=='2026')
            selected=self.folder_nodes.get(str(self.folder_filter or root),top);self.folder_tree.setCurrentItem(selected)
            ancestor=selected.parent()
            while ancestor:ancestor.setExpanded(True);ancestor=ancestor.parent()
        finally:self.folder_tree.blockSignals(False)
    def sidebar(self):
        frame=QFrame();frame.setMinimumHeight(220)
        box=QVBoxLayout(frame);box.setContentsMargins(0,14,0,0);box.setSpacing(12)
        box.addWidget(label('YOUR REVIEW','eyebrow'));self.library_name=label('No folder selected');self.library_name.setWordWrap(True);box.addWidget(self.library_name)
        self.library_count=label('Start with a folder of photos.','muted');self.library_count.setWordWrap(True);box.addWidget(self.library_count)
        self.media_filter=QComboBox();self.media_filter.addItem('All photos and videos','all');self.media_filter.addItem('Photos only','photos');self.media_filter.addItem('Videos only','videos')
        self.media_filter.setAccessibleName('Review media type');self.media_filter.currentIndexChanged.connect(self.review_media_changed);box.addWidget(self.media_filter)
        box.addSpacing(4);self.list_heading=label('SIMILAR SETS','eyebrow');box.addWidget(self.list_heading);self.group_list=QListWidget();self.group_list.currentRowChanged.connect(self.select_group);box.addWidget(self.group_list,1)
        self.issue_button=button('Scan report',self.show_errors);box.addWidget(self.issue_button)
        note=label('Selections save automatically.\nFiles stay put until you accept.','small');note.setWordWrap(True);box.addWidget(note)
        return frame
    def workspace(self):
        host=QWidget();box=QVBoxLayout(host);box.setContentsMargins(22,20,22,15);box.setSpacing(12)
        self.start_panel=QFrame();welcome=QVBoxLayout(self.start_panel)
        welcome.addWidget(label('Where are your pictures?','title'))
        welcome.addWidget(label('Are your pictures stored in the Windows Pictures folder?'))
        location=label(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation),'muted');location.setWordWrap(True);welcome.addWidget(location)
        choices=QHBoxLayout();self.use_pictures=button('Yes',self.use_standard_pictures,True);self.browse_pictures=button('No — choose another folder',self.choose_folder)
        choices.addWidget(self.use_pictures);choices.addWidget(self.browse_pictures);choices.addStretch();welcome.addLayout(choices)
        welcome.addWidget(label('We will remember the folder you choose.','muted'))
        self.phone_detection=QCheckBox('Enable phone detection');self.phone_detection.setChecked(False)
        self.phone_detection.setToolTip('Detect connected USB phones and portable devices. Does not import or scan their photos.')
        welcome.addSpacing(10);welcome.addWidget(self.phone_detection)
        welcome.addWidget(label('You can change this later in Settings.','muted'))
        self.phone_note=label('Phone detection off','muted');self.phone_note.setWordWrap(True);welcome.addWidget(self.phone_note);box.addWidget(self.start_panel)
        head=QHBoxLayout();titles=QVBoxLayout();titles.setSpacing(4);self.heading=label('A little space for the best shots.','title');self.group_meta=label('Open the sample review to try it.','muted');titles.addWidget(self.heading);titles.addWidget(self.group_meta);head.addLayout(titles,1)
        self.compare_button=button('Compare',self.change_compare);self.compare_button.setCheckable(True);head.addWidget(self.compare_button);box.addLayout(head)
        tools=QHBoxLayout();self.group_badge=label('READY','badge');tools.addWidget(self.group_badge);tools.addStretch();tools.addWidget(button('Fit',self.fit));tools.addWidget(button('100%',self.actual));box.addLayout(tools)
        self.views=QSplitter(Qt.Orientation.Horizontal);self.reference=PhotoPanel();self.current=PhotoPanel();self.views.addWidget(self.reference);self.views.addWidget(self.current);self.reference.hide();box.addWidget(self.views,1)
        self.current.view.install_navigation(lambda:self.navigate_photo(-1),lambda:self.navigate_photo(1))
        self.current.dimensions_changed.connect(self.video_dimensions)
        self.photo_nav=QHBoxLayout();self.prev=button('← Previous set',lambda:self.navigate_group(-1));self.next=button('Next set →',lambda:self.navigate_group(1),True);self.photo_position=label('','muted');self.photo_nav.addWidget(self.prev);self.photo_nav.addStretch();self.photo_nav.addWidget(self.photo_position);self.photo_nav.addStretch();self.photo_nav.addWidget(self.next);box.addLayout(self.photo_nav)
        self.filmstrip=QScrollArea();self.filmstrip.setWidgetResizable(True);self.filmstrip.setFixedHeight(139);self.filmstrip.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);strip_row=QHBoxLayout();strip_row.setSpacing(6)
        self.thumb_left=button('‹',lambda:self.scroll_thumbnails(-1));self.thumb_right=button('›',lambda:self.scroll_thumbnails(1))
        for arrow,description in [(self.thumb_left,'Scroll thumbnails left'),(self.thumb_right,'Scroll thumbnails right')]:
            arrow.setFixedWidth(30);arrow.setFixedHeight(112);arrow.setToolTip(description);arrow.setAccessibleName(description)
        self.review_thumbnails=QListView();self.review_thumbnails.setViewMode(QListView.ViewMode.IconMode);self.review_thumbnails.setFlow(QListView.Flow.LeftToRight)
        self.review_thumbnails.setWrapping(False);self.review_thumbnails.setUniformItemSizes(True);self.review_thumbnails.setMovement(QListView.Movement.Static)
        self.review_thumbnails.setIconSize(QSize(120,82));self.review_thumbnails.setGridSize(QSize(140,128));self.review_thumbnails.setFixedHeight(139)
        self.review_thumbnails.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.review_thumbnails.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.review_thumbnails.setStyleSheet('QListView {border:none;background:#f5f7fa;} QScrollBar:horizontal {background:#e7edf5;height:10px;} QScrollBar::handle:horizontal {background:#b6c5d9;min-width:25px;border-radius:4px;} QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal {width:0;} QScrollBar::add-page:horizontal,QScrollBar::sub-page:horizontal {background:none;}')
        self.review_thumbnails.setItemDelegate(PhotoThumbnailDelegate(self.review_thumbnails))
        self.thumbnail_model=PhotoListModel(self.review_thumbnails);self.review_thumbnails.setModel(self.thumbnail_model)
        self.thumbnail_model.keep_changed.connect(self.tile_keep)
        self.review_thumbnails.selectionModel().currentChanged.connect(lambda current,previous:self.choose_photo(current.row()) if current.isValid() and not self.rebuilding else None)
        self.strips=QStackedWidget();self.strips.addWidget(self.filmstrip);self.strips.addWidget(self.review_thumbnails);self.strips.setFixedHeight(139)
        strip_row.addWidget(self.thumb_left);strip_row.addWidget(self.strips,1);strip_row.addWidget(self.thumb_right);box.addLayout(strip_row)
        self.filmstrip.horizontalScrollBar().rangeChanged.connect(self.update_thumbnail_arrows)
        self.filmstrip.horizontalScrollBar().valueChanged.connect(self.update_thumbnail_arrows)
        self.review_thumbnails.horizontalScrollBar().rangeChanged.connect(self.update_thumbnail_arrows)
        self.review_thumbnails.horizontalScrollBar().valueChanged.connect(self.update_thumbnail_arrows)
        self.group_actions=QWidget();foot=QHBoxLayout(self.group_actions);foot.setContentsMargins(0,0,0,0);foot.addWidget(button('Keep whole group',self.keep_group));foot.addWidget(button('Skip group',self.skip_group));foot.addStretch();box.addWidget(self.group_actions)
        self.pending=label('Everything starts checked to keep.','muted');box.addWidget(self.pending)
        return host
    def inspector(self):
        frame=QFrame();frame.setObjectName('inspector');frame.setMinimumWidth(225);frame.setMaximumWidth(260)
        box=QVBoxLayout(frame);box.setContentsMargins(18,24,18,20);box.setSpacing(13)
        self.inspector_title=label('CURRENT PHOTO','eyebrow');box.addWidget(self.inspector_title);self.filename=label('No photo selected');self.filename.setWordWrap(True);self.filename.setStyleSheet('font-size:16px;font-weight:600;');box.addWidget(self.filename)
        self.keep=QCheckBox('Keep this photo');self.keep.setChecked(True);self.keep.setStyleSheet('font-weight:600;padding:9px 0;');self.keep.toggled.connect(self.set_keep);box.addWidget(self.keep)
        self.keep_note=label('Checked = keep.','green');self.keep_note.setWordWrap(True);box.addWidget(self.keep_note)
        self.fields={};self.field_titles={}
        for key,title in [('captured','CAPTURED'),('time_source','TIMESTAMP SOURCE'),('dimensions','DIMENSIONS'),('size','FILE SIZE'),('similarity','MATCH TO FIRST PHOTO')]:
            box.addSpacing(4);self.field_titles[key]=label(title,'eyebrow');box.addWidget(self.field_titles[key]);value=label('—');value.setWordWrap(True);box.addWidget(value);self.fields[key]=value
        box.addStretch();self.match_note=label('Matching scores suggest similar photos. They do not choose which memories to keep.','small');self.match_note.setWordWrap(True);box.addWidget(self.match_note)
        self.shortcut_note=label('','muted');box.addWidget(self.shortcut_note)
        return frame
    def review_group(self):
        key=(id(self.photos),len(self.photos),self.folder_filter,self.review_media)
        if getattr(self,'review_cache_key',None)!=key:
            folder=Path(self.folder_filter) if self.folder_filter else None
            self.review_cache=Group(sorted((p for p in self.photos if (folder is None or Path(p.folder).is_relative_to(folder)) and (self.review_media=='all' or p.is_video==(self.review_media=='videos'))),key=lambda p:(p.timestamp,p.path)),100)
            self.review_cache_key=key
        return self.review_cache
    def review_media_changed(self):
        if self.rebuilding:return
        self.current.clear();self.review_media=self.media_filter.currentData();self.photo_index=0;self.render();self.save()
    def mode_items(self):return self.photos if self.mode=='review' else [p for p in self.photos if not p.is_video]
    def video_dimensions(self,width,height):
        photo=self.current_photo()
        if photo and photo.is_video:
            photo.width,photo.height=width,height;self.fields['dimensions'].setText(f'{width:,} × {height:,} px')
    def current_group(self):
        if self.mode=='review':
            group=self.review_group();return group if group.photos else None
        return self.groups[self.group_index] if self.groups and self.group_index in self.visible_indices() else None
    def current_photo(self):
        group=self.current_group();return group.photos[self.photo_index] if group and 0<=self.photo_index<len(group.photos) else None
    def matching_key(self):return (id(self.photos),self.minutes.value(),self.threshold.value())
    def set_mode(self,mode,persist=True):
        if mode==self.mode:return
        if self.worker and self.worker.isRunning():return
        self.regroup_timer.stop()
        photo=self.current_photo();identity=photo.identity if photo else None
        self.mode_positions[self.mode]=(self.folder_filter,self.group_index,self.photo_index)
        self.mode=mode
        if mode=='compare' and self.folder_filter and not any(Path(p.folder).is_relative_to(Path(self.folder_filter)) for p in self.mode_items()):self.folder_filter=None
        for radio,checked in ((self.compare_radio,mode=='compare'),(self.review_radio,mode=='review')):
            radio.blockSignals(True);radio.setChecked(checked);radio.blockSignals(False)
        if persist:self.write_preferences(review_mode=mode)
        saved=self.mode_positions.get(mode)
        self.group_index=0;self.photo_index=0
        if saved and saved[0]==self.folder_filter:self.group_index,self.photo_index=saved[1:]
        elif identity:
            candidates=[self.review_group()] if mode=='review' else [self.groups[i] for i in self.visible_indices()]
            for group in candidates:
                index=next((i for i,p in enumerate(group.photos) if p.identity==identity),None)
                if index is not None:
                    self.group_index=0 if mode=='review' else next(i for i,g in enumerate(self.groups) if g is group);self.photo_index=index;break
        group=self.current_group()
        if not group:
            visible=self.visible_indices();self.group_index=visible[0] if visible else 0;self.photo_index=0
        elif self.photo_index>=len(group.photos):self.photo_index=0
        self.render()
        if persist:self.save()
        if persist and mode=='compare' and self.photos and self.match_key!=self.matching_key():self.regroup()
    def settings_changed(self):
        minutes=self.minutes.value()
        duration=f'{minutes / 1440:.1f} days' if minutes >= 1440 else f'{minutes / 60:.1f} hours' if minutes >= 60 else f'{minutes} minutes'
        self.time_text.setText(f'Within {duration}');self.score_text.setText(f'Similarity ≥ {self.threshold.value()}')
        if self.photos and self.mode=='compare':self.regroup_timer.start()
    def run_job(self,job,on_result,counted=False,dialog_class=BusyDialog,accept_cancelled=False):
        if self.closing or (self.worker and self.worker.isRunning()):return
        self.cancel.clear();self.regroup_timer.stop()
        self.current.pause();self.reference.pause()
        self.progress.show();self.minutes.setEnabled(False);self.threshold.setEnabled(False);self.finish_button.setEnabled(False)
        self.busy_dialog=dialog_class(self,self.cancel);self.pages.setEnabled(False);self.menuBar().setEnabled(False);self.busy_dialog.show()
        self.worker=Worker(job,counted);self.worker.progress.connect(self.statusBar().showMessage)
        self.worker.progress.connect(self.busy_dialog.update_progress);self.worker.counts.connect(self.busy_dialog.update_counts)
        self.worker.result.connect(lambda result:on_result(result) if accept_cancelled or not self.cancel.is_set() else None);self.worker.error.connect(self.job_failed)
        self.worker.finished.connect(self.job_finished);self.worker.start()
    def job_failed(self,error):
        if self.busy_dialog:self.busy_dialog.finish()
        if not self.cancel.is_set():QMessageBox.warning(self,'Could not finish',error)
    def job_finished(self):
        if self.busy_dialog:self.busy_dialog.finish();self.busy_dialog.deleteLater();self.busy_dialog=None
        self.pages.setEnabled(True);self.menuBar().setEnabled(True)
        self.progress.hide();self.minutes.setEnabled(True);self.threshold.setEnabled(True);self.render()
        if self.after_job:
            callback=self.after_job;self.after_job=None;QTimer.singleShot(0,callback)
        else:QTimer.singleShot(0,self.offer_device_imports)
    def choose_folder(self):
        if self.worker and self.worker.isRunning():return
        folder=QFileDialog.getExistingDirectory(self,'Choose photo folder',QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation))
        if folder:self.start_scan(folder,remember=True)
    def convert_heic(self):
        if not self.root or (self.worker and self.worker.isRunning()):return
        from .heic import convert_folder
        folder=Path(self.folder_filter or self.root);root=self.root
        def job(progress,counts):return convert_folder(folder,DATA/'heic-conversions.sqlite3',progress,counts,self.cancel)
        def ready(report):
            def finish():
                message=f"JPEG copies created: {report['converted']:,}\nAlready converted: {report['existing']:,}\nIssues: {len(report['errors']):,}\n\nHEIC originals were kept. Copies are saved beside them as name.HEIC.jpg. Both formats can be reviewed separately."
                if report['errors']:message+='\n\n'+str(report['errors'][0][0])+': '+str(report['errors'][0][1])
                QMessageBox.information(self,'JPEG conversion cancelled' if report['cancelled'] else 'JPEG copies ready',message)
                if not self.closing:self.start_scan(root,selected_paths=self.selection_paths,time_range=self.time_range)
            self.after_job=finish
        self.run_job(job,ready,counted=True,dialog_class=HeicConversionProgressDialog,accept_cancelled=True)
    def startup_folder(self):
        try:
            preferences=json.loads(self.preferences.read_text(encoding='utf-8'))
            folder=preferences.get('last_folder')
        except (OSError,ValueError,TypeError):folder=None
        if folder:
            try:payload=json.loads(self.draft.read_text(encoding='utf-8'))
            except (OSError,ValueError):payload={}
            if payload.get('root')==folder:self.resume(str(self.draft))
            else:self.start_scan(folder)
            return
        self.show_welcome()
    def fit_to_screen(self,width,height,min_width,min_height):
        available=self.screen().availableGeometry()
        extra_width=max(0,self.frameGeometry().width()-self.width())
        extra_height=max(0,self.frameGeometry().height()-self.height())
        max_width=max(1,available.width()-extra_width);max_height=max(1,available.height()-extra_height)
        self.setMinimumSize(min(min_width,max_width),min(min_height,max_height))
        self.resize(min(width,max_width),min(height,max_height))
        frame=self.frameGeometry();frame.moveCenter(available.center());self.move(frame.topLeft())
    def show_welcome(self):
        self.pages.setCurrentIndex(0);self.menuBar().hide();self.statusBar().hide();self.start_panel.show()
        self.fit_to_screen(760,360,640,300)
    def show_review(self):
        entering=self.pages.currentIndex()==0
        self.pages.setCurrentIndex(1);self.menuBar().show();self.statusBar().show()
        if entering:self.fit_to_screen(1720,990,1340,800)
    def use_standard_pictures(self):
        pictures=QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
        if pictures:self.start_scan(pictures,remember=True)
    def load_demo(self):self.start_scan(ROOT/'demo-assets',selected_paths=demo_paths())
    def start_scan(self,folder,selected_paths=None,time_range=None,remember=False):
        if self.worker and self.worker.isRunning():return
        self.show_review()
        self.pending_remember=remember
        self.cancel.clear();self.pending_root=Path(folder);self.pending_selection=selected_paths;self.pending_range=time_range
        minutes,threshold=self.minutes.value(),self.threshold.value();compare=self.mode=='compare'
        def job(progress,counts):
            photos,errors=scan(folder,DATA,progress,self.cancel,selected_paths=selected_paths,time_range=time_range,count_progress=counts,include_videos=True)
            groups=[]
            if compare:
                progress('Grouping similar photos…');groups=make_groups(photos,minutes,threshold,self.cancel,backend=os.environ.get('PHOTO_REVIEW_MATCHER','auto'),progress=progress)
            return photos,errors,groups
        self.run_job(job,self.scanned,counted=True)
    def scanned(self,result):
        previous={p.identity:p.keep for p in self.photos} if self.root==self.pending_root else {}
        self.photos,self.errors,self.groups=result
        for photo in self.photos:photo.keep=previous.get(photo.identity,True)
        self.match_key=self.matching_key() if self.mode=='compare' else None;self.mode_positions.clear()
        self.start_panel.hide()
        self.root=self.pending_root;self.selection_paths=self.pending_selection;self.time_range=self.pending_range;self.folder_filter=None;self.group_index=0;self.photo_index=0
        if hasattr(self,'pending_restore'):
            payload=self.pending_restore;del self.pending_restore
            for photo in self.photos:photo.keep=bool(payload.get('decisions',{}).get(photo.identity,True))
            for group in self.groups:group.skipped=group.identity in payload.get('skipped',[])
            self.folder_filter=payload.get('folder_filter')
            visible=self.visible_indices();saved=payload.get('group_index',0)
            self.group_index=saved if saved in visible else visible[0] if visible else 0
            group=self.current_group();self.photo_index=min(max(0,payload.get('photo_index',0)),len(group.photos)-1) if group else 0
        self.render();self.save()
        if getattr(self,'pending_remember',False):
            self.write_preferences(last_folder=str(self.root));self.pending_remember=False
        self.statusBar().showMessage(f'{len(self.photos):,} photos scanned  ·  {len(self.groups)} similar groups  ·  {len(self.errors)} skipped / unreadable')
    def regroup(self):
        if self.closing or not self.photos or self.mode!='compare':return
        minutes,threshold=self.minutes.value(),self.threshold.value()
        def job(progress):progress('Regrouping cached photos…');return make_groups(self.photos,minutes,threshold,self.cancel,backend=os.environ.get('PHOTO_REVIEW_MATCHER','auto'),progress=progress)
        def ready(groups):
            self.groups=groups;self.match_key=self.matching_key();visible=self.visible_indices();self.group_index=visible[0] if visible else 0;self.photo_index=0;self.render();self.save()
        self.run_job(job,ready)
    def select_group(self,index):
        if self.rebuilding or index<0:return
        if self.mode=='review':self.choose_photo(index);return
        self.group_index=self.visible_indices()[index];self.photo_index=0;self.render();self.save()
    def navigate_group(self,delta):
        if self.mode=='review':return
        visible=self.visible_indices()
        if not visible:return
        position=visible.index(self.group_index) if self.group_index in visible else 0
        target=visible[max(0,min(len(visible)-1,position+delta))]
        if target==self.group_index:return
        self.group_index=target;self.photo_index=0;self.render();self.save()
        self.filmstrip.horizontalScrollBar().setValue(0)
    def navigate_photo(self,delta):
        group=self.current_group()
        if not group:return
        target=max(0,min(len(group.photos)-1,self.photo_index+delta))
        if target!=self.photo_index:self.choose_photo(target)
    def scroll_thumbnails(self,direction):
        scrollbar=self.active_strip().horizontalScrollBar()
        scrollbar.setValue(scrollbar.value()+direction*max(140,int(scrollbar.pageStep()*.8)))
    def active_strip(self):return self.review_thumbnails if self.mode=='review' else self.filmstrip
    def update_thumbnail_arrows(self,*args):
        scrollbar=self.active_strip().horizontalScrollBar()
        self.thumb_left.setEnabled(scrollbar.value()>scrollbar.minimum())
        self.thumb_right.setEnabled(scrollbar.value()<scrollbar.maximum())
    def choose_photo(self,index):
        group=self.current_group()
        if not group or not 0<=index<len(group.photos):return
        self.photo_index=index;self.render()
        QTimer.singleShot(0,self.reveal_current_thumbnail)
    def reveal_current_thumbnail(self):
        if self.mode=='review':
            if self.current_photo():self.review_thumbnails.scrollTo(self.thumbnail_model.index(self.photo_index))
            return
        if self.current_group() and hasattr(self,'thumb_tiles') and self.photo_index<len(self.thumb_tiles):
            self.filmstrip.ensureWidgetVisible(self.thumb_tiles[self.photo_index],12,0)
    def set_keep(self,checked):
        if self.rebuilding:return
        photo=self.current_photo()
        if photo:self.change_keep(photo,checked);self.render();self.save()
    def change_keep(self,photo,checked):
        photo.keep=checked
        for group in self.groups:
            if any(p is photo for p in group.photos):group.skipped=False
    def toggle_keep(self):
        if self.current_photo():self.keep.setChecked(not self.keep.isChecked())
    def keep_group(self):
        if self.mode=='review':return
        group=self.current_group()
        if group:
            for photo in group.photos:photo.keep=True
            group.skipped=False;self.render();self.save()
    def skip_group(self):
        if self.mode=='review':return
        group=self.current_group()
        if group:
            for photo in group.photos:photo.keep=True
            group.skipped=True;self.render();self.save();self.navigate_group(1)
    def change_compare(self):
        self.reference.setVisible(self.mode=='compare' and self.compare_button.isChecked() and bool(self.groups))
        self.views.setSizes([500,500]);self.render()
    def fit(self):self.current.view.fit();self.reference.view.fit()
    def actual(self):self.current.view.actual();self.reference.view.actual()
    def save(self):
        if self.root and self.photos:
            try:save_draft(self.draft,self.root,self.photos,self.groups,self.minutes.value(),self.threshold.value(),self.group_index,selected_paths=self.selection_paths,time_range=self.time_range,folder_filter=self.folder_filter,mode=self.mode,photo_index=self.photo_index)
            except OSError as error:self.statusBar().showMessage('Could not save review: '+str(error))
    def resume(self,path=None):
        if self.worker and self.worker.isRunning():return
        if not path:path,_=QFileDialog.getOpenFileName(self,'Resume review',str(DATA),'Photo Review files (*.json)')
        if not path:return
        try:payload=json.loads(Path(path).read_text(encoding='utf-8'))
        except Exception as error:QMessageBox.warning(self,'Could not open review',str(error));return
        self.pending_restore=payload
        self.set_mode('review' if payload.get('mode')=='review' else 'compare',persist=False)
        self.minutes.setValue(payload['minutes']);self.threshold.setValue(payload['threshold']);self.regroup_timer.stop();self.start_scan(payload['root'],selected_paths=payload.get('selected_paths'),time_range=payload.get('time_range'))
    def review(self):
        if not self.photos:return
        self.current.pause();self.reference.pause();self.save();self.review_dialog=ReviewDialog(self);self.review_dialog.open()
    def show_errors(self):
        dialog=QDialog(self);dialog.setWindowTitle('Scan report');dialog.resize(850,450);layout=QVBoxLayout(dialog)
        layout.addWidget(label(f'{len(self.errors)} skipped or unreadable entries','title'))
        table=QTableWidget(len(self.errors),2);table.setHorizontalHeaderLabels(['Path','Reason']);table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for i,(path,reason) in enumerate(self.errors):table.setItem(i,0,QTableWidgetItem(path));table.setItem(i,1,QTableWidgetItem(reason))
        layout.addWidget(table);layout.addWidget(button('Close',dialog.accept));dialog.exec()
    def about(self):QMessageBox.information(self,'Photo Review','Photo Review v'+VERSION+'\nPython + PySide6 · pHash + structural similarity\nAll photos start checked. Changes apply only after final acceptance.\nChecks GitHub once at startup for a newer Windows release.')
    def render(self):
        if self.root is None:self.show_welcome()
        else:self.show_review()
        self.rebuilding=True
        try:
            reviewing=self.mode=='review'
            videos=reviewing and self.review_media!='photos' and any(p.is_video for p in self.photos)
            self.media_filter.setVisible(reviewing);self.folder_hint.setText('Browse photos and videos' if reviewing else 'Browse photos for comparison')
            noun='files' if videos else 'photos'
            self.inspector_title.setText('CURRENT FILE' if videos else 'CURRENT PHOTO')
            for item in (self.matching_controls,self.matching_separator,self.compare_button,self.prev,self.next,self.group_actions,self.match_note,self.field_titles['similarity'],self.fields['similarity']):item.setVisible(not reviewing)
            for action in self.match_actions:action.setEnabled(not reviewing)
            self.shortcut_note.setText('← →  Previous / next '+('file' if videos else 'image')+'\n'+('' if reviewing else '↑ ↓  Previous / next set\n')+'Space  Toggle keep\nCtrl+S  Save review')
            self.strips.setCurrentIndex(1 if reviewing else 0);self.list_heading.setText('VIDEOS' if reviewing and self.review_media=='videos' else 'PICTURES & VIDEOS' if videos else 'PICTURES' if reviewing else 'SIMILAR SETS')
            self.sync_folder_tree()
            visible=self.visible_indices()
            visible_position=visible.index(self.group_index) if self.group_index in visible else 0
            folder_count=getattr(self,'folder_photo_counts',{}).get(str(self.folder_filter),0) if self.folder_filter else len(self.mode_items())
            if reviewing:folder_count=len(self.review_group().photos)
            self.library_name.setText((datetime.fromtimestamp(self.time_range[0]).strftime('%b %d')+' – '+datetime.fromtimestamp(self.time_range[1]-1).strftime('%b %d, %Y')) if self.time_range else Path(self.folder_filter).name if self.folder_filter else self.root.name if self.root else 'No folder selected')
            self.library_count.setText((media_count(folder_count,noun)+' · '+('Pictures and videos' if videos else 'All pictures') if reviewing else f'{folder_count:,} photos · {len(visible)} similar sets') if self.root else 'Start with a folder of photos.')
            if reviewing and any(p.is_video for p in self.photos):
                items=self.review_group().photos;clips=sum(p.is_video for p in items)
                self.library_count.setText(media_count(len(items)-clips)+' · '+media_count(clips,'videos'))
            self.group_list.blockSignals(True)
            if reviewing:
                group=self.review_group();key=(self.mode,id(group))
                if getattr(self,'list_cache_key',None)!=key:
                    self.group_list.clear()
                    for item_photo in group.photos:
                        item=QListWidgetItem(item_photo.name+'\n'+('Keep' if item_photo.keep else 'Recycle'));item.setToolTip(('Video · ' if item_photo.is_video else 'Photo · ')+item_photo.path);item.setSizeHint(QSize(210,65))
                        if item_photo.is_video:item.setIcon(video_icon())
                        self.group_list.addItem(item)
                    self.list_keep_states=[p.keep for p in group.photos]
                else:
                    for i,p in enumerate(group.photos):
                        if self.list_keep_states[i]!=p.keep:
                            self.group_list.item(i).setText(p.name+'\n'+('Keep' if p.keep else 'Recycle'));self.list_keep_states[i]=p.keep
                if group.photos:
                    self.photo_index=max(0,min(self.photo_index,len(group.photos)-1));photo=group.photos[self.photo_index]
                    self.group_list.item(self.photo_index).setText(photo.name+'\n'+('Keep' if photo.keep else 'Recycle'));self.group_list.setCurrentRow(self.photo_index)
                self.list_cache_key=key
            else:
                self.list_cache_key=None;self.group_list.clear()
                for position,i in enumerate(visible):
                    group=self.groups[i]
                    count=sum(not p.keep for p in group.photos);suffix='Skipped' if group.skipped else f'{count} marked' if count else 'All kept'
                    group_title = datetime.fromtimestamp(group.photos[0].timestamp).strftime('%b %d · %I:%M %p') if self.root and (Path(group.photos[0].folder)==self.root or (len(Path(group.photos[0].folder).name)==7 and Path(group.photos[0].folder).name.replace('-','').isdigit())) else Path(group.photos[0].folder).name
                    item=QListWidgetItem(f'{position+1:02}   {group_title}\n       {len(group.photos)} photos  ·  {suffix}');item.setSizeHint(QSize(210,65));self.group_list.addItem(item)
                if visible:self.group_list.setCurrentRow(visible_position)
            self.group_list.blockSignals(False)
            self.statusBar().showMessage(f'{len(self.mode_items()):,} {noun} · '+('Review pictures and videos' if videos else 'Review all pictures' if reviewing else f'{len(self.groups)} similar groups')+f' · {len(self.errors)} scan issues' if self.root else 'Ready · Add a folder or open the sample review')
            group=self.current_group();photo=self.current_photo()
            self.finish_button.setEnabled(bool(self.photos) and not (self.worker and self.worker.isRunning()))
            self.compare_button.setEnabled(bool(group));self.keep.setEnabled(bool(photo))
            self.current.view.image_back.setEnabled(bool(group) and self.photo_index>0)
            self.current.view.image_next.setEnabled(bool(group) and self.photo_index<len(group.photos)-1)
            self.prev.setEnabled(bool(group) and visible_position>0)
            self.next.setEnabled(bool(group) and visible_position<len(visible)-1)
            removed=[p for p in self.photos if not p.keep]
            self.pending.setText(f'{len(self.photos)-len(removed):,} kept  ·  {len(removed)} marked for recycling  ·  No files moved yet')
            if not group:
                self.current.clear();self.reference.clear();self.reference.hide()
                self.current.caption.setText('No photo selected');self.current.position.clear();self.filename.setText('No photo selected')
                self.keep.setChecked(True);self.keep_note.setText('All photos remain kept');self.keep_note.setObjectName('green')
                for value in self.fields.values():value.setText('—')
                self.filmstrip.setWidget(QWidget());self.thumbnail_model.set_photos([]);self.photo_position.clear();self.group_badge.setText(('NO PICTURES' if reviewing else 'NO MATCHES') if self.root else 'READY')
                self.heading.setText((('No videos in this folder.' if self.review_media=='videos' else 'No pictures in this folder.') if reviewing else 'No similar groups yet.') if self.root else 'A little space for the best shots.')
                self.group_meta.setText('Choose a pictures folder to begin.' if not self.root else 'Choose another folder to see its pictures.' if reviewing else 'Try another folder or adjust the matching sliders.')
                return
            self.heading.setText(datetime.fromtimestamp(group.photos[0].timestamp).strftime('%B %d, %Y') if self.root and (Path(group.photos[0].folder)==self.root or (len(Path(group.photos[0].folder).name)==7 and Path(group.photos[0].folder).name.replace('-','').isdigit())) else Path(group.photos[0].folder).name)
            span=group.photos[-1].timestamp-group.photos[0].timestamp
            span=f'{span:.1f}' if 0<span<1 else str(round(span))
            if reviewing:self.heading.setText(Path(self.folder_filter or self.root).name)
            self.group_meta.setText(media_count(len(group.photos),'files' if videos else 'pictures')+' · Checked means Keep; unchecked means Recycle' if reviewing else f'{len(group.photos)} similar photos  ·  {span} seconds apart  ·  Same folder')
            self.group_badge.setText('PICTURES & VIDEOS' if videos else 'ALL PICTURES' if reviewing else f'SET {visible_position+1} OF {len(visible)}  ·  MIN. MATCH {group.minimum_score:.1f}')
            self.current.load(photo,f'{self.photo_index+1} / {len(group.photos)}')
            self.reference.setVisible(not reviewing and self.compare_button.isChecked())
            if not reviewing:self.reference.load(group.photos[0],'REFERENCE')
            self.filename.setText(photo.name);self.keep.setChecked(photo.keep)
            self.keep.setText('Keep this video' if photo.is_video else 'Keep this photo')
            self.keep_note.setText(('Keeping this video' if photo.is_video else 'Keeping this photo') if photo.keep else 'Marked for recycling on Accept')
            self.keep_note.setObjectName('green' if photo.keep else 'danger');self.keep_note.style().unpolish(self.keep_note);self.keep_note.style().polish(self.keep_note)
            self.fields['captured'].setText(datetime.fromtimestamp(photo.timestamp).strftime('%b %d, %Y · %I:%M:%S %p'))
            self.fields['time_source'].setText(photo.time_source);self.fields['dimensions'].setText('Loading video…' if photo.is_video and not photo.width else f'{photo.width:,} × {photo.height:,} px');self.fields['size'].setText(format_size(photo.size))
            self.fields['similarity'].setText('—' if reviewing else 'Reference photo' if self.photo_index==0 else f'{similarity(group.photos[0],photo):.2f} / 100')
            self.photo_position.setText(f'{"File" if videos else "Picture"} {self.photo_index+1:,} of {len(group.photos):,} · ← → {"files" if videos else "images"}' if reviewing else f'Set {visible_position+1} of {len(visible)}  ·  ← → images  ·  ↑ ↓ sets')
            if reviewing:
                self.thumbnail_model.set_photos(group.photos);self.review_thumbnails.setCurrentIndex(self.thumbnail_model.index(self.photo_index));self.review_thumbnails.viewport().update();self.update_thumbnail_arrows();return
            old_scroll=self.filmstrip.horizontalScrollBar().value()
            same_strip=getattr(self,'rendered_group',None)==group.identity
            self.rendered_group=group.identity
            self.thumb_tiles=[]
            strip=QWidget();row=QHBoxLayout(strip);row.setContentsMargins(0,3,0,3);row.setSpacing(8)
            for i,item in enumerate(group.photos):
                tile=QFrame();tile.setObjectName('tile');tile.setProperty('selected',i==self.photo_index);tile.setProperty('removed',not item.keep);tile.setFixedWidth(132)
                col=QVBoxLayout(tile);col.setContentsMargins(5,5,5,5);col.setSpacing(3)
                thumb=QPushButton();thumb.setStyleSheet('border:none;padding:0;background:transparent;');thumb.setIcon(QIcon(item.thumbnail));thumb.setIconSize(QSize(120,70));thumb.setFixedHeight(70);thumb.clicked.connect(lambda checked=False,index=i:self.choose_photo(index));col.addWidget(thumb)
                check=QCheckBox('Keep');check.setChecked(item.keep);check.toggled.connect(lambda checked,index=i:self.tile_keep(index,checked));col.addWidget(check)
                row.addWidget(tile);self.thumb_tiles.append(tile)
            row.addStretch();self.filmstrip.setWidget(strip);self.update_thumbnail_arrows()
            if same_strip:
                identity=group.identity
                QTimer.singleShot(0,lambda:self.filmstrip.horizontalScrollBar().setValue(old_scroll) if self.current_group() and self.current_group().identity==identity else None)
        finally:self.rebuilding=False
    def tile_keep(self,index,checked):
        if self.rebuilding:return
        group=self.current_group()
        if not group or not 0<=index<len(group.photos):return
        self.change_keep(group.photos[index],checked);self.photo_index=index;self.render();self.save()
    def closeEvent(self,event):
        self.closing=True
        self.update_display_timer.stop()
        self.current.clear();self.reference.clear()
        if hasattr(self,'review_dialog') and self.review_dialog.preview_panel:self.review_dialog.preview_panel.clear()
        self.regroup_timer.stop()
        running=[watcher for watcher in self.phone_watchers+self.usb_watchers if watcher.isRunning()]
        for watcher in running:watcher.stop()
        if self.update_worker and self.update_worker.isRunning():
            event.ignore();self.update_worker.finished.connect(self.close)
        elif self.worker and self.worker.isRunning():
            self.cancel.set();event.ignore();self.worker.finished.connect(self.close)
        elif running:event.ignore()
        else:self.save();super().closeEvent(event)
