import json
import threading
import urllib.request

import pytest

from fleetcheck import __main__ as cli
from fleetcheck import agent, probes, report, rules


def make_node(root, mem_total_kb=65_000_000, mem_avail_kb=30_000_000, load="2.0 4.0 3.0", cpus=8,
              nic_errors=(0, 0, 3), cards=None):
    """Build a fake /proc and /sys tree under root."""
    (root / "proc").mkdir(parents=True)
    (root / "proc/meminfo").write_text(
        f"MemTotal: {mem_total_kb} kB\nMemFree: 1000 kB\nMemAvailable: {mem_avail_kb} kB\n")
    (root / "proc/loadavg").write_text(f"{load} 2/900 12345\n")
    (root / "proc/stat").write_text("cpu  1 2 3\n" + "".join(f"cpu{i} 1 2 3\n" for i in range(cpus)) + "intr 0\n")
    (root / "proc/uptime").write_text("86400.5 1000.0\n")
    stats = root / "sys/class/net/eth0/statistics"
    stats.mkdir(parents=True)
    for name, v in zip(("rx_errors", "tx_errors", "rx_dropped"), nic_errors, strict=True):
        (stats / name).write_text(f"{v}\n")
    (root / "sys/class/net/lo/statistics").mkdir(parents=True)
    dev = root / "devices.json"
    dev.write_text(json.dumps({"devices": cards if cards is not None else [
        {"id": i, "temp_c": 55 + i, "power_w": 75, "aiclk_mhz": 1000, "eth_link_errors": 0, "status": "ok"}
        for i in range(4)]}))
    return {"disks": ["/"], "accelerator_fixture": str(dev), "accelerators_expected": 4, "node_name": "n1"}


def test_probes_read_proc_and_sys(tmp_path):
    cfg = make_node(tmp_path)
    f = probes.collect(cfg, str(tmp_path))
    assert f["mem_used_pct"] == pytest.approx(53.8, abs=0.1)
    assert f["cpus"] == 8 and f["load_per_cpu"] == 0.5
    assert f["nic_errors"] == {"eth0": 3}          # loopback ignored
    assert f["uptime_s"] == 86400
    assert len(f["accelerators"]) == 4
    assert f["probe_errors"] == {}


def test_healthy_node_is_ok(tmp_path):
    cfg = make_node(tmp_path)
    relaxed = {**cfg, "thresholds": {"disk_used_pct": {"warn": 101, "crit": 102}}}  # CI disks can be full
    result = rules.evaluate(probes.collect(cfg, str(tmp_path)), relaxed)
    assert result["status"] == "OK", [c for c in result["checks"] if c["level"]]


@pytest.mark.parametrize("kwargs,check,status", [
    ({"mem_avail_kb": 5_000_000}, "memory", "WARN"),
    ({"mem_avail_kb": 1_000_000}, "memory", "CRIT"),
    ({"load": "9 13 9", "cpus": 8}, "load", "WARN"),
    ({"nic_errors": (900, 50, 100)}, "nic:eth0", "CRIT"),
])
def test_thresholds(tmp_path, kwargs, check, status):
    cfg = make_node(tmp_path, **kwargs)
    result = rules.evaluate(probes.collect(cfg, str(tmp_path)), cfg)
    assert next(c for c in result["checks"] if c["check"] == check)["status"] == status


def test_missing_card_and_hot_card(tmp_path):
    cards = [{"id": 0, "temp_c": 97, "status": "ok"}, {"id": 1, "temp_c": 60, "status": "ok"},
             {"id": 2, "temp_c": 60, "status": "ok", "eth_link_errors": 3}]
    cfg = make_node(tmp_path, cards=cards)
    result = rules.evaluate(probes.collect(cfg, str(tmp_path)), cfg)
    by = {c["check"]: c["status"] for c in result["checks"]}
    assert by["accelerators:count"] == "CRIT"
    assert by["accel:0:temp"] == "CRIT"
    assert by["accel:2:eth"] == "WARN"
    assert result["status"] == "CRIT"


def test_card_fault_status_is_critical(tmp_path):
    cards = [{"id": i, "temp_c": 50, "status": "ok"} for i in range(3)] + [{"id": 3, "status": "hang"}]
    cfg = make_node(tmp_path, cards=cards)
    result = rules.evaluate(probes.collect(cfg, str(tmp_path)), cfg)
    assert any(c["check"] == "accel:3:status" and c["status"] == "CRIT" for c in result["checks"])


def test_broken_probe_does_not_hide_the_others(tmp_path):
    cfg = make_node(tmp_path)
    (tmp_path / "proc/loadavg").unlink()
    facts = probes.collect(cfg, str(tmp_path))
    assert "load" in facts["probe_errors"] and "mem_used_pct" in facts
    result = rules.evaluate(facts, cfg)
    assert any(c["check"] == "probe:load" and c["status"] == "WARN" for c in result["checks"])


def test_unparseable_device_output(tmp_path):
    cfg = make_node(tmp_path)
    (tmp_path / "devices.json").write_text("not json")
    result = rules.evaluate(probes.collect(cfg, str(tmp_path)), cfg)
    assert result["status"] == "CRIT"  # cards expected but cannot be read


def test_accelerator_command_failure_is_reported(tmp_path):
    out = probes.accelerators(cmd="exit 3")
    assert out["accelerators"] == [] and "accelerator_probe_error" in out


def test_http_endpoints(tmp_path):
    cfg = make_node(tmp_path)
    server, _state, stop = agent.serve({**cfg, "interval_s": 60}, "127.0.0.1", 0, str(tmp_path))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert urllib.request.urlopen(base + "/healthz").read() == b"ok\n"
        status = json.loads(urllib.request.urlopen(base + "/status").read())
        assert status["node"] == "n1" and status["facts"]["cpus"] == 8
        metrics = urllib.request.urlopen(base + "/metrics").read().decode()
        assert 'fleetcheck_status{node="n1"}' in metrics
        assert 'fleetcheck_accel_temp_c{node="n1",card="3"} 58' in metrics
        assert "# TYPE fleetcheck_collections_total counter" in metrics
        for line in metrics.splitlines():        # every sample line is "name{labels} number"
            if not line.startswith("#"):
                float(line.rsplit(" ", 1)[1])
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/nope")
        assert e.value.code == 404
    finally:
        stop.set()
        server.shutdown()
        server.server_close()


def _r(node, level, checks=()):
    return {"node": node, "status": rules.NAMES[level], "level": level,
            "checks": [{"check": c, "status": rules.NAMES[level], "level": level, "message": c} for c in checks]}


def test_report_drains_critical_and_persistent_warnings_only():
    reports = [_r("a", 0), _r("b", 2, ["accel:0:temp"]),
               _r("c", 1, ["disk:/"]), _r("c", 1, ["disk:/"]), _r("c", 1, ["disk:/"]),
               _r("d", 1, ["load"]), _r("e", 0), _r("f", 0), _r("g", 0), _r("h", 0)]
    s = report.summarize(reports, warn_streak=3)
    assert [d["node"] for d in s["drain"]] == ["b", "c"]       # d warned once: not drained
    assert s["counts"] == {"OK": 5, "WARN": 2, "CRIT": 1}


def test_report_never_drains_more_than_a_quarter_of_the_fleet():
    reports = [_r(f"n{i}", 2, ["memory"]) for i in range(8)]
    s = report.summarize(reports)
    assert len(s["drain"]) == 2 and len(s["held_back"]) == 6
    assert "HELD" in report.render(s)


def test_cli_check_exit_code_and_json(tmp_path, capsys):
    cfg = make_node(tmp_path, mem_avail_kb=1_000_000)
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps(cfg))
    assert cli.main(["check", "--config", str(cfg_path), "--root", str(tmp_path), "--json"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "CRIT"


def test_cli_reads_yaml_config(tmp_path):
    cfg = make_node(tmp_path)
    p = tmp_path / "cfg.yml"
    p.write_text(f"accelerator_fixture: {cfg['accelerator_fixture']}\naccelerators_expected: 4\n"
                 "thresholds:\n  disk_used_pct: {warn: 101, crit: 102}\n")
    assert cli.main(["check", "--config", str(p), "--root", str(tmp_path)]) == 0
