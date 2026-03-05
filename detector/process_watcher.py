"""
process_watcher.py

Window title detection for Teams and Zoom.

Teams observed window titles:
  Idle:    "Chat | <name> | Microsoft Teams"   ← has "Chat |" prefix
  Idle:    "Microsoft Teams"                   ← main app window
  On call: "<meeting name> | Microsoft Teams"  ← ends with "| Microsoft Teams", no "Chat |" prefix
  On call: "Aya Fnichel | Microsoft Teams"     ← 1:1 call, same pattern

Zoom:
  Idle:    "Zoom Workplace" / "Zoom Meetings"
  On call: "Zoom Meeting <duration/name>"
  On call: "ZPToolBarParentWnd" (floating toolbar)
"""

import logging
import psutil

logger = logging.getLogger(__name__)

# Zoom — exact titles observed from diagnose_zoom_windows.py
ZOOM_IDLE_TITLES: set[str] = {"Zoom Workplace", "Zoom Meetings"}
ZOOM_CALL_TITLES: set[str] = {"Zoom Meeting"}
ZOOM_CALL_TOOLBAR: str = "ZPToolBarParentWnd"

# Process names
TEAMS_PROCESS_NAMES = {'ms-teams.exe', 'teams.exe', 'msteams.exe'}
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

        teams_call, zoom_call = self._check_window_titles()
        signals.teams_call_window_detected = teams_call
        signals.zoom_call_window_detected = zoom_call

        return signals

    def _is_teams_call_window(self, title: str) -> bool:
        """
        Returns True if the window title is a Teams call window.

        Call windows end with "| Microsoft Teams" but do NOT start with "Chat |".
        Examples that match:
          "GroupProject(2nd) | Microsoft Teams"
          "Aya Fnichel | Microsoft Teams"
        Examples that must NOT match:
          "Chat | Aya Fnichel | Microsoft Teams"  ← idle chat window
          "Microsoft Teams"                        ← main app window, not a call
          "Calendar | Calendar | Microsoft Teams"  ← calendar tab
        """
        if not title.endswith("| Microsoft Teams"):
            return False
        if title.startswith("Chat |"):
            return False
        if title.startswith("Calendar |"):
            return False
        # Must have something before "| Microsoft Teams" that isn't just the app name
        prefix = title[: -len("| Microsoft Teams")].strip().rstrip("|").strip()
        if not prefix:
            return False
        return True

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

                    if not teams_found and self._is_teams_call_window(title):
                        teams_found = True
                        logger.debug("ProcessWatcher: Teams call window matched — %r", title)

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