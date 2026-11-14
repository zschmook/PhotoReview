import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from PySide6.QtCore import QTimer
from photo_review.ui import Window
app=QApplication(sys.argv)
app.setApplicationName('Photo Review')
app.setFont(QFont('Segoe UI',10))
window=Window()
window.show()
window.show_welcome()
sys.exit(app.exec())
