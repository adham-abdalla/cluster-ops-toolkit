"""The drain script, driven by pre-collected reports, in its default dry-run mode."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _r(node, level, checks=()):
    names = {0: "OK", 1: "WARN", 2: "CRIT"}
    return {"node": node, "status": names[level], "level": level,
            "checks": [{"check": c, "status": names[level], "level": level, "message": c} for c in checks]}


@pytest.mark.skipif(not shutil.which("jq"), reason="jq not installed")
def test_dry_run_prints_cordon_and_drain_but_runs_nothing(tmp_path):
    for i in range(6):
        (tmp_path / f"ok{i}.json").write_text(json.dumps(_r(f"ok-{i}", 0)))
    (tmp_path / "bad.json").write_text(json.dumps(_r("bad-1", 2, ["accel:0:status"])))
    p = subprocess.run(["bash", str(ROOT / "scripts/fleet-report.sh"), "-d", str(tmp_path)],
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    assert "DRY RUN: kubectl cordon bad-1" in p.stdout
    assert "DRY RUN: kubectl drain bad-1 --ignore-daemonsets" in p.stdout
    assert "ok-0" not in [line.split()[-1] for line in p.stdout.splitlines() if line.startswith("DRY RUN")]


@pytest.mark.skipif(not shutil.which("jq"), reason="jq not installed")
def test_healthy_fleet_needs_no_drain(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps(_r("a", 0)))
    p = subprocess.run(["bash", str(ROOT / "scripts/fleet-report.sh"), "-d", str(tmp_path)],
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0 and "nothing to drain" in p.stdout


def test_bad_arguments_exit_2():
    p = subprocess.run(["bash", str(ROOT / "scripts/fleet-report.sh"), "--bogus"], capture_output=True, text=True)
    assert p.returncode == 2
