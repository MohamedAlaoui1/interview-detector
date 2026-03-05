"""
audio_watcher.py

Network monitoring for Teams ONLY.
Zoom detection is handled entirely by window title in process_watcher.py
because Zoom's network is too spiky (drops to ~0 KB/s mid-call).

Teams observed values:
  Idle:   4,000 -  10,000 bytes/sec
  Call:  500,000 - 700,000 bytes/sec  (60x difference, sustained)

Strategy: spike detection + decay timer + rolling average gate
  Rolling average (last 4 polls ~12s) must ALSO exceed 200 KB/s
  This prevents a single brief spike (download, sync) from triggering
  One reading above 500KB/s = starts 20s decay timer
  Timer resets on each new spike
  Call ends only when 20s pass with no spike AND rolling avg drops
"""

import logging
import time
import collections
import psutil

logger = logging.getLogger(__name__)

NETWORK_SPIKE_THRESHOLD: float = 500_000       # 500 KB/s — single poll trigger
NETWORK_ROLLING_THRESHOLD: float = 200_000     # 200 KB/s — rolling avg must also exceed this
NETWORK_DECAY_SECS: float = 120.0              # 2 minutes — covers silent pauses in calls
NETWORK_ROLLING_POLLS: int = 4                 # ~12 seconds of history

AMPLITUDE_THRESHOLD: float = 200.0
AMPLITUDE_SUSTAINED_SECS: float = 10.0
AMPLITUDE_SAMPLE_RATE: int = 16000
AMPLITUDE_CHUNK: int = 1024

TEAMS_PROCESS_NAMES = {'ms-teams.exe', 'teams.exe', 'msteams.exe'}


class AudioSignals:
    def __init__(self):
        self.teams_network_active: bool = False
        self.network_current_bps: float = 0.0
        self.network_rolling_avg_bps: float = 0.0
        self.network_peak_bps: float = 0.0
        self.network_last_spike_secs_ago: float | None = None
        self.mic_amplitude_active: bool = False
        self.mic_sustained_secs: float = 0.0
        self.mic_avg_amplitude: float = 0.0
        self.pyaudio_available: bool = False

    @property
    def score(self) -> int:
        return sum([
            2 if self.teams_network_active else 0,
            1 if self.mic_amplitude_active else 0,
        ])

    def to_dict(self) -> dict:
        return {
            "teams_network_active": self.teams_network_active,
            "network_current_bps": round(self.network_current_bps, 0),
            "network_rolling_avg_bps": round(self.network_rolling_avg_bps, 0),
            "network_peak_bps": round(self.network_peak_bps, 0),
            "network_spike_threshold": NETWORK_SPIKE_THRESHOLD,
            "network_rolling_threshold": NETWORK_ROLLING_THRESHOLD,
            "network_decay_secs": NETWORK_DECAY_SECS,
            "network_last_spike_secs_ago": round(self.network_last_spike_secs_ago, 1) if self.network_last_spike_secs_ago is not None else None,
            "mic_amplitude_active": self.mic_amplitude_active,
            "mic_sustained_secs": round(self.mic_sustained_secs, 1),
            "mic_avg_amplitude": round(self.mic_avg_amplitude, 1),
            "pyaudio_available": self.pyaudio_available,
            "audio_score": self.score,
        }


class AudioWatcher:

    def __init__(self):
        self._pyaudio_available = False
        self._pa = None
        self._stream = None
        self._mic_active_since: float | None = None

        self._last_spike_time: float | None = None
        self._peak_bps_seen: float = 0.0
        self._net_history: collections.deque = collections.deque(maxlen=NETWORK_ROLLING_POLLS)
        self._prev_net_bytes: dict[int, tuple[float, float]] = {}

        self._try_init_pyaudio()

    def _try_init_pyaudio(self):
        try:
            import pyaudio
            self._pa = pyaudio.PyAudio()
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=AMPLITUDE_SAMPLE_RATE,
                input=True,
                frames_per_buffer=AMPLITUDE_CHUNK,
                start=False,
            )
            self._stream.start_stream()
            self._pyaudio_available = True
            logger.info("AudioWatcher: pyaudio mic stream opened.")
        except ImportError:
            logger.warning("AudioWatcher: pyaudio not installed — Teams network signal only.")
        except Exception as e:
            logger.warning(f"AudioWatcher: mic stream unavailable ({e}).")

    def scan(self) -> AudioSignals:
        signals = AudioSignals()
        signals.pyaudio_available = self._pyaudio_available

        # --- Teams network spike + decay + rolling average gate ---
        teams_bps = self._read_teams_network()
        now_mono = time.monotonic()

        self._net_history.append(teams_bps)
        signals.network_current_bps = teams_bps
        rolling_avg = sum(self._net_history) / len(self._net_history)
        signals.network_rolling_avg_bps = rolling_avg

        if teams_bps > self._peak_bps_seen:
            self._peak_bps_seen = teams_bps
        signals.network_peak_bps = self._peak_bps_seen

        # Only start/reset the decay timer if BOTH the spike AND the rolling
        # average are elevated. This filters out brief download spikes.
        if teams_bps >= NETWORK_SPIKE_THRESHOLD and rolling_avg >= NETWORK_ROLLING_THRESHOLD:
            self._last_spike_time = now_mono
            logger.debug(
                "AudioWatcher: network spike confirmed — current=%.0f bps, avg=%.0f bps",
                teams_bps, rolling_avg
            )
        elif teams_bps >= NETWORK_SPIKE_THRESHOLD:
            # Spike seen but rolling avg not yet elevated — log for visibility
            logger.debug(
                "AudioWatcher: spike seen (%.0f bps) but rolling avg too low (%.0f bps) — not confirming yet",
                teams_bps, rolling_avg
            )

        if self._last_spike_time is not None:
            secs_since = now_mono - self._last_spike_time
            signals.network_last_spike_secs_ago = secs_since
            signals.teams_network_active = secs_since <= NETWORK_DECAY_SECS
        else:
            signals.teams_network_active = False

        # --- Mic amplitude (bonus, requires pyaudio) ---
        if self._pyaudio_available and self._stream:
            amp = self._read_amplitude()
            signals.mic_avg_amplitude = amp
            if amp > AMPLITUDE_THRESHOLD:
                if self._mic_active_since is None:
                    self._mic_active_since = time.monotonic()
                sustained = time.monotonic() - self._mic_active_since
                signals.mic_sustained_secs = sustained
                signals.mic_amplitude_active = sustained >= AMPLITUDE_SUSTAINED_SECS
            else:
                self._mic_active_since = None

        return signals

    def _read_teams_network(self) -> float:
        total_bps = 0.0
        now = time.monotonic()

        for proc in psutil.process_iter(['pid', 'name']):
            try:
                name = (proc.info['name'] or '').lower()
                if name not in TEAMS_PROCESS_NAMES:
                    continue
                pid = proc.info['pid']
                io = proc.io_counters()
                total_bytes = io.read_bytes + io.write_bytes

                if pid in self._prev_net_bytes:
                    prev_bytes, prev_time = self._prev_net_bytes[pid]
                    elapsed = now - prev_time
                    if elapsed > 0:
                        total_bps += max(0.0, (total_bytes - prev_bytes) / elapsed)

                self._prev_net_bytes[pid] = (total_bytes, now)

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        return total_bps

    def _read_amplitude(self) -> float:
        try:
            import audioop
            data = self._stream.read(AMPLITUDE_CHUNK, exception_on_overflow=False)
            return float(audioop.rms(data, 2))
        except Exception as e:
            logger.debug(f"AudioWatcher: amplitude read error: {e}")
            return 0.0

    def cleanup(self):
        try:
            if self._stream:
                self._stream.stop_stream()
                self._stream.close()
            if self._pa:
                self._pa.terminate()
        except Exception:
            pass

    def reset_decay_timer(self):
        """
        Called by CallScorer when the call window title disappears.
        Clears the decay timer immediately so the network signal drops to inactive
        on the next poll rather than waiting up to 120 seconds.
        """
        self._last_spike_time = None
        self._net_history.clear()
        logger.debug("AudioWatcher: decay timer reset by external signal.")