"""
diagnose_zoom_windows.py

Dumps all visible windows belonging to Zoom processes.
Run this DURING a call and OUTSIDE a call to compare titles.

Usage:
    python diagnose_zoom_windows.py
"""

import psutil
import win32gui
import win32process

ZOOM_NAMES = {'Zoom.exe'}

def get_zoom_pids():
    pids = set()
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            if proc.info['name'] in ZOOM_NAMES:
                pids.add(proc.info['pid'])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return pids

def dump_windows():
    zoom_pids = get_zoom_pids()
    print(f"Zoom PIDs found: {zoom_pids}\n")
    print(f"{'PID':<8} {'HWND':<12} {'Title'}")
    print("-" * 70)

    results = []

    def _cb(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in zoom_pids:
                results.append((pid, hwnd, title or '<no title>'))
        except Exception:
            pass

    win32gui.EnumWindows(_cb, None)

    for pid, hwnd, title in sorted(results):
        print(f"{pid:<8} {hwnd:<12} {title}")

    if not results:
        print("No visible Zoom windows found.")
    print()

dump_windows()