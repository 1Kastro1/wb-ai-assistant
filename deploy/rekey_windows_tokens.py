"""One-time transfer of Windows DPAPI tokens to server Fernet encryption.

Read the Fernet key from stdin over SSH; never print plaintext tokens or the key.
The source SQLite database is opened read-only. The output contains encrypted
tokens only and should be sent directly to the destination VPS, then removed.
"""

import base64
import ctypes
import json
import os
import sqlite3
import sys
from pathlib import Path

from cryptography.fernet import Fernet


class Blob(ctypes.Structure):
    _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def unprotect(value: str) -> str:
    raw = base64.b64decode(value, validate=True)
    source_buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    destination = Blob()
    unprotect_data = ctypes.windll.crypt32.CryptUnprotectData
    unprotect_data.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
                               ctypes.POINTER(Blob)]
    unprotect_data.restype = ctypes.c_int
    if not unprotect_data(ctypes.byref(source), None, None, None, None, 1,
                          ctypes.byref(destination)):
        raise OSError('Windows DPAPI could not decrypt a stored token')
    try:
        return ctypes.string_at(destination.data, destination.size).decode('utf-8')
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(destination.data, ctypes.c_void_p))


def main() -> None:
    if os.name != 'nt' or len(sys.argv) != 3:
        raise SystemExit('Usage on Windows: rekey_windows_tokens.py source.db output.json')
    source = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve()
    key = sys.stdin.buffer.read().strip()
    cipher = Fernet(key)
    connection = sqlite3.connect(f'file:{source.as_posix()}?mode=ro', uri=True)
    try:
        if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite source failed integrity check')
        rows = connection.execute('SELECT id, encrypted_token FROM wb_accounts').fetchall()
    finally:
        connection.close()
    converted = []
    for account_id, protected in rows:
        plain = unprotect(protected)
        converted.append({'id': account_id, 'encrypted_token': 'fernet:' +
                          cipher.encrypt(plain.encode('utf-8')).decode('ascii')})
    handle = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(handle, 'w', encoding='utf-8') as destination:
        json.dump(converted, destination, ensure_ascii=False)
    print('Re-encrypted accounts:', ', '.join(row['id'] for row in converted))


if __name__ == '__main__':
    main()
