"""Probes read raw facts from a Linux node. Each one takes a root path so tests can
point it at fixture files instead of the real /proc and /sys."""
import json
import os
import shutil
import subprocess
import time


def _read(root, rel):
    with open(os.path.join(root, rel.lstrip("/"))) as f:
        return f.read()


def memory(root="/"):
    info = {}
    for line in _read(root, "proc/meminfo").splitlines():
        key, _, rest = line.partition(":")
        info[key] = int(rest.split()[0]) * 1024
    total = info["MemTotal"]
    available = info.get("MemAvailable", info.get("MemFree", 0))
    return {"mem_total_bytes": total, "mem_available_bytes": available,
            "mem_used_pct": round(100 * (total - available) / total, 1)}


def load(root="/"):
    one, five, fifteen = (float(x) for x in _read(root, "proc/loadavg").split()[:3])
    cpus = sum(1 for line in _read(root, "proc/stat").splitlines() if line.startswith("cpu") and line[3].isdigit())
    return {"load1": one, "load5": five, "load15": fifteen, "cpus": cpus,
            "load_per_cpu": round(five / max(cpus, 1), 2)}


def disks(paths):
    out = {}
    for p in paths:
        try:
            u = shutil.disk_usage(p)
        except OSError:
            continue
        out[p] = round(100 * u.used / u.total, 1) if u.total else 0.0
    return {"disk_used_pct": out}


def network(root="/"):
    base = os.path.join(root, "sys/class/net")
    stats = {}
    if not os.path.isdir(base):
        return {"nic_errors": stats}
    for nic in sorted(os.listdir(base)):
        if nic == "lo":
            continue
        d = os.path.join(base, nic, "statistics")
        try:
            errs = sum(int(_read(d, f)) for f in ("rx_errors", "tx_errors", "rx_dropped"))
        except (OSError, ValueError):
            continue
        stats[nic] = errs
    return {"nic_errors": stats}


def uptime(root="/"):
    return {"uptime_s": int(float(_read(root, "proc/uptime").split()[0]))}


def accelerators(cmd=None, fixture=None):
    """Accelerator cards via a vendor CLI that prints JSON (for example a `tt-smi`-style
    snapshot). Each card: id, temp_c, power_w, aiclk_mhz, eth_link_errors, status."""
    if fixture:
        with open(fixture) as f:
            raw = f.read()
    elif cmd:
        try:
            raw = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15, check=True).stdout
        except (subprocess.SubprocessError, OSError) as e:
            return {"accelerators": [], "accelerator_probe_error": str(e)[:200]}
    else:
        return {"accelerators": []}
    try:
        cards = json.loads(raw).get("devices", [])
    except (json.JSONDecodeError, AttributeError):
        return {"accelerators": [], "accelerator_probe_error": "unparseable device output"}
    return {"accelerators": cards}


def collect(cfg, root="/"):
    started = time.monotonic()
    facts = {}
    errors = {}
    probes = {
        "memory": lambda: memory(root),
        "load": lambda: load(root),
        "disks": lambda: disks(cfg.get("disks", ["/"])),
        "network": lambda: network(root),
        "uptime": lambda: uptime(root),
        "accelerators": lambda: accelerators(cfg.get("accelerator_cmd"), cfg.get("accelerator_fixture")),
    }
    for name, fn in probes.items():
        try:
            facts.update(fn())
        except Exception as e:  # one broken probe must not hide the others
            errors[name] = f"{type(e).__name__}: {e}"[:200]
    facts["probe_errors"] = errors
    facts["collect_ms"] = round(1000 * (time.monotonic() - started), 1)
    return facts
