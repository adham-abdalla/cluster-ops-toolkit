#!/usr/bin/env bash
# Pull /status from every node, build the fleet report and (optionally) cordon + drain
# the nodes it recommends.
#
#   scripts/fleet-report.sh -f nodes.txt            # nodes.txt: one host[:port] per line
#   scripts/fleet-report.sh -d ./reports            # use JSON files already collected
#   scripts/fleet-report.sh -f nodes.txt --apply    # really cordon/drain (default is dry run)
set -euo pipefail

usage() { sed -n '2,8p' "$0"; exit 2; }

hosts_file="" report_dir="" apply=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -f) hosts_file="${2:?}"; shift 2 ;;
    -d) report_dir="${2:?}"; shift 2 ;;
    --apply) apply=1; shift ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done
[[ -n "$hosts_file" || -n "$report_dir" ]] || usage

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "$report_dir" ]]; then
  report_dir="$(mktemp -d)"
  trap 'rm -rf "$report_dir"' EXIT
  while IFS= read -r host || [[ -n "$host" ]]; do
    [[ -z "$host" || "$host" == \#* ]] && continue
    [[ "$host" == *:* ]] || host="$host:9105"
    if ! curl -fsS --max-time 5 "http://$host/status" -o "$report_dir/${host%%:*}.json"; then
      echo "WARN: no answer from $host" >&2
    fi
  done < "$hosts_file"
fi

shopt -s nullglob
files=("$report_dir"/*.json)
if [[ ${#files[@]} -eq 0 ]]; then
  echo "no reports found in $report_dir" >&2
  exit 1
fi

summary="$(cd "$here" && python3 -m fleetcheck report --json "${files[@]}")" || true
(cd "$here" && python3 -m fleetcheck report "${files[@]}") || true

mapfile -t drain < <(jq -r '.drain[].node' <<<"$summary")
[[ ${#drain[@]} -eq 0 ]] && { echo "nothing to drain"; exit 0; }

run() {
  if [[ $apply -eq 1 ]]; then
    echo "+ $*"
    "$@"
  else
    echo "DRY RUN: $*"
  fi
}

for node in "${drain[@]}"; do
  run kubectl cordon "$node"
  run kubectl drain "$node" --ignore-daemonsets --delete-emptydir-data --timeout=300s
done
