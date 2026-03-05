"""
call_scorer.py

Two separate detection strategies:

  TEAMS — Network spike + decay (reliable, 60x gap between idle and call)
    +2  Teams network spike detected (decay timer active)
    +1  Teams call window title detected
    Threshold: 2  →  network alone triggers

  ZOOM — Window title only (network too spiky to use)
    +2  Zoom call window title detected ("Zoom Meeting <x>", not "Zoom Meetings")
    Threshold: 2  →  window title alone triggers

  BOTH — Max score: 3, threshold: 2

Duration gate : 2 consecutive polls above threshold (~6s at 3s interval)
End grace     : 0 extra polls — decay timer (20s) handles Teams end,
                window title disappears instantly for Zoom
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Optional

from detector.process_watcher import ProcessWatcher, ProcessSignals
from detector.audio_watcher import AudioWatcher, AudioSignals

logger = logging.getLogger(__name__)

CALL_SCORE_THRESHOLD: int = 2
DURATION_GATE_POLLS: int = 2
CALL_END_GRACE_POLLS: int = 0


@dataclass
class CallState:
    call_active: bool = False
    app_name: Optional[str] = None
    all_active_apps: list[str] = field(default_factory=list)
    confidence: int = 0
    score: int = 0
    process_signals: dict = field(default_factory=dict)
    audio_signals: dict = field(default_factory=dict)
    detected_at: Optional[float] = None
    ended_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "call_active": self.call_active,
            "app_name": self.app_name,
            "score": self.score,
            "confidence": self.confidence,
            "process_signals": self.process_signals,
            "audio_signals": self.audio_signals,
            "detected_at": self.detected_at,
            "ended_at": self.ended_at,
        }


class CallScorer:

    def __init__(self):
        self.process_watcher = ProcessWatcher()
        self.audio_watcher = AudioWatcher()
        self._current_state = CallState()
        self._history: list[CallState] = []
        self._consecutive_active_polls: int = 0
        self._consecutive_inactive_polls: int = 0

    def _compute_score(self, proc: ProcessSignals, audio: AudioSignals) -> tuple[int, str | None]:
        """
        Compute total score and identify which app is on a call.
        Zoom and Teams scored independently, highest wins.
        Returns (score, app_name).
        """
        teams_score = sum([
            2 if audio.teams_network_active else 0,
            1 if proc.teams_call_window_detected else 0,
        ])

        zoom_score = sum([
            2 if proc.zoom_call_window_detected else 0,
        ])

        if teams_score >= zoom_score and teams_score >= CALL_SCORE_THRESHOLD:
            return teams_score, "Microsoft Teams"
        elif zoom_score >= CALL_SCORE_THRESHOLD:
            return zoom_score, "Zoom"
        else:
            # Return whichever is higher even if below threshold (for debug)
            return max(teams_score, zoom_score), None

    def evaluate(self) -> CallState:
        proc: ProcessSignals = self.process_watcher.scan()
        audio: AudioSignals = self.audio_watcher.scan()

        total_score, detected_app = self._compute_score(proc, audio)
        now = time.time()
        prev_active = self._current_state.call_active

        if total_score >= CALL_SCORE_THRESHOLD:
            self._consecutive_active_polls += 1
            self._consecutive_inactive_polls = 0
        else:
            self._consecutive_inactive_polls += 1
            self._consecutive_active_polls = 0

        if self._consecutive_active_polls >= DURATION_GATE_POLLS:
            confidence = min(3, total_score)
            app_name = detected_app or self._current_state.app_name or "Unknown"

            new_state = CallState(
                call_active=True,
                app_name=app_name,
                all_active_apps=[app_name],
                confidence=confidence,
                score=total_score,
                process_signals=proc.to_dict(),
                audio_signals=audio.to_dict(),
                detected_at=self._current_state.detected_at or now,
            )

            if not prev_active:
                logger.info(
                    "CallScorer: CALL DETECTED — app=%s  score=%d | "
                    "teams_net=%s  teams_win=%s  zoom_win=%s  mic=%s",
                    app_name, total_score,
                    "✓" if audio.teams_network_active else "✗",
                    "✓" if proc.teams_call_window_detected else "✗",
                    "✓" if proc.zoom_call_window_detected else "✗",
                    "✓" if audio.mic_amplitude_active else "✗",
                )
                self._history.append(new_state)

        elif prev_active and self._consecutive_inactive_polls <= CALL_END_GRACE_POLLS:
            new_state = self._current_state  # hold during grace

        else:
            if prev_active:
                logger.info("CallScorer: Call ENDED — score=%d", total_score)
            new_state = CallState(
                call_active=False,
                score=total_score,
                process_signals=proc.to_dict(),
                audio_signals=audio.to_dict(),
                ended_at=now if prev_active else self._current_state.ended_at,
            )

        self._current_state = new_state
        return new_state

    @property
    def current_state(self) -> CallState:
        return self._current_state

    @property
    def history(self) -> list[dict]:
        return [s.to_dict() for s in self._history]

    def debug_snapshot(self) -> dict:
        proc = self.process_watcher.scan()
        audio = self.audio_watcher.scan()
        total_score, detected_app = self._compute_score(proc, audio)
        return {
            "total_score": total_score,
            "detected_app": detected_app,
            "threshold": CALL_SCORE_THRESHOLD,
            "above_threshold": total_score >= CALL_SCORE_THRESHOLD,
            "consecutive_active_polls": self._consecutive_active_polls,
            "consecutive_inactive_polls": self._consecutive_inactive_polls,
            "duration_gate_required": DURATION_GATE_POLLS,
            "process_signals": proc.to_dict(),
            "audio_signals": audio.to_dict(),
            "current_state": self._current_state.to_dict(),
        }

    def cleanup(self):
        self.audio_watcher.cleanup()