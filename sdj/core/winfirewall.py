"""Windows Firewall: let linked phones reach this PC (Windows asks the owner's permission once).

Linked phones connect to this PC on TCP 47810 (sync) and UDP 47811 (finding the PC). The installer adds the rules
when it runs as admin; this module checks for them and can add them again from Settings → Phone link (for example
after an antivirus or a "Cancel" on the Windows prompt blocked the app).
"""
from __future__ import annotations

import base64
import logging
import subprocess
import sys

from .phonesync import DISCOVERY_PORT, PORT

log = logging.getLogger(__name__)
RULE = "SDJ Mobile Care phone sync"
_NO_WINDOW = 0x08000000


def supported() -> bool:
    return sys.platform.startswith("win")


def program() -> str:
    return sys.executable


def rule_present() -> bool | None:
    """True if our allow rule exists, False if not, None if it cannot be checked (not Windows / netsh failed)."""
    if not supported():
        return None
    try:
        r = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={RULE}"], capture_output=True,
                           text=True, timeout=20, creationflags=_NO_WINDOW)
        return r.returncode == 0
    except Exception:  # netsh missing or blocked by policy
        log.exception("firewall check failed")
        return None


def script(prog: str | None = None) -> str:
    """PowerShell run with admin rights: remove blocks for SDJ, then allow the program and the two ports."""
    p = (prog or program()).replace("'", "''")
    return "\n".join([
        "$ErrorActionPreference = 'SilentlyContinue'",
        f"$p = '{p}'",
        "Get-NetFirewallRule -Direction Inbound -Action Block | Where-Object { $_.DisplayName -like '*SDJ*' -or "
        "$_.DisplayName -like '*sdjmobilecare*' } | Remove-NetFirewallRule",
        "Get-NetFirewallApplicationFilter -Program $p | Get-NetFirewallRule | Where-Object { $_.Action -eq 'Block' } "
        "| Remove-NetFirewallRule",
        f"Remove-NetFirewallRule -DisplayName '{RULE}*'",
        f"New-NetFirewallRule -DisplayName '{RULE}' -Direction Inbound -Action Allow -Program $p -Profile Any | Out-Null",
        f"New-NetFirewallRule -DisplayName '{RULE} (TCP {PORT})' -Direction Inbound -Action Allow -Protocol TCP "
        f"-LocalPort {PORT} -Profile Any | Out-Null",
        f"New-NetFirewallRule -DisplayName '{RULE} (UDP {DISCOVERY_PORT})' -Direction Inbound -Action Allow "
        f"-Protocol UDP -LocalPort {DISCOVERY_PORT} -Profile Any | Out-Null",
        "exit 0",
    ])


def allow(timeout_s: int = 120) -> tuple[bool, str]:
    """Asks Windows (UAC) to allow phone sync and waits for it. Returns (ok, message for the owner)."""
    if not supported():
        return False, "This is only needed on Windows."
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    encoded = base64.b64encode(script().encode("utf-16-le")).decode("ascii")
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040                       # SEE_MASK_NOCLOSEPROCESS – so we can wait for it
    info.lpVerb = "runas"                         # Windows shows "Do you want to allow this app…?"
    info.lpFile = "powershell.exe"
    info.lpParameters = f"-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -EncodedCommand {encoded}"
    info.nShow = 0
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.GetLastError()
        if err == 1223:
            return False, "Windows permission was not given, so nothing was changed."
        return False, f"Could not ask Windows for permission (error {err})."
    ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, timeout_s * 1000)
    ctypes.windll.kernel32.CloseHandle(info.hProcess)
    if rule_present():
        return True, "Done – Windows Firewall now allows phone sync. Try linking the phone again."
    return False, ("Windows Firewall still has no rule for SDJ. If an antivirus (Quick Heal, K7, Norton…) has its own "
                   "firewall, allow SDJ Mobile Care there.")
