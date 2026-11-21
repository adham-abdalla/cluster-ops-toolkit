"""Fleet report: combine per-node /status JSON into one view and decide what to drain.

A node is recommended for drain when it is CRIT, or when it has been WARN in at
least `warn_streak` consecutive reports (flapping nodes are not drained on one blip).
"""
import json


def summarize(reports, warn_streak=3, max_drain_fraction=0.25):
    """reports: list of node reports, possibly several per node (oldest first)."""
    by_node = {}
    for r in reports:
        by_node.setdefault(r["node"], []).append(r)

    rows, drain = [], []
    for node, history in sorted(by_node.items()):
        latest = history[-1]
        failing = [c for c in latest["checks"] if c["level"] > 0]
        streak = 0
        for r in reversed(history):
            if r["level"] >= 1:
                streak += 1
            else:
                break
        reason = None
        if latest["level"] == 2:
            reason = "critical: " + "; ".join(c["message"] for c in failing if c["level"] == 2)
        elif latest["level"] == 1 and streak >= warn_streak:
            reason = f"warning for {streak} reports: " + "; ".join(c["message"] for c in failing)
        rows.append({"node": node, "status": latest["status"], "failing": [c["check"] for c in failing],
                     "streak": streak})
        if reason:
            drain.append({"node": node, "reason": reason})

    # safety valve: never drain more than a fraction of the fleet in one pass
    limit = max(1, int(len(by_node) * max_drain_fraction))
    held = []
    if len(drain) > limit:
        drain.sort(key=lambda d: (not d["reason"].startswith("critical"), d["node"]))
        drain, held = drain[:limit], drain[limit:]
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("OK", "WARN", "CRIT")}
    return {"nodes": len(rows), "counts": counts, "rows": rows, "drain": drain, "held_back": held}


def render(summary):
    c = summary["counts"]
    out = [f"{summary['nodes']} nodes: {c['OK']} OK, {c['WARN']} WARN, {c['CRIT']} CRIT", ""]
    out.append(f"{'NODE':<16}{'STATUS':<8}{'STREAK':<8}FAILING CHECKS")
    for r in summary["rows"]:
        out.append(f"{r['node']:<16}{r['status']:<8}{r['streak']:<8}{', '.join(r['failing']) or '-'}")
    out.append("")
    for d in summary["drain"]:
        out.append(f"DRAIN {d['node']}: {d['reason']}")
    for d in summary["held_back"]:
        out.append(f"HELD  {d['node']} (drain limit reached): {d['reason']}")
    return "\n".join(out)


def load_reports(paths):
    reports = []
    for p in paths:
        with open(p) as f:
            data = json.load(f)
        reports.extend(data if isinstance(data, list) else [data])
    return reports
