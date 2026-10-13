"""Windows DPAPI protects per-machine sessions and host database credentials."""
import ctypes
import json
import os
import sys
from pathlib import Path

class Blob(ctypes.Structure):
    _fields_=[('length',ctypes.c_uint32),('data',ctypes.POINTER(ctypes.c_byte))]
def _blob(value):
    buffer=ctypes.create_string_buffer(value)
    return Blob(len(value),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_byte))),buffer
def protect(value):
    if sys.platform=='darwin':
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from .mac_keychain import encryption_key
        nonce=os.urandom(12)
        return b'MACKC1'+nonce+AESGCM(encryption_key()).encrypt(nonce,value,b'NexSearch local storage v1')
    if os.name!='nt':return b'JSON1'+value
    input_blob,keep=_blob(value);output=Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(input_blob),None,None,None,None,1,ctypes.byref(output)):raise ctypes.WinError()
    try:return b'DPAPI'+ctypes.string_at(output.data,output.length)
    finally:ctypes.windll.kernel32.LocalFree(output.data)
def unprotect(value):
    if value.startswith(b'MACKC1'):
        if sys.platform!='darwin':raise ValueError('Сеанс принадлежит другому компьютеру')
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from .mac_keychain import encryption_key
        return AESGCM(encryption_key()).decrypt(value[6:18],value[18:],b'NexSearch local storage v1')
    if value.startswith(b'JSON1') and os.name!='nt':return value[5:]
    if not value.startswith(b'DPAPI') or os.name!='nt':raise ValueError('Сеанс принадлежит другому компьютеру')
    input_blob,keep=_blob(value[5:]);output=Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(input_blob),None,None,None,None,1,ctypes.byref(output)):raise ctypes.WinError()
    try:return ctypes.string_at(output.data,output.length)
    finally:ctypes.windll.kernel32.LocalFree(output.data)
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    pending=path.with_suffix('.tmp');pending.write_bytes(protect(json.dumps(value,ensure_ascii=False,default=str).encode('utf-8')))
    if os.name!='nt':pending.chmod(0o600)
    pending.replace(path)
def read(path,default=None):
    path=Path(path)
    if not path.exists():return default
    return json.loads(unprotect(path.read_bytes()).decode('utf-8'))
