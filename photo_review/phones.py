"""Read-only Windows portable-device discovery. No filesystem or phone-content access."""
from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import sys
import threading
import uuid
import re
from PySide6.QtCore import QThread,Signal

@dataclass(frozen=True)
class PortableDevice:
    identity:str
    name:str


def is_apple_phone(device):
    """Recognize renamed iPhones/iPads by Apple's USB vendor ID as well."""
    return bool(re.search(r'VID_05AC(?:[&#\\]|$)',device.identity,re.IGNORECASE)
                or re.search(r'iphone|ipad',device.name,re.IGNORECASE))


def same_physical_phone(first,second):
    if first.identity.casefold()==second.identity.casefold():return True
    # USB mode switches can change PID/interface details while preserving the
    # vendor and actual serial. Port-generated instance tails are not serials.
    def serial(device):
        match=re.fullmatch(r'USB\\VID_([0-9A-F]{4})[^\\]*\\([^\\]+)',device.identity,re.IGNORECASE)
        if match and '&' not in match[2]:return match[1].casefold(),match[2].casefold()
    left=serial(first);return left is not None and left==serial(second)

class DeviceTracker:
    def __init__(self):self.devices={}
    def update(self,devices):
        current={d.identity.casefold():d for d in devices}
        added=[d for key,d in current.items() if key not in self.devices]
        removed=[d for key,d in self.devices.items() if key not in current]
        changed=current!=self.devices
        self.devices=current
        return added,removed,changed

def is_phone_candidate(identity,service='',compatible='',name=''):
    """WPD also exposes disk volumes: exclude those without reading any drive."""
    text=' '.join((identity,service,compatible,name)).upper()
    if any(token in text for token in ('USBSTOR','STORAGE\\VOLUME')):return False
    return any(token in text for token in ('USB\\VID_','USB#VID_','MTP','PTP','IPHONE','IPAD'))

def enumerate_portable_devices():
    if sys.platform!='win32':raise RuntimeError('Phone detection currently requires Windows')
    api=ctypes.WinDLL('setupapi',use_last_error=True)
    class GUID(ctypes.Structure):_fields_=[('bytes',ctypes.c_ubyte*16)]
    class Interface(ctypes.Structure):
        _fields_=[('cbSize',wintypes.DWORD),('guid',GUID),('flags',wintypes.DWORD),('reserved',ctypes.c_void_p)]
    class Info(ctypes.Structure):
        _fields_=[('cbSize',wintypes.DWORD),('guid',GUID),('instance',wintypes.DWORD),('reserved',ctypes.c_void_p)]
    api.SetupDiGetClassDevsW.argtypes=[ctypes.POINTER(GUID),wintypes.LPCWSTR,wintypes.HWND,wintypes.DWORD]
    api.SetupDiGetClassDevsW.restype=wintypes.HANDLE
    api.SetupDiEnumDeviceInterfaces.argtypes=[wintypes.HANDLE,ctypes.c_void_p,ctypes.POINTER(GUID),wintypes.DWORD,ctypes.POINTER(Interface)]
    api.SetupDiEnumDeviceInterfaces.restype=wintypes.BOOL
    api.SetupDiGetDeviceInterfaceDetailW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Interface),ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),ctypes.POINTER(Info)]
    api.SetupDiGetDeviceInterfaceDetailW.restype=wintypes.BOOL
    api.SetupDiGetDeviceRegistryPropertyW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Info),wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)]
    api.SetupDiGetDeviceRegistryPropertyW.restype=wintypes.BOOL
    api.SetupDiGetDeviceInstanceIdW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Info),wintypes.LPWSTR,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)]
    api.SetupDiGetDeviceInstanceIdW.restype=wintypes.BOOL
    api.SetupDiDestroyDeviceInfoList.argtypes=[wintypes.HANDLE];api.SetupDiDestroyDeviceInfoList.restype=wintypes.BOOL
    guid=GUID((ctypes.c_ubyte*16).from_buffer_copy(uuid.UUID('6AC27878-A6FA-4155-BA85-F98F491D4F33').bytes_le))
    handle=api.SetupDiGetClassDevsW(ctypes.byref(guid),None,None,0x12) # PRESENT | DEVICEINTERFACE
    if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    def property(info,code):
        needed=wintypes.DWORD();kind=wintypes.DWORD()
        api.SetupDiGetDeviceRegistryPropertyW(handle,ctypes.byref(info),code,ctypes.byref(kind),None,0,ctypes.byref(needed))
        if not needed.value:return ''
        buffer=ctypes.create_string_buffer(needed.value)
        if not api.SetupDiGetDeviceRegistryPropertyW(handle,ctypes.byref(info),code,ctypes.byref(kind),buffer,len(buffer),ctypes.byref(needed)):return ''
        return buffer.raw.decode('utf-16-le',errors='replace').rstrip('\0').replace('\0',' ')
    devices=[]
    try:
        index=0
        while True:
            interface=Interface();interface.cbSize=ctypes.sizeof(Interface)
            if not api.SetupDiEnumDeviceInterfaces(handle,None,ctypes.byref(guid),index,ctypes.byref(interface)):
                error=ctypes.get_last_error()
                if error==259:break # NO_MORE_ITEMS
                raise ctypes.WinError(error)
            index+=1;needed=wintypes.DWORD()
            api.SetupDiGetDeviceInterfaceDetailW(handle,ctypes.byref(interface),None,0,ctypes.byref(needed),None)
            if not needed.value:raise ctypes.WinError(ctypes.get_last_error())
            detail=ctypes.create_string_buffer(needed.value)
            ctypes.cast(detail,ctypes.POINTER(wintypes.DWORD))[0]=8 if ctypes.sizeof(ctypes.c_void_p)==8 else 6
            info=Info();info.cbSize=ctypes.sizeof(Info)
            if not api.SetupDiGetDeviceInterfaceDetailW(handle,ctypes.byref(interface),detail,len(detail),None,ctypes.byref(info)):
                # Device may disappear during enumeration. Retry on next poll.
                continue
            identity=ctypes.create_unicode_buffer(4096)
            if not api.SetupDiGetDeviceInstanceIdW(handle,ctypes.byref(info),identity,len(identity),None):continue
            name=property(info,12) or property(info,0) or 'Portable device'
            if is_phone_candidate(identity.value,property(info,4),property(info,2),name):
                devices.append(PortableDevice(identity.value,name))
        return devices
    finally:api.SetupDiDestroyDeviceInfoList(handle)

class PhoneWatcher(QThread):
    devices_changed=Signal(object)
    device_connected=Signal(object)
    device_disconnected=Signal(object)
    detection_error=Signal(str)
    def __init__(self,parent=None,reader=None,interval=2):
        super().__init__(parent);self.reader=reader or enumerate_portable_devices
        self.interval=interval;self.stop_event=threading.Event();self.tracker=DeviceTracker()
    def stop(self):self.stop_event.set()
    def run(self):
        previous_error=None;first=True
        while not self.stop_event.is_set():
            try:
                added,removed,changed=self.tracker.update(self.reader())
                if self.stop_event.is_set():return
                if changed or first or previous_error:self.devices_changed.emit(list(self.tracker.devices.values()))
                for device in added:self.device_connected.emit(device)
                for device in removed:self.device_disconnected.emit(device)
                first=False;previous_error=None
            except Exception as error:
                if self.stop_event.is_set():return
                message=str(error)
                if message!=previous_error:self.detection_error.emit(message)
                previous_error=message
            if self.stop_event.wait(self.interval):break
