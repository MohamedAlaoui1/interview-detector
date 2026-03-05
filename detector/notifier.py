"""
notifier.py

Fires Windows toast notifications when a call is detected.
Uses winotify — pure Python, no admin rights required, works from venv.

Notification behaviour:
  - Only fires once per detected call (cooldown prevents spam)
  - 30-second cooldown after firing before it can fire again
  - Test notification available for manual testing via the API
  - Retries once on toast failure before falling back to console
"""

import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

NOTIFICATION_COOLDOWN_SECS: int = 30


class Notifier:
    """
    Manages Windows toast notifications for call detection events.
    Handles deduplication and cooldown internally.
    """

    def __init__(self):
        self._last_notified_at: Optional[float] = None
        self._notified_this_call: bool = False
        self._winotify_available: bool = False
        self._try_import()

    def _try_import(self):
        try:
            from winotify import Notification, audio
            self._winotify_available = True
            logger.info("Notifier: winotify available.")
        except ImportError:
            logger.warning(
                "Notifier: winotify not installed. "
                "Install with: pip install winotify\n"
                "Falling back to console-only notifications."
            )

    def notify_call_detected(
        self,
        app_name: str,
        confidence: int,
        all_apps: list[str] | None = None
    ) -> bool:
        """
        Fire a 'call detected' toast notification.
        Returns True if notification was sent, False if suppressed (cooldown or already notified).
        """
        if self._notified_this_call:
            logger.debug("Notifier: suppressed — already notified for this call.")
            return False

        now = time.time()
        if self._last_notified_at:
            elapsed = now - self._last_notified_at
            if elapsed < NOTIFICATION_COOLDOWN_SECS:
                logger.debug(f"Notifier: suppressed — cooldown active ({elapsed:.0f}s / {NOTIFICATION_COOLDOWN_SECS}s elapsed).")
                return False

        confidence_label = {1: "Low", 2: "Medium", 3: "High"}.get(confidence, "Unknown")
        title = "📞 Call Detected"

        if all_apps and len(all_apps) > 1:
            apps_str = " · ".join(all_apps)
            message = f"Active: {apps_str}\nStart the interview assistant?"
        else:
            message = f"{app_name} call in progress.\nStart the interview assistant?"

        subtitle = f"Confidence: {confidence_label}"
        sent = self._send(title=title, message=message, subtitle=subtitle)

        if sent:
            self._last_notified_at = now
            self._notified_this_call = True
            logger.info(f"Notifier: toast sent for {app_name} (confidence={confidence}/3).")
        else:
            logger.warning("Notifier: toast FAILED for %s — check winotify install.", app_name)

        return sent

    def notify_call_ended(self) -> bool:
        """Fire a subtle 'call ended' notification and reset state."""
        self._notified_this_call = False  # Reset for next call

        return self._send(
            title="📴 Call Ended",
            message="The call has ended.",
            subtitle="Interview Assistant",
        )

    def test_notify(self) -> bool:
        """Send a test notification — available via the /test-notify API endpoint."""
        return self._send(
            title="🧪 Test Notification",
            message="If you can see this, toast notifications are working!",
            subtitle="Interview Detector",
        )

    def reset_call_state(self):
        """Call this when a call ends to allow notifications on the next call."""
        self._notified_this_call = False

    def _send(self, title: str, message: str, subtitle: str = "") -> bool:
        """
        Internal: send the toast, with one retry on failure.
        Falls back to console print if winotify isn't available or both attempts fail.
        """
        if self._winotify_available:
            for attempt in range(2):
                try:
                    from winotify import Notification, audio

                    toast = Notification(
                        app_id="Interview Assistant",
                        title=title,
                        msg=message,
                        duration="long",   # changed: 'long' (~25s) is more reliable than 'short'
                    )
                    toast.set_audio(audio.Default, loop=False)
                    toast.show()
                    logger.debug("Notifier: toast delivered (attempt %d).", attempt + 1)
                    return True

                except Exception as e:
                    logger.warning(f"Notifier: toast attempt {attempt + 1} failed — {e}")
                    if attempt == 0:
                        time.sleep(0.5)  # brief pause before retry

            logger.error("Notifier: both toast attempts failed — falling back to console.")

        # Console fallback — always works, always visible in terminal
        print("\n" + "=" * 55)
        print(f"[NOTIFICATION] {title}")
        if subtitle:
            print(f"  {subtitle}")
        print(f"  {message}")
        print("=" * 55 + "\n")
        return True  # console fallback counts as sent
