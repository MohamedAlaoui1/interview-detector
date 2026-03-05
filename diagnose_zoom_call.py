"""
diagnose_zoom_call.py

Run this DURING an active Zoom call.
Prints network bytes/sec for all Zoom processes every 2 seconds for 30 seconds.
This shows us the real range so we can set the right threshold and grace period.

Usage:
    python diagnose_zoom_call.py
"""

import psutil
import time

ZOOM_NAME = 'Zoom.exe'
DURATION  = 30   # seconds to watch
INTERVAL  = 2    # seconds between readings

def get_zoom_bytes():
    total = 0
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            if proc.info['name'] == ZOOM_NAME:
                io = proc.io_counters()
                total += io.read_bytes + io.write_bytes
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total

print(f"Watching {ZOOM_NAME} network for {DURATION}s — run this ON a Zoom call\n")
print(f"{'Time':>6}  {'KB/s':>10}  {'Status'}")
print("-" * 35)

prev_bytes = get_zoom_bytes()
prev_time  = time.monotonic()
readings   = []

for i in range(DURATION // INTERVAL):
    time.sleep(INTERVAL)
    now_bytes = get_zoom_bytes()
    now_time  = time.monotonic()
    elapsed   = now_time - prev_time
    bps       = (now_bytes - prev_bytes) / elapsed
    kbps      = bps / 1024
    readings.append(kbps)

    status = "✓ above 100KB/s" if kbps >= 100 else "✗ BELOW 100KB/s"
    print(f"{i*INTERVAL+INTERVAL:>5}s  {kbps:>10.1f}  {status}")

    prev_bytes = now_bytes
    prev_time  = now_time

print("\n--- Summary ---")
print(f"Min:  {min(readings):.1f} KB/s")
print(f"Max:  {max(readings):.1f} KB/s")
print(f"Avg:  {sum(readings)/len(readings):.1f} KB/s")
print(f"Dips below 100KB/s: {sum(1 for r in readings if r < 100)} / {len(readings)} readings")