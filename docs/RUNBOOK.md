# Runbook: fleetcheck alerts

Each check maps to one line of `/status` and one `fleetcheck_check_status` series in Prometheus (0 OK, 1 WARN, 2 CRIT).

| Check | Typical cause | First steps | Drain? |
|---|---|---|---|
| `accelerators:count` CRIT | card not enumerated after reboot, PCIe link down, driver not loaded | `lspci -d 1e52:` and `dmesg | grep -i tenstorrent`; reseat or power-cycle the host | yes |
| `accel:<id>:status` CRIT | card reports hang / reset needed | run the vendor reset tool for that card, then re-check; collect logs first | yes |
| `accel:<id>:temp` WARN/CRIT | fan failure, blocked airflow, neighbour card running hot | check chassis fans and inlet temperature; compare with other cards in the rack | CRIT only |
| `accel:<id>:eth` WARN/CRIT | flaky inter-card Ethernet cable or port | reseat or swap the QSFP cable; errors that keep rising after reseat mean RMA | CRIT only |
| `memory` CRIT | runaway job or leak | find the process (`ps aux --sort=-rss | head`), talk to the job owner before killing | no |
| `disk:<path>` CRIT | logs or model caches filling the disk | `du -xh --max-depth=2 <path> | sort -h | tail`; clean caches, rotate logs | if `/` |
| `nic:<if>` WARN/CRIT | bad cable, duplex mismatch, switch port errors | `ethtool -S <if>`; check the switch side | no |
| `probe:*` WARN | a probe itself failed (missing file, vendor CLI timeout) | check agent logs: `journalctl -u fleetcheck` or `kubectl logs` | no |

## Draining

`scripts/fleet-report.sh` decides what to drain from the latest reports:

- **CRIT** now → drain.
- **WARN** for 3 reports in a row → drain (one blip does not take a node out).
- Never more than **25% of the fleet** in one pass. Extra candidates are listed as `HELD` so a person decides.

It prints the `kubectl cordon` / `kubectl drain` commands and runs nothing unless `--apply` is given.

## Returning a node

1. Fix the cause and confirm `/status` is `OK` for at least two collections.
2. `kubectl uncordon <node>`.
3. Note the cause and fix in the node's maintenance log so repeat offenders are visible.
