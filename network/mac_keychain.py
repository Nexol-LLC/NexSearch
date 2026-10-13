"""Keep the macOS encryption key in the current user's login Keychain."""
import ctypes
import os
import threading
from ctypes import c_void_p,c_char_p,c_long,c_uint32,c_int32,POINTER

_cached=None
_lock=threading.Lock()

def encryption_key():
    global _cached
    with _lock:
        if _cached is not None:return _cached
        core=ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        sec=ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
        core.CFStringCreateWithCString.argtypes=[c_void_p,c_char_p,c_uint32];core.CFStringCreateWithCString.restype=c_void_p
        core.CFDataCreate.argtypes=[c_void_p,c_void_p,c_long];core.CFDataCreate.restype=c_void_p
        core.CFDictionaryCreateMutable.argtypes=[c_void_p,c_long,c_void_p,c_void_p];core.CFDictionaryCreateMutable.restype=c_void_p
        core.CFDictionarySetValue.argtypes=[c_void_p,c_void_p,c_void_p]
        core.CFDataGetLength.argtypes=[c_void_p];core.CFDataGetLength.restype=c_long
        core.CFDataGetBytePtr.argtypes=[c_void_p];core.CFDataGetBytePtr.restype=c_void_p
        core.CFRelease.argtypes=[c_void_p]
        sec.SecItemCopyMatching.argtypes=[c_void_p,POINTER(c_void_p)];sec.SecItemCopyMatching.restype=c_int32
        sec.SecItemAdd.argtypes=[c_void_p,POINTER(c_void_p)];sec.SecItemAdd.restype=c_int32
        def constant(name):return c_void_p.in_dll(sec,name).value
        allocated=[]
        def string(value):
            result=core.CFStringCreateWithCString(None,value.encode('utf-8'),0x08000100)
            if not result:raise RuntimeError('Не удалось создать запись связки ключей')
            allocated.append(result);return result
        def dictionary():
            result=core.CFDictionaryCreateMutable(None,0,None,None);allocated.append(result)
            core.CFDictionarySetValue(result,constant('kSecClass'),constant('kSecClassGenericPassword'))
            core.CFDictionarySetValue(result,constant('kSecAttrService'),service)
            core.CFDictionarySetValue(result,constant('kSecAttrAccount'),account)
            return result
        def retrieve():
            query=dictionary();core.CFDictionarySetValue(query,constant('kSecReturnData'),c_void_p.in_dll(core,'kCFBooleanTrue').value)
            output=c_void_p();status=sec.SecItemCopyMatching(query,ctypes.byref(output))
            if status==-25300:return None
            if status:raise RuntimeError(f'macOS не разрешила доступ к связке ключей (код {status})')
            try:return ctypes.string_at(core.CFDataGetBytePtr(output),core.CFDataGetLength(output))
            finally:core.CFRelease(output)
        try:
            service=string('com.nexsearch.local-storage.v1');account=string(str(os.getuid()))
            key=retrieve()
            if key is None:
                key=os.urandom(32);buffer=ctypes.create_string_buffer(key)
                data=core.CFDataCreate(None,buffer,len(key));allocated.append(data)
                entry=dictionary();core.CFDictionarySetValue(entry,constant('kSecValueData'),data)
                core.CFDictionarySetValue(entry,constant('kSecAttrLabel'),string('NexSearch — защищённые локальные сеансы'))
                status=sec.SecItemAdd(entry,None)
                if status==-25299:key=retrieve() # Another instance created it first.
                elif status:raise RuntimeError(f'Не удалось сохранить ключ в связке macOS (код {status})')
            if not key or len(key)!=32:raise RuntimeError('Повреждён ключ NexSearch в связке macOS')
            _cached=key;return key
        finally:
            for item in reversed(allocated):core.CFRelease(item)
