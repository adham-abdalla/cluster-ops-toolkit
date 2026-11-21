# cluster-ops-toolkit

[![CI/CD](https://github.com/adham-abdalla/cluster-ops-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/adham-abdalla/cluster-ops-toolkit/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.12-3776AB)
![Ansible](https://img.shields.io/badge/Ansible-idempotent-EE0000)
![Docker](https://img.shields.io/badge/Docker-non--root-2496ED)
![Kubernetes](https://img.shields.io/badge/Kubernetes-DaemonSet-326CE5)

Health monitoring and safe remediation for a fleet of **AI accelerator servers**, deployed three ways: Ansible + systemd on bare metal, a Docker container, and a Kubernetes DaemonSet. Every path is exercised end to end in CI.

```
$ python -m fleetcheck check --config examples/config.yml     # example output from a lab node
overall: WARN
  OK    memory                       41.2% of RAM in use
  OK    load                         5-min load 0.31 per CPU
  OK    disk:/                       / 38.0% full
  OK    accelerators:count           4/4 cards visible
  OK    accel:0:temp                 card 0 at 61.5 °C
  WARN  accel:2:eth                  card 2 ethernet link errors 2
  ...
```

## What's in it

| Part | What it does |
|---|---|
| `fleetcheck/` agent (Python, stdlib + PyYAML) | Reads `/proc` and `/sys` (memory, load per CPU, disk, NIC errors, uptime) and accelerator cards through a vendor CLI that prints JSON (temperature, power, clock, inter-card Ethernet errors, status). Evaluates thresholds into OK/WARN/CRIT. Serves `/healthz`, `/status` (JSON) and `/metrics` (Prometheus). One broken probe is reported, it does not hide the others. |
| `fleetcheck report` + `scripts/fleet-report.sh` | Collects `/status` from every node, prints a fleet table and decides what to drain: CRIT now, or WARN three reports in a row. Never drains more than 25% of the fleet in one pass. Dry run by default; `--apply` runs `kubectl cordon` / `drain`. |
| `ansible/` | `common` role (packages, sysctl, service account) and `fleetcheck` role (code, config template, hardened systemd unit, health check after deploy). Inventory grouped by hardware generation. |
| `Dockerfile` | Non-root (65534), works with `--read-only`, built-in `HEALTHCHECK`. |
| `k8s/` | Kustomize: Namespace, ConfigMap, DaemonSet (read-only `/proc` + `/sys` hostPath mounts, all capabilities dropped, resource limits, liveness/readiness probes, tolerates every taint so drained nodes keep reporting), Service. |
| `docs/RUNBOOK.md` | What each alert means, first steps, and whether to drain. |

## CI/CD pipeline

```
lint ──► test ──┬──► ansible-deploy ──┐
                ├──► container ───────┼──► publish image to GHCR (main only)
                └──► kubernetes ──────┘
```

| Job | What it proves |
|---|---|
| lint | ruff, shellcheck, `ansible-playbook --syntax-check`, `kubectl kustomize` renders |
| test | 19 pytest tests on Python 3.10 and 3.12: probes against fake `/proc` trees, every threshold, broken-probe isolation, HTTP endpoints and Prometheus format, drain policy, the bash script in dry-run mode |
| ansible-deploy | The playbook deploys the agent **to the CI runner itself** as a systemd service, then runs again and must report `changed=0` (idempotency). Then `/status` must show 4 cards. |
| container | Builds the image, runs it read-only against the host's `/proc`, waits for Docker's health check, scrapes `/metrics`, confirms it is not running as root. |
| kubernetes | Creates a **kind** cluster, loads the image, applies the manifests, waits for the DaemonSet rollout, scrapes through the Service and runs the fleet report against it. |
| publish | Pushes `ghcr.io/adham-abdalla/fleetcheck:latest` and `:<sha>` only after all three deploy paths pass. |

## Run it

```bash
pip install pyyaml pytest
python -m pytest                                   # 19 tests
python -m fleetcheck check --config examples/config.yml
python -m fleetcheck serve --config examples/config.yml --port 9105
curl localhost:9105/metrics

# bare metal / VMs
cd ansible && ansible-playbook -i inventory.example.ini site.yml --check --diff

# Kubernetes
kubectl apply -k k8s && kubectl -n fleetcheck rollout status ds/fleetcheck

# fleet report (dry run)
bash scripts/fleet-report.sh -f nodes.txt
```

## Design choices

- **Probes take a root path.** Tests build a fake `/proc` and `/sys` in a temp folder; containers read the host's through `/host`. Same code everywhere.
- **The vendor CLI is a seam.** Accelerator data comes from any command that prints `{"devices": [...]}`, so the agent does not link against a driver library. `examples/devices.json` is a snapshot used by the demos and CI.
- **Remediation is conservative.** WARN has to persist, CRIT drains, a fleet-wide fraction caps the blast radius, and nothing runs without `--apply`.
- **Least privilege.** systemd unit with `ProtectSystem=strict`, `NoNewPrivileges`; container non-root and read-only; pod drops all capabilities and mounts host paths read-only.

## License

MIT
