# Interview Detector

A lightweight background app that automatically detects when you join a video call on **Microsoft Teams** or **Zoom**, fires a Windows toast notification, and exposes a local API for debugging and future assistant integration.

Built to run entirely from a Python virtual environment — no admin rights, no system-level installation, no IT policy violations.

---

## How It Works

The detector runs a polling loop every 3 seconds. Each poll gathers signals from two watchers, combines them into a score, and decides whether a call is active.

### Detection Strategy

Each app uses a different primary signal based on real observed behavior:

#### Microsoft Teams
- **Primary signal — Network spike + decay timer**
  - Idle Teams generates ~4–10 KB/s of background traffic (chat sync, presence)
  - An active Teams call generates ~500–700 KB/s (audio/video stream) — a 60× gap
  - One reading above 500 KB/s starts a 20-second decay timer
  - The timer resets on every new spike
  - Call ends only when 20 full seconds pass with no spike
- **Bonus signal — Window title**
  - Idle: `"Chat | <Name> | Microsoft Teams"`
  - On call: `"Microsoft Teams"` (exact — call UI takes over)

#### Zoom
- **Primary signal — Window title** (network is too spiky to use reliably)
  - Idle: only `"Zoom Workplace"` visible
  - On call: `"Zoom Meeting"` window appears on PID handling the call
  - On call: `"ZPToolBarParentWnd"` (floating in-call toolbar) also appears
  - Either title triggers detection; both disappear instantly on hangup
- Network is **not used** for Zoom — observed behavior showed near-zero traffic mid-call despite active audio, making it an unreliable signal

### Scoring Table

| Signal | App | Score |
|---|---|---|
| Teams network spike (decay active) | Teams | +2 |
| Teams call window title | Teams | +1 |
| Zoom call window title / toolbar | Zoom | +2 |
| Mic amplitude sustained 10s+ | Both | +1 (bonus, requires pyaudio) |

**Trigger threshold: 2 points**  
**Duration gate: 2 consecutive polls above threshold** (~6 seconds) before notifying — eliminates transient false positives.

### False Positive Prevention

| Scenario | Why it doesn't trigger |
|---|---|
| Teams open, no call | Network ~10 KB/s — far below 500 KB/s threshold |
| Teams notification sound | Network spike lasts <3s — duration gate blocks it |
| Zoom open, no call | Only `"Zoom Workplace"` window present — no call titles |
| Browser video (YouTube etc.) | Not monitored — browser processes excluded |

---

## Architecture

```
interview-detector/
│
├── main.py                        ← Entry point. Runs detection loop + FastAPI server
│
├── detector/
│   ├── process_watcher.py         ← Window title detection for Teams and Zoom
│   ├── audio_watcher.py           ← Teams network monitoring (spike + decay)
│   ├── call_scorer.py             ← Combines signals, applies duration gate, decides call state
│   └── notifier.py                ← Windows toast notifications via winotify
│
├── api/
│   └── main.py                    ← FastAPI debug layer (status, signals, history endpoints)
│
├── diagnose_zoom.py               ← Find exact Zoom process names on a machine
├── diagnose_zoom_call.py          ← Watch Zoom network in real time during a call
├── diagnose_zoom_windows.py       ← Dump all Zoom window titles (idle vs on call)
└── requirements.txt
```

### Component Responsibilities

**`process_watcher.py`**  
Enumerates all visible windows via `win32gui.EnumWindows` on every poll. Checks for Teams and Zoom call-specific window titles. Single pass, handles both apps simultaneously. No admin required.

**`audio_watcher.py`**  
Reads `io_counters()` from all `ms-teams.exe` processes via `psutil`. Computes bytes/second delta between polls. Maintains a spike detection state with a 20-second decay timer. Optionally reads mic amplitude via `pyaudio` (bonus signal, not required).

**`call_scorer.py`**  
Orchestrates both watchers. Scores Teams and Zoom independently. Applies a 2-poll duration gate before triggering. Maintains call history for the session.

**`notifier.py`**  
Fires Windows toast notifications via `winotify`. Handles cooldown (won't re-fire during the same call), call-end notification, and console fallback if `winotify` is unavailable.

**`api/main.py`**  
FastAPI server running on `localhost:8000`. Shares the same process as the detection loop via `asyncio.gather`. Exposes raw signal data for debugging.

---

## Setup

```bash
# 1. Create virtual environment
python -m venv .venv
.venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run
python main.py
```

### Requirements

```
fastapi==0.111.0
uvicorn==0.29.0
psutil==5.9.8
pywin32==311
winotify==1.1.0
comtypes==1.4.2
pyaudio==0.2.14     # optional — enables mic amplitude signal
```

> **pyaudio** is optional. If not installed, detection still works via network (Teams) and window titles (Zoom). Install it to enable the mic amplitude bonus signal.

---

## API Endpoints

All endpoints are local only (`127.0.0.1:8000`) — never exposed externally.

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Health check + endpoint list |
| `/status` | GET | Current call state — app name, score, confidence |
| `/signals` | GET | Full raw signal breakdown for debugging |


### Example `/status` response (on a call)
```json
{
  "call_active": true,
  "app_name": "Microsoft Teams",
  "all_active_apps": ["Microsoft Teams"],
  "score": 2,
  "confidence": 2,
  "uptime_seconds": 183.4
}
```

### Example `/signals` response (useful for debugging)
```json
{
  "total_score": 2,
  "threshold": 2,
  "above_threshold": true,
  "consecutive_active_polls": 4,
  "process_signals": {
    "teams_running": true,
    "teams_call_window_detected": false,
    "zoom_running": false,
    "zoom_call_window_detected": false
  },
  "audio_signals": {
    "teams_network_active": true,
    "network_current_bps": 612400,
    "network_peak_bps": 748200,
    "network_last_spike_secs_ago": 1.4
  }
}
```

---

## Confirmed Working Output

```
14:53:50  INFO  main — Detection loop started. Polling every 3s.

14:54:23  INFO  call_scorer — CALL DETECTED — app=Zoom  score=2 | zoom_win=✓
14:54:23  INFO  notifier   — Toast sent for Zoom (confidence=2/3)
14:54:40  INFO  call_scorer — Call ENDED — score=0

14:55:05  INFO  call_scorer — CALL DETECTED — app=Microsoft Teams  score=2 | teams_net=✓
14:55:05  INFO  notifier   — Toast sent for Microsoft Teams (confidence=2/3)
14:55:34  INFO  call_scorer — Call ENDED — score=0
```

---

## Portability

| Scenario | Works? | Notes |
|---|---|---|
| Any Windows laptop, Teams + Zoom in English | ✅ | Drop-in, no changes |
| Teams only, no Zoom | ✅ | Zoom watcher runs silently, no effect |
| Zoom only, no Teams | ✅ | Teams watcher runs silently, no effect |
| Non-English Teams/Zoom | ⚠️ | Window titles may differ — re-run diagnostics |
| Older Teams (`Teams.exe`) | ✅ | Both process names covered |
| Different Zoom version | ⚠️ | Run `diagnose_zoom_windows.py` to verify titles |
| macOS / Linux | ❌ | Windows APIs only (`win32gui`, `winotify`) |
| Other call apps (Slack, Webex) | ❌ | Not implemented — framework is ready, needs diagnostics |

### Adding a New Call App

1. Run `diagnose_zoom_windows.py` logic adapted for the new app's process name
2. Identify idle vs on-call window title diff
3. Add titles to `process_watcher.py`
4. Add process name to `audio_watcher.py` if network signal is reliable
5. Add scoring branch in `call_scorer.py`

---


## Constraints & Design Decisions

- **No admin rights required** — runs entirely in user space from a venv
- **No audio content read** — mic signal reads amplitude (volume level) only, never audio data
- **No network packets inspected** — only bytes/sec counters from `psutil.io_counters()`
- **No data leaves the machine** — all processing is local, API is localhost only
- **No background service** — runs as a regular Python process, no system registration
- **Single process** — detection loop and FastAPI server share one process via `asyncio`

---

## What's Next

The detection layer is complete. The natural next step is activating the assistant layer on call detection — transcription, note-taking, and question suggestions — which can hook into the existing `call_active` state from `/status`.