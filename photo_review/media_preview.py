"""Local video playback inside the existing photo preview scene."""
from PySide6.QtCore import Qt,QUrl,QSizeF,Signal
from PySide6.QtWidgets import QWidget,QHBoxLayout,QPushButton,QSlider,QLabel,QStyle
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem


def clock_text(milliseconds):
    seconds=max(0,int(milliseconds)//1000)
    return f'{seconds//3600}:{seconds//60%60:02}:{seconds%60:02}' if seconds>=3600 else f'{seconds//60}:{seconds%60:02}'


class VideoControls(QWidget):
    dimensions_changed=Signal(int,int)
    def __init__(self,view,parent=None):
        super().__init__(parent);self.view=view;self.video=None;self.duration=0
        self.player=QMediaPlayer(self);self.audio=QAudioOutput(self);self.audio.setVolume(.5);self.player.setAudioOutput(self.audio)
        row=QHBoxLayout(self);row.setContentsMargins(12,6,12,6);row.setSpacing(10)
        self.play=QPushButton('Play');self.play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay));self.play.setMinimumWidth(84);self.play.clicked.connect(self.toggle);row.addWidget(self.play)
        self.seek=QSlider(Qt.Orientation.Horizontal);self.seek.setRange(0,0);self.seek.setAccessibleName('Video position');row.addWidget(self.seek,1)
        self.time=QLabel('0:00 / 0:00');row.addWidget(self.time)
        self.mute=QPushButton('Mute');self.mute.setCheckable(True);self.mute.toggled.connect(self.audio.setMuted);row.addWidget(self.mute)
        self.message=QLabel();self.message.setWordWrap(True);row.addWidget(self.message)
        for widget in (self.play,self.seek,self.mute):widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.seek.sliderPressed.connect(self.player.pause)
        self.player.positionChanged.connect(self.position_changed);self.player.durationChanged.connect(self.duration_changed)
        self.player.playbackStateChanged.connect(self.state_changed);self.player.errorOccurred.connect(self.failed)
        self.player.mediaStatusChanged.connect(self.status_changed)
    def load(self,path):
        self.release();self.message.setText('Loading video…');self.play.setEnabled(False);self.seek.setEnabled(False)
        self.video=QGraphicsVideoItem();self.video.setSize(QSizeF(640,360));self.view.scene.addItem(self.video)
        self.view.scene.setSceneRect(self.video.boundingRect());self.video.nativeSizeChanged.connect(self.native_size_changed)
        self.player.setVideoOutput(self.video);self.player.setSource(QUrl.fromLocalFile(path));self.view.fit()
    def native_size_changed(self,size):
        if not self.video or size.isEmpty():return
        self.video.setSize(size);self.view.scene.setSceneRect(self.video.boundingRect());self.view.fit()
        self.dimensions_changed.emit(round(size.width()),round(size.height()))
    def duration_changed(self,value):
        self.duration=value;self.seek.setRange(0,value);self.seek.setEnabled(value>0);self.position_changed(self.player.position())
    def position_changed(self,value):
        if not self.seek.isSliderDown():self.seek.setValue(value)
        self.time.setText(clock_text(value)+' / '+clock_text(self.duration))
    def status_changed(self,status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia,QMediaPlayer.MediaStatus.BufferedMedia):
            self.message.clear();self.play.setEnabled(True)
    def state_changed(self,state):
        playing=state==QMediaPlayer.PlaybackState.PlayingState
        self.play.setText('Pause' if playing else 'Play');self.play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause if playing else QStyle.StandardPixmap.SP_MediaPlay))
    def toggle(self):
        if self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState:self.player.pause()
        else:
            if self.player.mediaStatus()==QMediaPlayer.MediaStatus.EndOfMedia:self.player.setPosition(0)
            self.player.play()
    def failed(self,*args):
        self.message.setText('Unable to play this format. You can still Keep or Recycle it.')
        self.message.setToolTip(self.player.errorString());self.play.setEnabled(False);self.seek.setEnabled(False)
    def pause(self):self.player.pause()
    def release(self):
        # Release the decoder's file handle before the scene destroys its output,
        # or before Windows is asked to move this file into the Recycle Bin.
        self.player.stop();self.player.setSource(QUrl());self.player.setVideoOutput(None)
        self.video=None;self.duration=0;self.seek.setRange(0,0);self.time.setText('0:00 / 0:00');self.message.clear()
    def hideEvent(self,event):self.pause();super().hideEvent(event)
