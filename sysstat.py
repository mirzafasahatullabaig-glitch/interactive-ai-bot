"""Cheap system stats for the kiosk status bar. Stdlib only (no psutil) --
reads /proc and vcgencmd on the Pi; returns None fields off-Pi so the UI
just omits them. CPU% is sampled between calls.
"""
import os
import subprocess
import time

_prev = {}   # cpu jiffies between calls


def _cpu_percent():
    """Whole-system CPU% since the previous call (0-100), or None off-Linux."""
    try:
        with open("/proc/stat") as f:
            parts = [int(x) for x in f.readline().split()[1:]]
    except OSError:
        return None
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
    total = sum(parts)
    pi, pt = _prev.get("idle", idle), _prev.get("total", total)
    _prev["idle"], _prev["total"] = idle, total
    dt = total - pt
    if dt <= 0:
        return None
    return round(100.0 * (1 - (idle - pi) / dt), 1)


def _mem():
    """(used_mb, total_mb) from /proc/meminfo, or (None, None)."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":")
                info[k] = int(v.split()[0])   # kB
    except OSError:
        return None, None
    total = info.get("MemTotal", 0) / 1024
    avail = info.get("MemAvailable", 0) / 1024
    return round(total - avail), round(total)


def _temp():
    """CPU temp in C, or None. Tries sysfs then vcgencmd."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read()) / 1000.0, 1)
    except OSError:
        pass
    try:
        out = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True,
                             text=True, timeout=2).stdout
        return float(out.split("=")[1].split("'")[0])   # temp=47.0'C
    except (OSError, IndexError, ValueError):
        return None


def snapshot():
    """Dict of current stats; absent values are None. Safe to call often."""
    used, total = _mem()
    return {"cpu": _cpu_percent(), "mem_used": used, "mem_total": total,
            "temp": _temp()}


def fmt(s):
    """One short line for the status bar, skipping unavailable fields."""
    bits = []
    if s["cpu"] is not None:
        bits.append(f"CPU {s['cpu']:.0f}%")
    if s["mem_used"] is not None:
        bits.append(f"RAM {s['mem_used']}/{s['mem_total']}MB")
    if s["temp"] is not None:
        bits.append(f"{s['temp']:.0f}°C")
    return "  •  ".join(bits) if bits else "stats n/a"
