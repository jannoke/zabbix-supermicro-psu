"""Shared helpers for the Supermicro PSU Zabbix scripts.

Covers: shelling out to `ipmitool i2c`, parsing PMBus READ_EIN frames and
IPMI FRU Product Info areas, and the stateful rollover-aware energy counter
math plus its on-disk persistence.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

# --- PMBus / I2C constants -------------------------------------------------

PMBUS_CMD_READ_EIN = 0x86
PMBUS_CMD_STATUS_BYTE = 0x78

# (pmbus_addr, fru_addr) per bay, in bay order 1-4. This addressing is a
# convention shared across Supermicro PSU backplanes, not something to probe.
BAY_ADDRESSES = [
    (0x78, 0x70),
    (0x7A, 0x72),
    (0x7C, 0x74),
    (0x7E, 0x76),
]

READ_EIN_RESPONSE_LEN = 7
READ_EIN_BYTE_COUNT = 0x06

ACC_BITS = 24
ACC_MAX = 1 << ACC_BITS
ROLLOVER_BITS = 8
ROLLOVER_MAX = 1 << ROLLOVER_BITS

DEFAULT_IPMITOOL_TIMEOUT = 5.0
DEFAULT_LOCK_TIMEOUT = 2.0

STATE_DIR = os.environ.get("PSU_STATE_DIR", "/var/lib/zabbix-supermicro-psu/state")

IPMITOOL_BIN = shutil.which("ipmitool") or "ipmitool"


# --- Exceptions --------------------------------------------------------------

class PsuError(Exception):
    """Base class for all PSU-script errors."""


class PsuConfigError(PsuError):
    """Environment/configuration problem (missing binary, bad macro, ...)."""


class PsuI2CError(PsuError):
    """The I2C/PMBus transaction itself failed (NACK, timeout, non-zero exit)."""


class PsuFramingError(PsuError):
    """ipmitool returned data but it doesn't match the expected byte layout."""


class PsuLockError(PsuError):
    """Could not acquire the per-bay state file lock in time."""


# --- ipmitool i2c wrapper ----------------------------------------------------

def _fmt_addr(addr: int) -> str:
    return f"0x{addr:02x}"


def _fmt_byte(b: int) -> str:
    return f"0x{b:02x}"


def run_ipmitool_i2c(
    bus: int, addr: int, read_len: int, write_bytes, timeout: float = DEFAULT_IPMITOOL_TIMEOUT
):
    """Run `ipmitool i2c bus=<bus> <addr> <read_len> <write_bytes...>`.

    Returns the parsed response as a list of ints. Raises PsuConfigError if
    ipmitool itself is missing, PsuI2CError for any transaction failure
    (NACK, timeout, non-zero exit), PsuFramingError if the output can't be
    parsed as hex bytes at all.
    """
    cmd = [
        IPMITOOL_BIN, "i2c", f"bus={bus}", _fmt_addr(addr), str(read_len),
        *[_fmt_byte(b) for b in write_bytes],
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise PsuConfigError(f"ipmitool binary not found ({IPMITOOL_BIN!r})") from exc
    except subprocess.TimeoutExpired as exc:
        raise PsuI2CError(f"ipmitool timed out after {timeout}s: {' '.join(cmd)}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise PsuI2CError(f"ipmitool exited {proc.returncode}: {detail}")

    tokens = proc.stdout.split()
    try:
        return [int(tok, 16) for tok in tokens]
    except ValueError as exc:
        raise PsuFramingError(f"unparsable ipmitool output: {proc.stdout!r}") from exc


# --- READ_EIN parsing --------------------------------------------------------

@dataclass(frozen=True)
class EinReading:
    rollover: int
    accumulator: int
    sample_count: int


def parse_read_ein(raw_bytes) -> EinReading:
    """Parse a PMBus READ_EIN (0x86) block-read response.

    Expected layout (confirmed against real hardware captures): byte count
    (must be 0x06), 1-byte rollover count, 3-byte big-endian energy
    accumulator, 2-byte sample count. No PEC/CRC byte has been observed; if
    an 8-byte response with a trailing PEC shows up in the field, add that
    check here rather than changing the framing logic below.
    """
    if len(raw_bytes) != READ_EIN_RESPONSE_LEN:
        raise PsuFramingError(
            f"expected {READ_EIN_RESPONSE_LEN} bytes from READ_EIN, got {len(raw_bytes)}: {raw_bytes!r}"
        )
    byte_count = raw_bytes[0]
    if byte_count != READ_EIN_BYTE_COUNT:
        raise PsuFramingError(
            f"unexpected READ_EIN byte count 0x{byte_count:02x}, expected 0x{READ_EIN_BYTE_COUNT:02x}"
        )
    rollover = raw_bytes[1]
    accumulator = (raw_bytes[2] << 16) | (raw_bytes[3] << 8) | raw_bytes[4]
    sample_count = (raw_bytes[5] << 8) | raw_bytes[6]
    return EinReading(rollover=rollover, accumulator=accumulator, sample_count=sample_count)


def read_energy(bus: int, addr: int, timeout: float = DEFAULT_IPMITOOL_TIMEOUT) -> EinReading:
    raw = run_ipmitool_i2c(bus, addr, READ_EIN_RESPONSE_LEN, [PMBUS_CMD_READ_EIN], timeout=timeout)
    return parse_read_ein(raw)


def probe_present(bus: int, addr: int, timeout: float = DEFAULT_IPMITOOL_TIMEOUT) -> bool:
    """Cheap presence check: does this bay answer a READ_EIN probe at all?"""
    try:
        read_energy(bus, addr, timeout=timeout)
        return True
    except (PsuI2CError, PsuFramingError):
        return False


# --- Rollover-aware counter combination -------------------------------------

def combine_energy(prev_state: Optional[dict], reading: EinReading):
    """Combine a raw (rollover, accumulator) reading into an ever-increasing
    64-bit counter, given the previously persisted state (or None on first
    run). Returns (new_combined_counter, reset_event: bool).

    A "reset event" means the accumulator/rollover pair implies the PSU's
    onboard counter went backwards further than a single rollover wrap can
    explain (device replaced, or its accumulator was cleared some other
    way). We re-baseline by resuming forward from the current raw value so
    the counter we expose to Zabbix never decreases, at the cost of losing
    one delta window across the reset - an explicit, documented tradeoff.
    """
    if prev_state is None:
        combined = reading.rollover * ACC_MAX + reading.accumulator
        return combined, False

    last_rollover = prev_state["last_rollover"]
    last_accumulator = prev_state["last_accumulator"]
    prev_combined = prev_state["combined_counter"]

    raw_rollover_delta = reading.rollover - last_rollover
    if raw_rollover_delta < 0:
        raw_rollover_delta += ROLLOVER_MAX

    accumulator_delta = reading.accumulator - last_accumulator + raw_rollover_delta * ACC_MAX

    # A rollover delta over half the rollover byte's range is far more
    # likely to be a reset than that many genuine wraps happened between
    # two polls, given any sane polling interval.
    implausible = raw_rollover_delta > ROLLOVER_MAX // 2

    if accumulator_delta < 0 or implausible:
        new_combined = prev_combined + reading.accumulator
        return new_combined, True

    return prev_combined + accumulator_delta, False


# --- State persistence -------------------------------------------------------

def _state_path(bus: int, addr: int) -> str:
    return os.path.join(STATE_DIR, f"{bus}_0x{addr:02x}.json")


class StateLock:
    """Advisory lock on a per-bay state file, held for the read-modify-write
    cycle so overlapping invocations (stuck previous run, manual testing,
    multiple agent pollers) can't corrupt each other's state."""

    def __init__(self, path: str, timeout: float = DEFAULT_LOCK_TIMEOUT):
        self.lock_path = path + ".lock"
        self.timeout = timeout
        self._fd = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        self._fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o640)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    os.close(self._fd)
                    raise
                if time.monotonic() >= deadline:
                    os.close(self._fd)
                    raise PsuLockError(f"timed out locking {self.lock_path}")
                time.sleep(0.05)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
        return False


def load_state(bus: int, addr: int) -> Optional[dict]:
    path = _state_path(bus, addr)
    try:
        with open(path, "r") as f:
            data = json.load(f)
        for key in ("last_rollover", "last_accumulator", "combined_counter"):
            if key not in data:
                raise ValueError(f"missing key {key!r}")
        return data
    except FileNotFoundError:
        return None
    except (ValueError, json.JSONDecodeError):
        # Corrupt state is treated the same as "no state" - re-initialize
        # rather than crash the item. We favor availability of monitoring
        # over perfect continuity of one delta window.
        return None


def save_state(bus: int, addr: int, reading: EinReading, combined_counter: int) -> None:
    path = _state_path(bus, addr)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {
        "last_rollover": reading.rollover,
        "last_accumulator": reading.accumulator,
        "combined_counter": combined_counter,
        "last_update_epoch": int(time.time()),
    }
    tmp_path = f"{path}.tmp.{os.getpid()}"
    with open(tmp_path, "w") as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def update_energy_counter(bus: int, addr: int, reading: EinReading):
    """Load state, combine, persist - all under the per-bay lock. Returns
    (combined_counter, reset_event)."""
    with StateLock(_state_path(bus, addr)):
        prev_state = load_state(bus, addr)
        combined, reset_event = combine_energy(prev_state, reading)
        save_state(bus, addr, reading, combined)
        return combined, reset_event


# --- FRU Product Info parsing -------------------------------------------------

FRU_COMMON_HEADER_LEN = 8
FRU_TYPE_ASCII8 = 0b11
FRU_END_OF_FIELDS = 0xC1
_FRU_FIELD_NAMES = (
    "manufacturer",
    "product_name",
    "part_number",
    "version",
    "serial_number",
    "asset_tag",
)


def parse_product_info_area(area_bytes) -> dict:
    """Best-effort parse of an IPMI FRU Product Info Area. Returns a dict
    with whatever fields it managed to decode; never raises - a malformed
    or non-standard area just yields fewer/blank fields, since a PSU that
    fails FRU parsing should still be discovered as present."""
    if len(area_bytes) < 3:
        return {}
    idx = 3  # skip format version, area length, language code
    fields = {}
    try:
        for name in _FRU_FIELD_NAMES:
            if idx >= len(area_bytes):
                break
            type_length = area_bytes[idx]
            if type_length == FRU_END_OF_FIELDS:
                break
            length = type_length & 0x3F
            type_code = (type_length >> 6) & 0x3
            idx += 1
            raw = bytes(area_bytes[idx:idx + length])
            idx += length
            encoding = "ascii" if type_code == FRU_TYPE_ASCII8 else "latin1"
            fields[name] = raw.decode(encoding, errors="replace").strip()
    except Exception:
        return fields
    return fields


def read_fru_identity(bus: int, fru_addr: int, timeout: float = DEFAULT_IPMITOOL_TIMEOUT) -> Optional[dict]:
    """Best-effort read of a PSU's FRU EEPROM identity fields. Returns None
    if the FRU can't be read/parsed at all (caller should fall back to
    "unknown" rather than treat this as the PSU being absent)."""
    try:
        header = run_ipmitool_i2c(bus, fru_addr, FRU_COMMON_HEADER_LEN, [0x00], timeout=timeout)
    except (PsuI2CError, PsuFramingError):
        return None
    if len(header) != FRU_COMMON_HEADER_LEN:
        return None

    product_offset = header[4] * 8
    if product_offset == 0:
        return None

    try:
        area_header = run_ipmitool_i2c(bus, fru_addr, 2, [product_offset], timeout=timeout)
    except (PsuI2CError, PsuFramingError):
        return None
    if len(area_header) != 2:
        return None

    area_len = max(2, min(area_header[1] * 8, 64))
    try:
        area = run_ipmitool_i2c(bus, fru_addr, area_len, [product_offset], timeout=timeout)
    except (PsuI2CError, PsuFramingError):
        return None

    fields = parse_product_info_area(area)
    if not fields:
        return None
    return fields


# --- Misc --------------------------------------------------------------------

def parse_bus_list(buses_arg: str):
    parts = [p.strip() for p in buses_arg.split(",") if p.strip()]
    if not parts:
        raise ValueError("empty bus list")
    try:
        return [int(p, 0) for p in parts]
    except ValueError as exc:
        raise ValueError(f"invalid bus number in {buses_arg!r}") from exc
