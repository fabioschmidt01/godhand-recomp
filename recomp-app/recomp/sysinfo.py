"""Informações do PC para o relatório de feedback. Tudo lido localmente (registro do Windows), sem rede."""
from __future__ import annotations

import ctypes
import os
import platform
import sys


def _reg(path: str, name: str) -> str:
    if sys.platform != "win32":
        return ""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            return str(winreg.QueryValueEx(key, name)[0]).strip()
    except OSError:
        return ""


def cpu() -> str:
    return _reg(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString") or platform.processor()


def gpu() -> str:
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    names = []
    for i in range(8):
        name = _reg(rf"{base}\{i:04d}", "DriverDesc")
        if name and name not in names and "Basic" not in name:
            names.append(name)
    return " / ".join(names)


def ram_gb() -> str:
    if sys.platform != "win32":
        return ""

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return ""
    return f"{status.ullTotalPhys / (1 << 30):.0f} GB"


def windows() -> str:
    if sys.platform != "win32":
        return platform.platform()
    build = int(platform.version().split(".")[-1] or 0)
    name = "Windows 11" if build >= 22000 else "Windows 10"
    return f"{name} (build {build})"


def threads() -> int:
    return os.cpu_count() or 4
