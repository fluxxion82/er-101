#!/usr/bin/env python3
"""Baseline capture runner for the ER-101 bench (plan item A14), scaffold.

    python3 tools/bench/bench.py --list
    python3 tools/bench/bench.py --dry-run [--only clock_to_gate_latency,ratchet]
    python3 tools/bench/bench.py --run --hex release/avr32/er-101-firmware-3.0.0.hex --out bench-results/

The capture matrix is data: tools/bench/captures.json. --dry-run walks it and
prints, for every capture, the probes to connect, the clock and reset pattern to
set up in Ableton (ES-8), the scope settings and the measurements to take; it
needs no module, no scope and no Ableton. --run drives a scope and clock source
through the backends in tools/bench/backends.py; the real backends are
placeholders until the first bench session (the scope and Ableton are reachable
from the maintainer's environment through control bridges, not from this script
yet), so --run refuses unless a backend is configured.

Every run archives a JSON result per capture under --out with the sha256 of the
firmware hex under test, the capture definition and the measurements, so a
later run can be compared against the baseline (regression limits) or against
the expected changes a fix declares.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import backends  # noqa: E402


def load_matrix(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def expand(capture):
    """A capture with for_each becomes one capture per item."""
    items = capture.get("for_each")
    if not items:
        return [capture]
    out = []
    for item in items:
        c = dict(capture)
        c["id"] = "%s[%s]" % (capture["id"], item.replace(" ", "_"))
        c["for_each_item"] = item
        out.append(c)
    return out


def describe(capture, matrix):
    lines = ["== %s (%s)" % (capture["id"], capture.get("group", ""))]
    probes = capture.get("probes", {})
    lines.append("   probes:   ch1 -> %s; ch2 -> %s" % (probes.get("ch1", "-"), probes.get("ch2", "-")))
    if "for_each_item" in capture:
        lines.append("   item:     %s" % capture["for_each_item"])
    stim = capture.get("stimulus", {})
    if "clock_bpm" in stim:
        lines.append("   clock:    %s at %s BPM, %s ppqn (%s)" % (
            matrix["clock_source"]["clock_output"], stim["clock_bpm"], stim.get("clock_ppqn", 1),
            matrix["clock_source"]["name"]))
    if "reset" in stim:
        lines.append("   reset:    %s on %s" % (stim["reset"], matrix["clock_source"]["reset_output"]))
    if "panel" in stim:
        lines.append("   panel:    %s" % stim["panel"])
    sc = capture.get("scope", {})
    lines.append("   scope:    trigger %s, %s s/div%s" % (
        sc.get("trigger", "auto"), sc.get("timebase_s_per_div", "?"),
        ", %s V/div" % sc["volts_per_div"] if "volts_per_div" in sc else ""))
    lines.append("   measure:  %s x %s" % ("; ".join(capture.get("measure", [])), capture.get("repeat", 1)))
    if "expected" in capture:
        lines.append("   expected: %s" % capture["expected"])
    return "\n".join(lines)


def run_capture(capture, matrix, scope, clock, hex_sha, out_dir):
    stim = capture.get("stimulus", {})
    if "clock_bpm" in stim:
        clock.set_clock(stim["clock_bpm"], stim.get("clock_ppqn", 1))
    if "reset" in stim:
        clock.arm_reset(stim["reset"])
    scope.configure(capture.get("scope", {}), capture.get("probes", {}))
    samples = []
    for _ in range(int(capture.get("repeat", 1))):
        clock.start()
        samples.append(scope.measure(capture.get("measure", [])))
        clock.stop()
    result = {
        "capture": capture,
        "firmware_sha256": hex_sha,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "scope": scope.identify(),
        "clock_source": clock.identify(),
        "samples": samples,
    }
    os.makedirs(out_dir, exist_ok=True)
    safe_id = capture["id"].replace("[", "_").replace("]", "").replace(" ", "_")
    path = os.path.join(out_dir, safe_id + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    return path


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--matrix", default=os.path.join(HERE, "captures.json"))
    ap.add_argument("--list", action="store_true", help="list capture ids and stop")
    ap.add_argument("--dry-run", action="store_true", help="print the plan for each capture; no hardware")
    ap.add_argument("--run", action="store_true", help="drive the scope and clock source (needs backends)")
    ap.add_argument("--only", default="", help="comma-separated capture ids")
    ap.add_argument("--hex", help="firmware hex under test; its sha256 goes into every result")
    ap.add_argument("--out", default="bench-results", help="result directory for --run")
    ap.add_argument("--scope-backend", default=os.environ.get("ER101_SCOPE_BACKEND", "none"))
    ap.add_argument("--clock-backend", default=os.environ.get("ER101_CLOCK_BACKEND", "none"))
    args = ap.parse_args(argv[1:])

    matrix = load_matrix(args.matrix)
    captures = [c for base in matrix["captures"] for c in expand(base)]
    if args.only:
        wanted = set(args.only.split(","))
        captures = [c for c in captures if c["id"] in wanted or c["id"].split("[")[0] in wanted]
        if not captures:
            print("bench: no capture matches --only %s" % args.only, file=sys.stderr)
            return 2

    if args.list or not (args.dry_run or args.run):
        for c in captures:
            print("%-40s %s" % (c["id"], c.get("group", "")))
        print("%d captures" % len(captures))
        return 0

    if args.dry_run:
        print("Dry run: %d captures from %s" % (len(captures), args.matrix))
        print("Scope: %s (%d channels). Clock: %s." % (
            matrix["scope"]["model"], matrix["scope"]["channels"], matrix["clock_source"]["name"]))
        if args.hex:
            print("Firmware under test: %s sha256 %s" % (args.hex, sha256_file(args.hex)))
        for c in captures:
            print(describe(c, matrix))
        print("Nothing was measured.")
        return 0

    # --run
    if not args.hex:
        print("bench: --run needs --hex so every result records the firmware under test", file=sys.stderr)
        return 2
    try:
        scope = backends.make_scope(args.scope_backend)
        clock = backends.make_clock(args.clock_backend)
    except backends.BackendUnavailable as e:
        print("bench: %s" % e, file=sys.stderr)
        return 1
    hex_sha = sha256_file(args.hex)
    for c in captures:
        print(describe(c, matrix))
        path = run_capture(c, matrix, scope, clock, hex_sha, args.out)
        print("   -> %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
