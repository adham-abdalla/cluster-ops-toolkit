"""Node agent: collects facts on an interval and serves them over HTTP.

  GET /healthz   liveness: 200 while the collector thread is alive and fresh
  GET /status    JSON: facts + evaluated checks
  GET /metrics   Prometheus text format
"""
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import probes, rules


class State:
    def __init__(self, cfg, root="/"):
        self.cfg, self.root = cfg, root
        self.node = cfg.get("node_name") or os.environ.get("FLEETCHECK_NODE_NAME") or socket.gethostname()
        self.lock = threading.Lock()
        self.report = None
        self.updated = 0.0
        self.collections = 0

    def refresh(self):
        facts = probes.collect(self.cfg, self.root)
        result = rules.evaluate(facts, self.cfg)
        report = {"node": self.node, "time": int(time.time()), "facts": facts, **result}
        with self.lock:
            self.report, self.updated = report, time.monotonic()
            self.collections += 1
        return report

    def snapshot(self):
        with self.lock:
            return self.report, self.updated, self.collections


def _esc(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"')


def prometheus(report, collections):
    node = _esc(report["node"])
    f = report["facts"]
    lines = [
        "# HELP fleetcheck_status Overall node status (0 ok, 1 warn, 2 crit).",
        "# TYPE fleetcheck_status gauge",
        f'fleetcheck_status{{node="{node}"}} {report["level"]}',
        "# HELP fleetcheck_check_status Status of one check (0 ok, 1 warn, 2 crit).",
        "# TYPE fleetcheck_check_status gauge",
    ]
    lines += [f'fleetcheck_check_status{{node="{node}",check="{_esc(c["check"])}"}} {c["level"]}'
              for c in report["checks"]]
    gauges = [("mem_used_pct", "Memory in use, percent."), ("load_per_cpu", "5-minute load per CPU."),
              ("uptime_s", "Seconds since boot."), ("collect_ms", "Time the last collection took.")]
    for key, help_ in gauges:
        if key in f:
            lines += [f"# HELP fleetcheck_{key} {help_}", f"# TYPE fleetcheck_{key} gauge",
                      f'fleetcheck_{key}{{node="{node}"}} {f[key]}']
    lines += ["# HELP fleetcheck_disk_used_pct Filesystem in use, percent.",
              "# TYPE fleetcheck_disk_used_pct gauge"]
    lines += [f'fleetcheck_disk_used_pct{{node="{node}",path="{_esc(p)}"}} {v}'
              for p, v in f.get("disk_used_pct", {}).items()]
    cards = f.get("accelerators", [])
    if cards:
        lines += ["# HELP fleetcheck_accel_temp_c Accelerator temperature.", "# TYPE fleetcheck_accel_temp_c gauge"]
        lines += [f'fleetcheck_accel_temp_c{{node="{node}",card="{_esc(c.get("id"))}"}} {c["temp_c"]}'
                  for c in cards if "temp_c" in c]
        lines += ["# HELP fleetcheck_accel_power_w Accelerator power draw.", "# TYPE fleetcheck_accel_power_w gauge"]
        lines += [f'fleetcheck_accel_power_w{{node="{node}",card="{_esc(c.get("id"))}"}} {c["power_w"]}'
                  for c in cards if "power_w" in c]
    lines += ["# HELP fleetcheck_collections_total Collections since start.",
              "# TYPE fleetcheck_collections_total counter",
              f'fleetcheck_collections_total{{node="{node}"}} {collections}']
    return "\n".join(lines) + "\n"


def make_handler(state):
    interval = state.cfg.get("interval_s", 30)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            report, updated, n = state.snapshot()
            if self.path == "/healthz":
                fresh = report is not None and time.monotonic() - updated < 3 * interval
                self._send(200 if fresh else 503, "ok\n" if fresh else "stale\n", "text/plain")
            elif report is None:
                self._send(503, "warming up\n", "text/plain")
            elif self.path == "/status":
                self._send(200, json.dumps(report, indent=1), "application/json")
            elif self.path == "/metrics":
                self._send(200, prometheus(report, n), "text/plain; version=0.0.4")
            else:
                self._send(404, "not found\n", "text/plain")

        def log_message(self, *args):  # quiet: the agent logs collections, not every scrape
            pass

    return Handler


def serve(cfg, host="0.0.0.0", port=9105, root="/"):
    state = State(cfg, root)
    state.refresh()
    stop = threading.Event()

    def loop():
        while not stop.wait(cfg.get("interval_s", 30)):
            r = state.refresh()
            print(f"[fleetcheck] {r['node']} {r['status']} ({len(r['checks'])} checks)", flush=True)

    threading.Thread(target=loop, daemon=True).start()
    server = ThreadingHTTPServer((host, port), make_handler(state))
    print(f"[fleetcheck] serving on {host}:{server.server_port}", flush=True)
    return server, state, stop
