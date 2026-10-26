"""User-local connection preferences; Windows payloads use current-user DPAPI."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

from geoask_runtime import atomic_output

_WINDOWS = sys.platform == "win32"
_DPAPI = b"GEOASK-DPAPI-1\n"
_JSON = b"GEOASK-JSON-1\n"


class LocalSettingsError(Exception):
    """Safe user-facing error; never include credentials or file contents."""


def connection_file() -> Path:
    if _WINDOWS:
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        directory = Path(base) if base else Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        directory = Path.home() / "Library" / "Application Support"
    else:
        directory = Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config")
    return directory / "geoask" / "connection.dat"


def _crypt(data: bytes, *, decrypt=False) -> bytes:
    """DPAPI without a third-party dependency; no machine-wide encryption flag."""
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN: errors return normally, never prompt from a worker.
    if not operation(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise LocalSettingsError("无法处理本地接口设置，请使用保存它的 Windows 用户登录，或清除本地设置后重新保存。")
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel.LocalFree(ctypes.cast(outgoing.data, ctypes.c_void_p))


def _identity(llm):
    parsed = urlsplit(llm["base_url"])
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("invalid endpoint")
    return (parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path.rstrip("/"),
            parsed.query, llm["api_key_env"])


def _validate(payload):
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("invalid schema")
    for name in ("llm", "map", "routing"):
        if not isinstance(payload.get(name), dict):
            raise ValueError("invalid profile")
    if not isinstance(payload.get("api_key"), str):
        raise ValueError("invalid key")
    llm = payload["llm"]
    if any(not isinstance(llm.get(k), str) or not llm[k].strip() for k in
           ("base_url", "model", "api_key_env", "response_format")):
        raise ValueError("invalid model settings")
    _identity(llm)
    amap = payload.get("amap", {})
    if amap is None:
        amap = {}
        payload["amap"] = amap
    if not isinstance(amap, dict):
        raise ValueError("invalid amap profile")
    for key in ("key", "security_code"):
        if key in amap and not isinstance(amap[key], str):
            raise ValueError("invalid amap credential")
    return payload


def same_connection(first: dict, second: dict) -> bool:
    """Whether a credential may be reused for these two configured services."""
    return _identity(first) == _identity(second)


def load_connection():
    """None means no saved connection; corrupt/unreadable data is never ignored."""
    path = connection_file()
    try:
        with path.open("rb") as stream:
            data = stream.read(131073)
        if len(data) > 131072:
            raise ValueError("oversized profile")
        if data.startswith(_DPAPI) and _WINDOWS:
            raw = _crypt(data[len(_DPAPI):], decrypt=True)
        elif data.startswith(_JSON) and not _WINDOWS:
            raw = data[len(_JSON):]
        else:
            raise ValueError("unsupported storage")
        return _validate(json.loads(raw.decode("utf-8")))
    except FileNotFoundError:
        return None
    except LocalSettingsError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as from_exc:
        raise LocalSettingsError("无法读取本地接口设置；请在“接口设置”中清除本地保存后重新填写。") from from_exc


def save_connection(llm: dict, maps: dict, routing: dict, api_key: str,
                    amap_key: str | None = None,
                    amap_security_code: str | None = None):
    try:
        # Keep credentials that were saved by an earlier version when callers
        # only update the LLM/map form. Passing an empty string explicitly
        # clears the corresponding Amap value.
        existing = None
        if amap_key is None or amap_security_code is None:
            try:
                existing = load_connection()
            except LocalSettingsError:
                existing = None
        old_amap = dict((existing or {}).get("amap") or {})
        amap = {
            "key": old_amap.get("key", "") if amap_key is None else amap_key.strip(),
            "security_code": old_amap.get("security_code", "") if amap_security_code is None else amap_security_code.strip(),
        }
        payload = _validate(dict(version=1, llm=llm, map=maps, routing=routing,
                                 api_key=api_key.strip(), amap=amap))
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        data = _DPAPI + _crypt(raw) if _WINDOWS else _JSON + raw
        path = connection_file()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not _WINDOWS and path.exists():
            path.chmod(0o600)
        with atomic_output(path, mode="wb") as output:
            if not _WINDOWS:
                os.chmod(output.name, 0o600)
            output.write(data)
        if not _WINDOWS:
            path.chmod(0o600)
    except LocalSettingsError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise LocalSettingsError("无法保存接口设置，请检查当前用户配置目录的写入权限。") from exc


def clear_connection():
    try:
        connection_file().unlink(missing_ok=True)
    except OSError as exc:
        raise LocalSettingsError("无法清除本地接口设置，请检查文件权限或关闭占用该文件的程序。") from exc


def saved_api_key(llm: dict) -> str:
    profile = load_connection()
    # A key is never reused for a different provider, path or environment name.
    return profile["api_key"] if profile and same_connection(profile["llm"], llm) else ""


def saved_amap_credentials() -> tuple[str, str]:
    """Return the locally saved Amap Web key and security code, if present."""
    profile = load_connection()
    if not profile:
        return "", ""
    amap = profile.get("amap") or {}
    return str(amap.get("key") or ""), str(amap.get("security_code") or "")
