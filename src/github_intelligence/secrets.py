"""Current-user Windows DPAPI storage; never log private key material."""

import ctypes
import os
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path

_MARKER = b"GHI-DPAPI-v1\0"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class SecretError(Exception):
    """Sanitized local secret storage failure."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data: bytes, *, protect: bool) -> bytes:
    if sys.platform != "win32":
        raise SecretError("DPAPI private keys require Windows.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    blob_pointer = ctypes.POINTER(_DataBlob)
    # A NULL description output avoids a second allocation on decryption.
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    function.argtypes = [
        blob_pointer,
        wintypes.LPCWSTR if protect else ctypes.POINTER(wintypes.LPWSTR),
        blob_pointer, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, blob_pointer,
    ]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = _DataBlob()
    try:
        if not function(
            ctypes.byref(source), None, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result),
        ):
            raise SecretError("DPAPI private key operation failed.")
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        if result.pbData:
            ctypes.memset(result.pbData, 0, result.cbData)
            kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))
        ctypes.memset(buffer, 0, ctypes.sizeof(buffer))


def load_private_key(path: Path) -> bytes:
    """Load legacy PEM/test bytes or a marked DPAPI blob."""
    try:
        data = path.read_bytes()
        if path.suffix.lower() == ".dpapi" or data.startswith(_MARKER):
            if not data.startswith(_MARKER):
                raise SecretError("Invalid DPAPI private key file.")
            data = _crypt(data[len(_MARKER):], protect=False)
        if not data:
            raise SecretError("Private key is empty.")
        return data
    except (OSError, ValueError, OverflowError):
        raise SecretError("Private key is inaccessible or invalid.") from None


def protect_private_key(pem: bytes, destination: Path) -> None:
    """Encrypt bytes and atomically publish a new file, never overwrite."""
    if not isinstance(pem, bytes) or not pem:
        raise SecretError("Private key must be nonempty bytes.")
    temporary: str | None = None
    try:
        encrypted = _MARKER + _crypt(pem, protect=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
            temporary = stream.name
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        # On Windows rename fails if the destination exists, unlike replace.
        os.rename(temporary, destination)
        temporary = None
    except (OSError, ValueError, OverflowError):
        raise SecretError("Cannot securely create private key file.") from None
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                raise SecretError("Cannot clean up encrypted private key temporary file.") from None
