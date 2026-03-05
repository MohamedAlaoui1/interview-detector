

"""
api/main.py — FastAPI debug layer

Endpoints:
  GET  /          → health check + endpoint list
  GET  /status    → current call state (clean)
  GET  /signals   → full raw signal breakdown (debug)
  GET  /history   → calls detected this session
  POST /test-notify → fire a test toast
"""

import time
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Interview Detector", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_scorer = None
_notifier = None
_started_at: float = time.time()


def init(scorer, notifier):
    global _scorer, _notifier
    _scorer = scorer
    _notifier = notifier


@app.get("/")
def root():
    return {
        "status": "running",
        "endpoints": {
            "status":       "GET  /status       — current call state",
            "signals":      "GET  /signals      — full raw signal debug view",
            "history":      "GET  /history      — calls detected this session",
            "test_notify":  "POST /test-notify  — fire a test toast",
            "docs":         "GET  /docs         — Swagger UI",
        }
    }


@app.get("/status")
def get_status():
    if not _scorer:
        return JSONResponse({"error": "Not initialised"}, status_code=503)
    state = _scorer.current_state
    return {
        "call_active": state.call_active,
        "app_name": state.app_name,
        "all_active_apps": state.all_active_apps,
        "score": state.score,
        "confidence": state.confidence,
        "uptime_seconds": round(time.time() - _started_at, 1),
    }


@app.get("/signals")
def get_signals():
    """
    Full real-time signal breakdown.
    Open this in your browser while idle and while on a call to compare.
    Key fields to watch:
      - total_score vs threshold (need >= 3 to trigger)
      - consecutive_active_polls (need >= 2 for duration gate)
      - process_signals.teams_process_count (idle=1, call=2)
      - process_signals.teams_call_window_detected
      - audio_signals.mic_avg_amplitude + mic_sustained_secs
      - audio_signals.network_rolling_avg_bps
    """
    if not _scorer:
        return JSONResponse({"error": "Not initialised"}, status_code=503)
    return _scorer.debug_snapshot()


@app.get("/history")
def get_history():
    if not _scorer:
        return JSONResponse({"error": "Not initialised"}, status_code=503)
    return {
        "total_calls_detected": len(_scorer.history),
        "calls": _scorer.history,
    }


@app.post("/test-notify")
def test_notify():
    if not _notifier:
        return JSONResponse({"error": "Not initialised"}, status_code=503)
    sent = _notifier.test_notify()
    return {"sent": sent}