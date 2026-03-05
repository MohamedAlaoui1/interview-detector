"""
process_watcher.py

Window title detection for Teams and Zoom.

Teams:
  Idle:    "Chat | <name> | Microsoft Teams"
  On call: "Microsoft Teams"  (exact)

Zoom:
  Idle:    "Zoom Meetings"  (exact)
  On call: "Zoom Meeting <duration/name>"  (starts with "zoom meeting " + extra)

Score: +1 if any call window title detected (bonus alongside network signal)
"""

import logging
import psutil

logger = logging.getLogger(__name__)

# Teams
TEAMS_CALL_TITLE_EXACT: str = "microsoft teams"
TEAMS_IDLE_TITLE_MARKERS: list[str] = ["chat |", "| microsoft teams"]

# Zoom — exact titles observed from diagnose_zoom_windows.py
ZOOM_IDLE_TITLES: set[str] = {"Zoom Workplace", "Zoom Meetings"}
ZOOM_CALL_TITLES: set[str] = {"Zoom Meeting"}        # exact — appears only during a call
ZOOM_CALL_TOOLBAR: str = "ZPToolBarParentWnd"        # floating toolbar, only exists on a call

# Process names
TEAMS_PROCESS_NAMES = {'ms-teams.exe', 'teams.exe', 'msteams.exe'}
# psutil returns exact Windows name — Zoom uses capital Z
ZOOM_PROCESS_NAMES  = {'Zoom.exe'}



class ProcessSignals:
    def __init__(self):
        self.teams_is_running: bool = False
        self.teams_process_count: int = 0
        self.teams_call_window_detected: bool = False
        self.zoom_is_running: bool = False
        self.zoom_process_count: int = 0
        self.zoom_call_window_detected: bool = False

    @property
    def score(self) -> int:
        return sum([
            1 if self.teams_call_window_detected else 0,
            1 if self.zoom_call_window_detected else 0,
        ])

    @property
    def active_app(self) -> str | None:
        """Which app has a call window active right now."""
        if self.teams_call_window_detected:
            return "Microsoft Teams"
        if self.zoom_call_window_detected:
            return "Zoom"
        return None

    def to_dict(self) -> dict:
        return {
            "teams_running": self.teams_is_running,
            "teams_process_count": self.teams_process_count,
            "teams_call_window_detected": self.teams_call_window_detected,
            "zoom_running": self.zoom_is_running,
            "zoom_process_count": self.zoom_process_count,
            "zoom_call_window_detected": self.zoom_call_window_detected,
            "process_score": self.score,
            "active_app": self.active_app,
        }


class ProcessWatcher:

    def scan(self) -> ProcessSignals:
        signals = ProcessSignals()

        teams_count = 0
        zoom_count = 0

        for proc in psutil.process_iter(['name']):
            try:
                name = proc.info['name'] or ''
                name_lower = name.lower()
                if name_lower in {n.lower() for n in TEAMS_PROCESS_NAMES}:
                    teams_count += 1
                elif name in ZOOM_PROCESS_NAMES or name_lower in {n.lower() for n in ZOOM_PROCESS_NAMES}:
                    zoom_count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        signals.teams_process_count = teams_count
        signals.teams_is_running = teams_count >= 1
        signals.zoom_process_count = zoom_count
        signals.zoom_is_running = zoom_count >= 1

        # Window title scan — single pass for both apps
        teams_call, zoom_call = self._check_window_titles()
        signals.teams_call_window_detected = teams_call
        signals.zoom_call_window_detected = zoom_call

        return signals

    def _check_window_titles(self) -> tuple[bool, bool]:
        """
        Single EnumWindows pass — checks for both Teams and Zoom call windows.
        Returns (teams_call_found, zoom_call_found).
        """
        teams_found = False
        zoom_found = False

        try:
            import win32gui

            def _cb(hwnd, _):
                nonlocal teams_found, zoom_found
                try:
                    if not win32gui.IsWindowVisible(hwnd):
                        return
                    title = win32gui.GetWindowText(hwnd).strip()
                    if not title:
                        return
                    t_lower = title.lower()

                    # Teams: exactly "microsoft teams" (case-insensitive), no "chat |" prefix
                    if (not teams_found
                            and t_lower == TEAMS_CALL_TITLE_EXACT
                            and not any(m in t_lower for m in TEAMS_IDLE_TITLE_MARKERS)):
                        teams_found = True

                    # Zoom: "Zoom Meeting" exact OR toolbar — case-sensitive, from diagnosis
                    if not zoom_found:
                        if title in ZOOM_CALL_TITLES or title == ZOOM_CALL_TOOLBAR:
                            zoom_found = True

                except Exception:
                    pass

            win32gui.EnumWindows(_cb, None)

        except ImportError:
            logger.warning("ProcessWatcher: win32gui not available, window title check skipped.")
        except Exception as e:
            logger.debug(f"ProcessWatcher: window scan error: {e}")

        return teams_found, zoom_found