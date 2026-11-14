"""Portable-device monitoring tests; injected device records, no phone file access."""
import threading
import unittest
from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QSignalSpy,QTest
from photo_review.phones import PortableDevice,DeviceTracker,PhoneWatcher,is_phone_candidate,is_apple_phone

class TrackerTests(unittest.TestCase):
    def test_apple_scope_uses_usb_vendor_even_when_phone_is_renamed(self):
        for identity,name in [('USB\\VID_05AC&PID_12A8\\fixture','Mom'),('SWD\\WPDBUSENUM\\USB#VID_05AC&PID_12A8#fixture','Phone'),('fixture','Apple iPhone')]:
            self.assertTrue(is_apple_phone(PortableDevice(identity,name)))
        self.assertFalse(is_apple_phone(PortableDevice('USB\\VID_18D1&PID_4EE1\\fixture','Pixel 8')))
        self.assertFalse(is_apple_phone(PortableDevice('USB\\VID_05AC0&PID_0000\\fixture','Other')))
    def test_add_remove_reconnect_and_duplicate_identity(self):
        tracker=DeviceTracker();phone=PortableDevice('USB\\VID_1234','Phone')
        added,removed,changed=tracker.update([phone,phone]);self.assertEqual(added,[phone]);self.assertTrue(changed)
        self.assertEqual(tracker.update([PortableDevice('usb\\vid_1234','Phone')])[0],[])
        self.assertEqual(tracker.update([])[1][0].name,'Phone')
        self.assertEqual(tracker.update([phone])[0],[phone])
    def test_disks_are_excluded_and_mtp_ptp_supported(self):
        self.assertTrue(is_phone_candidate('SWD\\WPDBUSENUM\\_??_USB#VID_18D1#PHONE'))
        self.assertTrue(is_phone_candidate('USB\\VID_1234','WUDFWpdMtp'))
        self.assertTrue(is_phone_candidate('portable',compatible='MTP'))
        self.assertTrue(is_phone_candidate('portable',compatible='PTP'))
        self.assertFalse(is_phone_candidate('SWD\\WPDBUSENUM\\USBSTOR#DISK','',name='USB drive'))
        self.assertFalse(is_phone_candidate('STORAGE\\VOLUME#USB\\VID_1234'))
        self.assertFalse(is_phone_candidate('SWD\\VOLUME',name='Local disk'))

class WatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])
    def test_hotplug_dedup_disconnect_and_recovery(self):
        phone=PortableDevice('USB\\VID_1234','Test phone');calls=[0];holder=[]
        def reader():
            calls[0]+=1
            if calls[0] in (1,2):raise OSError('temporary enumeration error')
            if calls[0]==3:return []
            if calls[0] in (4,5):return [phone]
            if calls[0]==6:return []
            holder[0].stop();return []
        watcher=PhoneWatcher(reader=reader,interval=.005);holder.append(watcher)
        added=QSignalSpy(watcher.device_connected);removed=QSignalSpy(watcher.device_disconnected)
        changes=QSignalSpy(watcher.devices_changed);errors=QSignalSpy(watcher.detection_error)
        watcher.start()
        try:
            for _ in range(100):
                QTest.qWait(10)
                if not watcher.isRunning():break
            self.assertFalse(watcher.isRunning());self.assertEqual(added.count(),1);self.assertEqual(removed.count(),1)
            self.assertEqual(changes.count(),3);self.assertEqual(errors.count(),1)
        finally:watcher.stop();watcher.wait(2000)
    def test_stop_interrupts_poll_wait(self):
        ready=threading.Event()
        def reader():ready.set();return []
        watcher=PhoneWatcher(reader=reader,interval=60);watcher.start()
        try:
            self.assertTrue(ready.wait(1));watcher.stop();self.assertTrue(watcher.wait(1000))
        finally:watcher.stop();watcher.wait(2000)
