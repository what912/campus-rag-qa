"""Windows user-bound DPAPI storage. No plaintext credential file is written."""
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path
from urllib.parse import urlparse


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(payload, decrypt=False):
    if os.name != 'nt':
        raise ValueError('本机加密保存需要 Windows；其他系统可使用进程内密钥。')
    source_buffer = ctypes.create_string_buffer(payload)
    source = Blob(len(payload), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    library = ctypes.WinDLL('crypt32', use_last_error=True)
    operation = library.CryptUnprotectData if decrypt else library.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ValueError('Windows 本机加密密钥读取或保存失败，请重新配置。')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(result.data)


def _origin(base_url):
    parsed = urlparse(base_url)
    return (parsed.scheme.lower(), (parsed.hostname or '').lower(), parsed.port)


def same_origin(first, second):
    return bool(first and second) and _origin(first) == _origin(second)


def save_key(directory, key, base_url):
    if not key or not base_url:
        raise ValueError('保存密钥前需要填写 API 地址和密钥。')
    payload = json.dumps({'api_key':key, 'origin':_origin(base_url)}, ensure_ascii=False).encode('utf-8')
    path = Path(directory) / 'credential.dpapi'
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(_crypt(payload))
    temporary.replace(path)


def load_key(directory, base_url):
    path = Path(directory) / 'credential.dpapi'
    if not path.is_file():
        return ''
    payload = json.loads(_crypt(path.read_bytes(), decrypt=True).decode('utf-8'))
    return payload['api_key'] if tuple(payload['origin']) == _origin(base_url) else ''


def clear_key(directory):
    (Path(directory) / 'credential.dpapi').unlink(missing_ok=True)
