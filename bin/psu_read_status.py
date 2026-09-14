#!/usr/bin/env python3
"""Zabbix UserParameter target: cheap presence/health probe for one PSU bay.

Usage: psu_read_status.py --bus <N> --addr <0xNN>

Prints 1 (present/responding) or 0 (absent/not responding) and always
exits 0 - a bay going quiet after being discovered (hot-swap removal, or a
transient I2C hiccup) is an expected, clean signal, not a script error.
Only a real configuration problem (e.g. ipmitool missing) is surfaced as
"not supported", since silently reporting 0 for that would hide a total
monitoring outage behind what looks like a routine PSU removal.
"""

import argparse
import sys

import psu_common as psu


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bus", type=lambda s: int(s, 0), required=True)
    parser.add_argument("--addr", type=lambda s: int(s, 0), required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        present = psu.probe_present(args.bus, args.addr)
    except psu.PsuConfigError as exc:
        print(f"psu_read_status: bus={args.bus} addr=0x{args.addr:02x}: {exc}", file=sys.stderr)
        return 1

    print(1 if present else 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
