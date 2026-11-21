"""fleetcheck command line.

  python -m fleetcheck check  [--config FILE] [--json]      one-shot check, exit 0/1/2 = OK/WARN/CRIT
  python -m fleetcheck serve  [--config FILE] [--port N]    run the HTTP agent
  python -m fleetcheck report FILE... [--json]              fleet summary + drain list
"""
import argparse
import json
import sys

from . import agent, probes, report, rules


def load_config(path):
    if not path:
        return {}
    with open(path) as f:
        text = f.read()
    if path.endswith((".yml", ".yaml")):
        import yaml  # only needed for YAML configs
        return yaml.safe_load(text) or {}
    return json.loads(text)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fleetcheck")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--config")
    c.add_argument("--root", default="/", help="filesystem root for /proc and /sys (tests use fixtures)")
    c.add_argument("--json", action="store_true")
    s = sub.add_parser("serve")
    s.add_argument("--config")
    s.add_argument("--root", default="/")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=9105)
    r = sub.add_parser("report")
    r.add_argument("files", nargs="+")
    r.add_argument("--json", action="store_true")
    r.add_argument("--warn-streak", type=int, default=3)
    args = ap.parse_args(argv)

    if args.cmd == "check":
        cfg = load_config(args.config)
        facts = probes.collect(cfg, args.root)
        result = rules.evaluate(facts, cfg)
        if args.json:
            print(json.dumps({"facts": facts, **result}, indent=1))
        else:
            print(f"overall: {result['status']}")
            for chk in result["checks"]:
                print(f"  {chk['status']:<5} {chk['check']:<28} {chk['message']}")
        return result["level"]

    if args.cmd == "serve":
        server, _, stop = agent.serve(load_config(args.config), args.host, args.port, args.root)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            server.server_close()
        return 0

    summary = report.summarize(report.load_reports(args.files), warn_streak=args.warn_streak)
    print(json.dumps(summary, indent=1) if args.json else report.render(summary))
    return 2 if summary["drain"] else 0


if __name__ == "__main__":
    sys.exit(main())
