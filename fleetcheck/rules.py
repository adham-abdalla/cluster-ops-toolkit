"""Turn raw facts into checks with OK / WARN / CRIT, using thresholds from the config."""
OK, WARN, CRIT = 0, 1, 2
NAMES = {OK: "OK", WARN: "WARN", CRIT: "CRIT"}

DEFAULT_THRESHOLDS = {
    "mem_used_pct": {"warn": 85, "crit": 95},
    "load_per_cpu": {"warn": 1.5, "crit": 3.0},
    "disk_used_pct": {"warn": 80, "crit": 90},
    "nic_errors": {"warn": 100, "crit": 1000},
    "accel_temp_c": {"warn": 85, "crit": 95},
    "accel_eth_link_errors": {"warn": 1, "crit": 50},
}


def _level(value, t):
    if value >= t["crit"]:
        return CRIT
    if value >= t["warn"]:
        return WARN
    return OK


def evaluate(facts, cfg):
    th = {**DEFAULT_THRESHOLDS, **cfg.get("thresholds", {})}
    checks = []

    def add(name, status, value, msg):
        checks.append({"check": name, "status": NAMES[status], "level": status, "value": value, "message": msg})

    if "mem_used_pct" in facts:
        v = facts["mem_used_pct"]
        add("memory", _level(v, th["mem_used_pct"]), v, f"{v}% of RAM in use")
    if "load_per_cpu" in facts:
        v = facts["load_per_cpu"]
        add("load", _level(v, th["load_per_cpu"]), v, f"5-min load {v} per CPU")
    for path, v in facts.get("disk_used_pct", {}).items():
        add(f"disk:{path}", _level(v, th["disk_used_pct"]), v, f"{path} {v}% full")
    for nic, v in facts.get("nic_errors", {}).items():
        add(f"nic:{nic}", _level(v, th["nic_errors"]), v, f"{nic} rx/tx errors+drops {v}")

    expected = cfg.get("accelerators_expected")
    cards = facts.get("accelerators", [])
    if expected is not None:
        n = len(cards)
        add("accelerators:count", CRIT if n < expected else OK, n, f"{n}/{expected} cards visible")
    for card in cards:
        cid = card.get("id", "?")
        if card.get("status", "ok") != "ok":
            add(f"accel:{cid}:status", CRIT, card.get("status"), f"card {cid} reports {card.get('status')}")
        if "temp_c" in card:
            v = card["temp_c"]
            add(f"accel:{cid}:temp", _level(v, th["accel_temp_c"]), v, f"card {cid} at {v} °C")
        if "eth_link_errors" in card:
            v = card["eth_link_errors"]
            add(f"accel:{cid}:eth", _level(v, th["accel_eth_link_errors"]), v,
                f"card {cid} ethernet link errors {v}")
    for probe, err in facts.get("probe_errors", {}).items():
        add(f"probe:{probe}", WARN, None, f"probe failed: {err}")
    if facts.get("accelerator_probe_error"):
        add("probe:accelerators", CRIT if expected else WARN, None, facts["accelerator_probe_error"])

    overall = max((c["level"] for c in checks), default=OK)
    return {"status": NAMES[overall], "level": overall, "checks": checks}
