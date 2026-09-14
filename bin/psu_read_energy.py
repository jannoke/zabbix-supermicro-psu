#!/usr/bin/env python3
"""Zabbix UserParameter target: prints a PSU bay's linearized, ever-increasing
energy counter as a single integer.

Usage: psu_read_energy.py --bus <N> --addr <0xNN>

Exit 0 with the counter value on stdout on success. Exit non-zero (Zabbix
"not supported") on any communication/config/framing/locking failure - a
bay that's simply empty is handled by psu_discover.py not creating this
item in the first place, not by this script returning a sentinel value.
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
        reading = psu.read_energy(args.bus, args.addr)
        combined, reset_event = psu.update_energy_counter(args.bus, args.addr, reading)
    except psu.PsuError as exc:
        print(f"psu_read_energy: bus={args.bus} addr=0x{args.addr:02x}: {exc}", file=sys.stderr)
        return 1

    if reset_event:
        print(
            f"psu_read_energy: bus={args.bus} addr=0x{args.addr:02x}: "
            "detected accumulator reset, re-baselined counter",
            file=sys.stderr,
        )

    print(combined)
    return 0


if __name__ == "__main__":
    sys.exit(main())
