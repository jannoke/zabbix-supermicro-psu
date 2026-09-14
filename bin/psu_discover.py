#!/usr/bin/env python3
"""Zabbix LLD discovery script for Supermicro PSU bays.

Usage: psu_discover.py "<comma-separated bus list>"
(invoked by Zabbix as supermicro.psu.discovery[{$PSU.I2C.BUSES}])

Probes every (bus, bay) combination for a PMBus response; a bay that NACKs
or errors is simply omitted from the output - that's the common, expected
case for an empty/absent PSU slot, not an error. Only real configuration
problems (missing ipmitool, an unparsable bus list) produce a non-zero
exit; "nothing found" is a normal, successful `{"data": []}` result.
"""

import json
import os
import sys

import psu_common as psu


def _fru_cache_path() -> str:
    # Computed on each call (not a module-level constant) so it tracks
    # psu.STATE_DIR even if that's overridden after import - as tests do.
    return os.path.join(psu.STATE_DIR, "fru_cache.json")


def _load_fru_cache() -> dict:
    try:
        with open(_fru_cache_path(), "r") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _save_fru_cache(cache: dict) -> None:
    path = _fru_cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp.{os.getpid()}"
    with open(tmp_path, "w") as f:
        json.dump(cache, f)
    os.replace(tmp_path, path)


def _fru_identity(cache: dict, bus: int, fru_addr: int) -> dict:
    """FRU identity rarely changes once a PSU is seated, so a successful
    read is cached indefinitely; a failed/unparsable read is NOT cached,
    so future discovery cycles keep retrying it for an otherwise-present
    PSU rather than getting stuck reporting "unknown" forever."""
    key = f"{bus}_0x{fru_addr:02x}"
    if key in cache:
        return cache[key]
    identity = psu.read_fru_identity(bus, fru_addr)
    if identity:
        cache[key] = identity
        return identity
    return {}


def discover(buses) -> list:
    cache = _load_fru_cache()
    cache_dirty = False
    results = []

    for bus in buses:
        for bay_index, (pmbus_addr, fru_addr) in enumerate(psu.BAY_ADDRESSES, start=1):
            if not psu.probe_present(bus, pmbus_addr):
                continue

            before = len(cache)
            identity = _fru_identity(cache, bus, fru_addr)
            cache_dirty = cache_dirty or len(cache) != before

            results.append({
                "{#PSU.BUS}": str(bus),
                "{#PSU.BAY}": str(bay_index),
                "{#PSU.PMBUS.ADDR}": f"0x{pmbus_addr:02x}",
                "{#PSU.FRU.ADDR}": f"0x{fru_addr:02x}",
                "{#PSU.MODEL}": identity.get("product_name", "unknown"),
                "{#PSU.SERIAL}": identity.get("serial_number", "unknown"),
                "{#PSU.VENDOR}": identity.get("manufacturer", "unknown"),
            })

    if cache_dirty:
        _save_fru_cache(cache)

    return results


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: psu_discover.py <comma-separated bus list>", file=sys.stderr)
        return 1

    try:
        buses = psu.parse_bus_list(sys.argv[1])
    except ValueError as exc:
        print(f"psu_discover: {exc}", file=sys.stderr)
        return 1

    try:
        data = discover(buses)
    except psu.PsuConfigError as exc:
        print(f"psu_discover: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"data": data}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
