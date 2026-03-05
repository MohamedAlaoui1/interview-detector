"""
call_scorer.py

Two separate detection strategies:

  TEAMS — Network spike + decay (reliable, 60x gap between idle and call)
    +2  Teams network spike detected (rolling avg confirmed, decay timer active)
    +1  Teams call window title detected
    Threshold: 2  →  network alone triggers, window title alone does NOT

  ZOOM — Window title only (network too spiky to use)
    +2  Zoom call window title detected
    Threshold: 2  →  window title alone triggers

  BOTH — Max score: 3, threshold: 2

Duration gate  : 2 consecutive polls above threshold (~6s) before notifying
Grace period   : 10 consecutive polls below threshold (~30s) required to end call
                 BYPASSED instantly if the call window title disappears
Window hold    : if call window title is still present, score drop is ignored
Decay timer    : 120s (in audio_watcher) — RESET immediately on window title drop
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
CALL_END_GRACE_POLLS: int = 10


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
        self._prev_teams_window: bool = False
        self._prev_zoom_window: bool = False

    def _compute_score(self, proc: ProcessSignals, audio: AudioSignals) -> tuple[int, str | None]:
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
            return max(teams_score, zoom_score), None

    def _call_should_be_held(self, proc: ProcessSignals) -> bool:
        """Hold call active if the call window title is still present — covers silent pauses."""
        app = self._current_state.app_name
        if app == "Microsoft Teams" and proc.teams_call_window_detected:
            logger.debug("CallScorer: Teams call window still present — holding call state.")
            return True
        if app == "Zoom" and proc.zoom_call_window_detected:
            logger.debug("CallScorer: Zoom call window still present — holding call state.")
            return True
        return False

    def evaluate(self) -> CallState:
        proc: ProcessSignals = self.process_watcher.scan()
        audio: AudioSignals = self.audio_watcher.scan()

        total_score, detected_app = self._compute_score(proc, audio)
        now = time.time()
        prev_active = self._current_state.call_active

        # Fast-end: call window title just disappeared → end immediately,
        # don't wait for decay timer (120s) or grace period (30s).
        teams_window_just_dropped = (
            prev_active
            and self._current_state.app_name == "Microsoft Teams"
            and self._prev_teams_window
            and not proc.teams_call_window_detected
        )
        zoom_window_just_dropped = (
            prev_active
            and self._current_state.app_name == "Zoom"
            and self._prev_zoom_window
            and not proc.zoom_call_window_detected
        )

        if teams_window_just_dropped or zoom_window_just_dropped:
            logger.info("CallScorer: call window disappeared — fast-ending, resetting decay timer.")
            self.audio_watcher.reset_decay_timer()
            self._consecutive_inactive_polls = CALL_END_GRACE_POLLS + 1
            self._consecutive_active_polls = 0

        self._prev_teams_window = proc.teams_call_window_detected
        self._prev_zoom_window = proc.zoom_call_window_detected

        above_threshold = total_score >= CALL_SCORE_THRESHOLD
        held_by_window = prev_active and self._call_should_be_held(proc)

        if above_threshold or held_by_window:
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
            new_state = CallState(
                call_active=True,
                app_name=self._current_state.app_name,
                all_active_apps=self._current_state.all_active_apps,
                confidence=self._current_state.confidence,
                score=total_score,
                process_signals=proc.to_dict(),
                audio_signals=audio.to_dict(),
                detected_at=self._current_state.detected_at,
            )
            logger.debug(
                "CallScorer: score dropped, grace period (%d/%d polls) — holding.",
                self._consecutive_inactive_polls, CALL_END_GRACE_POLLS
            )

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
            "grace_period_polls": CALL_END_GRACE_POLLS,
            "duration_gate_required": DURATION_GATE_POLLS,
            "process_signals": proc.to_dict(),
            "audio_signals": audio.to_dict(),
            "current_state": self._current_state.to_dict(),
        }

    def cleanup(self):
        self.audio_watcher.cleanup()