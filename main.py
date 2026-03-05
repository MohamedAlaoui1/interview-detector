"""
main.py — Entry point for the Interview Detector

Runs two things in one Python process:
  1. Background async detection loop (polls every 3 seconds)
  2. FastAPI server on localhost:8000 for debug + state inspection

Usage:
    python main.py

No admin rights needed. Runs entirely from your venv.
"""

import asyncio
import logging
import sys
import time

import uvicorn

from detector.call_scorer import CallScorer
from detector.notifier import Notifier
from api.main import app as fastapi_app, init as init_api

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

POLL_INTERVAL_SECS: float = 3.0


async def detection_loop(scorer: CallScorer, notifier: Notifier):
    logger.info("Detection loop started. Polling every %.0fs.", POLL_INTERVAL_SECS)
    was_active: bool = False

    while True:
        try:
            state = scorer.evaluate()

            if state.call_active:
                if not was_active:
                    # FIX: use correct signal keys that match AudioSignals/ProcessSignals .to_dict()
                    logger.info(
                        "▶  Call STARTED  |  Score: %d/3  |  Confidence: %d/3  |  "
                        "window=%s  mic=%s  network=%s",
                        state.score,
                        state.confidence,
                        "✓" if state.process_signals.get("teams_call_window_detected") else "✗",
                        "✓" if state.audio_signals.get("mic_amplitude_active") else "✗",
                        "✓" if state.audio_signals.get("teams_network_active") else "✗",
                    )
                    was_active = True

                notifier.notify_call_detected(
                    app_name=state.app_name or "Microsoft Teams",
                    confidence=state.confidence,
                    all_apps=state.all_active_apps,
                )

            else:
                if was_active:
                    logger.info("■  Call ENDED")
                    was_active = False
                    notifier.reset_call_state()
                    notifier.notify_call_ended()

        except Exception as e:
            logger.error("Detection loop error: %s", e, exc_info=True)

        await asyncio.sleep(POLL_INTERVAL_SECS)


async def run_server():
    config = uvicorn.Config(
        app=fastapi_app,
        host="127.0.0.1",
        port=8000,
        log_level="warning",
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    logger.info("=" * 60)
    logger.info("  Interview Detector  |  starting up")
    logger.info("  Debug UI  → http://localhost:8000")
    logger.info("  Signals   → http://localhost:8000/signals")
    logger.info("  Status    → http://localhost:8000/status")
    logger.info("=" * 60)

    scorer = CallScorer()
    notifier = Notifier()
    init_api(scorer, notifier)

    try:
        await asyncio.gather(
            detection_loop(scorer, notifier),
            run_server(),
        )
    finally:
        scorer.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Detector stopped.")
        sys.exit(0)