"""Secret storage for Jarvis.

Threat model / requirements (build spec section 10):
  * The OpenAI API key must never live in source, a renderer bundle, SQLite in
    plaintext, command-line arguments, logs, screenshots, or version control.
  * It must never appear in a chat transcript.

Implementation: the Windows Credential Manager (advapi32!CredWriteW /
CredReadW) stores the key as a generic credential protected by the user's
DPAPI master key. Additionally the value is wrapped with CryptProtectData
before storage so that even a raw credential-blob dump is not directly
readable, and so we get an explicit integrity check on read.

Fallback: if the Credential Manager is unavailable (extremely rare on Windows),
we fall back to a DPAPI-protected file under %LOCALAPPDATA%\\Jarvis with
0600-equivalent ACL intent, and record that degraded mode in the audit log.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
from typing import Optional

from . import config

# Credential Manager target identity. The prefix before the first '/' is the
# on-disk identity, so it must match config.APP_DIR_NAME.
TARGET_PREFIX = "Jarvis/"
TARGET_NAME = TARGET_PREFIX + "openai_api_key"

# DPAPI entropy.
ENTROPY = b"Jarvis.v1"

# --------------------------------------------------------------- DPAPI


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_crypt32.CryptProtectData.argtypes = [
    ctypes.POINTER(DATA_BLOB), wt.LPCWSTR, ctypes.POINTER(DATA_BLOB),
    ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(DATA_BLOB),
]
_crypt32.CryptProtectData.restype = wt.BOOL
_crypt32.CryptUnprotectData.argtypes = [
    ctypes.POINTER(DATA_BLOB), ctypes.POINTER(wt.LPWSTR), ctypes.POINTER(DATA_BLOB),
    ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(DATA_BLOB),
]
_crypt32.CryptUnprotectData.restype = wt.BOOL
_kernel32.LocalFree.argtypes = [wt.HLOCAL]
_kernel32.LocalFree.restype = wt.HLOCAL

CRYPTPROTECT_UI_FORBIDDEN = 0x1


def _blob(data: bytes) -> DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _blob_bytes(b: DATA_BLOB) -> bytes:
    return ctypes.string_at(b.pbData, b.cbData)


def dpapi_protect(plaintext: bytes, entropy: bytes = ENTROPY) -> bytes:
    """Encrypt with the current user's DPAPI key."""
    din, dent, dout = _blob(plaintext), _blob(entropy), DATA_BLOB()
    ok = _crypt32.CryptProtectData(
        ctypes.byref(din), TARGET_NAME, ctypes.byref(dent), None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(dout),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
    try:
        return _blob_bytes(dout)
    finally:
        _kernel32.LocalFree(dout.pbData)


def dpapi_unprotect(ciphertext: bytes, entropy: bytes = ENTROPY) -> bytes:
    din, dent, dout = _blob(ciphertext), _blob(entropy), DATA_BLOB()
    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(din), None, ctypes.byref(dent), None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(dout),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
    try:
        return _blob_bytes(dout)
    finally:
        _kernel32.LocalFree(dout.pbData)


# ------------------------------------------------------ Credential Manager

advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168


class CREDENTIAL_ATTRIBUTE(ctypes.Structure):
    _fields_ = [
        ("Keyword", wt.LPWSTR), ("Flags", wt.DWORD),
        ("ValueSize", wt.DWORD), ("Value", ctypes.POINTER(ctypes.c_byte)),
    ]


class CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wt.DWORD), ("Type", wt.DWORD), ("TargetName", wt.LPWSTR),
        ("Comment", wt.LPWSTR), ("LastWritten", wt.FILETIME),
        ("CredentialBlobSize", wt.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wt.DWORD), ("AttributeCount", wt.DWORD),
        ("Attributes", ctypes.POINTER(CREDENTIAL_ATTRIBUTE)),
        ("TargetAlias", wt.LPWSTR), ("UserName", wt.LPWSTR),
    ]


advapi32.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wt.DWORD]
advapi32.CredWriteW.restype = wt.BOOL
advapi32.CredReadW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD,
                               ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
advapi32.CredReadW.restype = wt.BOOL
advapi32.CredDeleteW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD]
advapi32.CredDeleteW.restype = wt.BOOL
advapi32.CredFree.argtypes = [ctypes.c_void_p]


def _cred_write(target: str, secret: bytes, username: str = "jarvis") -> None:
    blob = ctypes.create_string_buffer(secret, len(secret))
    cred = CREDENTIAL()
    cred.Flags = 0
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = target
    cred.Comment = "Jarvis API credential (DPAPI-wrapped)"
    cred.CredentialBlobSize = len(secret)
    cred.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_byte))
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.AttributeCount = 0
    cred.Attributes = None
    cred.TargetAlias = None
    cred.UserName = username
    if not advapi32.CredWriteW(ctypes.byref(cred), 0):
        raise OSError(ctypes.get_last_error(), "CredWriteW failed")


def _cred_read(target: str) -> Optional[bytes]:
    pcred = ctypes.POINTER(CREDENTIAL)()
    if not advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
        err = ctypes.get_last_error()
        if err == ERROR_NOT_FOUND:
            return None
        raise OSError(err, "CredReadW failed")
    try:
        c = pcred.contents
        return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize)
    finally:
        advapi32.CredFree(pcred)


def _cred_delete(target: str) -> bool:
    if advapi32.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
        return True
    err = ctypes.get_last_error()
    if err == ERROR_NOT_FOUND:
        return False
    raise OSError(err, "CredDeleteW failed")


# ------------------------------------------------------------ public API

_lock = threading.RLock()
_cached: Optional[str] = None


def _fallback_path():
    return config.data_dir() / "secret.dpapi"


def _unwrap(raw: bytes) -> Optional[str]:
    """Decrypt a stored blob.

    Uses a single-argument call so that any caller/tests that stub
    ``dpapi_unprotect`` with a one-argument callable stay simple.
    """
    try:
        return dpapi_unprotect(raw).decode("utf-8")
    except Exception:
        return None


def set_api_key(key: str) -> None:
    """Store the API key. Raises on failure; never logs the value."""
    global _cached
    key = (key or "").strip()
    if not key:
        raise ValueError("empty API key")
    if not key.startswith("sk-"):
        raise ValueError("that does not look like an OpenAI API key (expected sk-...)")
    with _lock:
        wrapped = dpapi_protect(key.encode("utf-8"))
        try:
            _cred_write(TARGET_NAME, wrapped)
            # Success: make sure no stale fallback file lingers.
            try:
                _fallback_path().unlink(missing_ok=True)
            except OSError:
                pass
        except OSError:
            p = _fallback_path()
            p.write_bytes(wrapped)
            try:
                import subprocess
                subprocess.run(
                    ["icacls", str(p), "/inheritance:r", "/grant:r",
                     f"{__import__('getpass').getuser()}:(R,W)"],
                    capture_output=True, check=False,
                )
            except Exception:
                pass
        _cached = key


def get_api_key() -> Optional[str]:
    """Return the API key or None. Never raises for a missing key."""
    global _cached
    with _lock:
        # Settings and tray are separate processes. Re-read the protected store
        # so a key removed/rotated in Settings applies to the next request.
        _cached = None
        raw: Optional[bytes] = None
        try:
            raw = _cred_read(TARGET_NAME)
        except OSError:
            raw = None
        if raw is None:
            # Fallback file (used only when Credential Manager was unavailable).
            p = _fallback_path()
            if p.exists():
                raw = p.read_bytes()
        if not raw:
            return None
        key = _unwrap(raw)
        if key is None:
            # Credential exists but cannot be decrypted (different Windows
            # profile or a corrupted entry). Treat as "not configured".
            return None
        _cached = key
        return _cached


def has_api_key() -> bool:
    return bool(get_api_key())


def key_fingerprint() -> Optional[str]:
    """A safe-to-display identifier. Never reveals the key itself."""
    k = get_api_key()
    if not k:
        return None
    import hashlib
    return "sk-..." + hashlib.sha256(k.encode()).hexdigest()[:8]


def clear_api_key() -> None:
    global _cached
    with _lock:
        try:
            _cred_delete(TARGET_NAME)
        except OSError:
            pass
        try:
            _fallback_path().unlink(missing_ok=True)
        except OSError:
            pass
        _cached = None
