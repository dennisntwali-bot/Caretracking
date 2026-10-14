"""Command line entry point.

  python -m caretracking snapshot            # print the generated hospital
  python -m caretracking export data.json    # dump the synthetic dataset
  python -m caretracking run                 # one agent review
  python -m caretracking run --cycles 6 --interval 60
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .agent import ClaudeCareAgent, RuleBasedCareAgent
from .hospital import Hospital
from .models import to_jsonable
from .tools import get_ward_overview


def _hospital(args) -> Hospital:
    return Hospital.generate(seed=args.seed, occupancy=args.occupancy)


def cmd_snapshot(args) -> None:
    h = _hospital(args)
    ov = get_ward_overview(h)
    print(f"{ov['now']}  {ov['patient_count']} active patients")
    print(f"{'id':6} {'ward':12} {'bed':4} {'NEWS2':>5} {'6h':>4} {'qSOFA':>5} {'obs late':>8} "
          f"{'doses late':>10}  diagnosis")
    for r in ov["patients"]:
        print(f"{r['patient_id']:6} {r['ward']:12} {r['bed']:4} {r['news2']:>5} "
              f"{r['news2_change_6h']:>+4} {r['qsofa']:>5} {r['obs_overdue_min']:>8} "
              f"{r['overdue_doses']:>10}  {r['diagnosis']}")


def cmd_export(args) -> None:
    h = _hospital(args)
    data = {"generated_at": h.now, "patients": list(h.patients.values())}
    with open(args.path, "w") as f:
        json.dump(to_jsonable(data), f, indent=1)
    print(f"Wrote {len(h.patients)} patients to {args.path}")


def cmd_run(args) -> None:
    h = _hospital(args)
    use_claude = not args.offline and (os.environ.get("ANTHROPIC_API_KEY")
                                       or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    if use_claude:
        agent = ClaudeCareAgent(effort=args.effort, verbose=args.verbose)
        print("Agent: Claude (claude-opus-5-5)")
    else:
        agent = RuleBasedCareAgent()
        print("Agent: offline rule engine (set ANTHROPIC_API_KEY to use Claude)")

    for cycle in range(1, args.cycles + 1):
        if cycle > 1:
            for e in h.advance(minutes=args.interval):
                print(f"  event: {e}")
        print(f"\n=== Review {cycle}/{args.cycles} at {h.now:%Y-%m-%d %H:%M} ===")
        report = agent.review(h)
        for a in report.alerts_created:
            print(f"  ALERT {a['alert_id']} [{a['severity']}] {a['patient_id']} "
                  f"{a['category']}: {a['reason']}")
        print(f"\n{report.summary}\n({report.tool_calls} tool calls)")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="caretracking", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--occupancy", type=float, default=0.85)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("snapshot", help="print the current state of every patient")
    exp = sub.add_parser("export", help="write the synthetic dataset to JSON")
    exp.add_argument("path")
    run = sub.add_parser("run", help="run the care-tracking agent")
    run.add_argument("--cycles", type=int, default=1, help="number of reviews")
    run.add_argument("--interval", type=int, default=60, help="simulated minutes between reviews")
    run.add_argument("--offline", action="store_true", help="force the rule-based agent")
    run.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    run.add_argument("--verbose", action="store_true", help="print each tool call")

    args = parser.parse_args(argv)
    {"snapshot": cmd_snapshot, "export": cmd_export, "run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    main(sys.argv[1:])
