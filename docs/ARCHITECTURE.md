# Architecture

## Why a counter, not instantaneous power

PMBus's `READ_EIN` (0x86) command exposes a hardware energy *accumulator*
plus a rollover count, rather than an instantaneous reading like
`READ_PIN`/`READ_POUT`. Total energy usage over time is exactly what an
accumulator is for, and it maps directly onto Zabbix's native counter/delta
item semantics - so this project reads that accumulator on every poll
instead of polling instantaneous power and integrating client-side.

## The rollover problem

The accumulator is 24-bit (wraps at 2^24) and the device tracks that
wraparound itself in a 1-byte rollover counter (which itself wraps at 256).
Zabbix's built-in counter/delta preprocessing only understands simple
wraparound of the item's own value - it has no way to see the separate
rollover byte. So a stateful local helper (`psu_read_energy.py` +
`psu_common.combine_energy`) linearizes `(rollover, accumulator)` into a
single ever-increasing 64-bit value on the monitored host, before the value
ever reaches Zabbix. Zabbix then does plain `change_per_second` on that
already-linearized number - exactly what it's good at.

`combine_energy()` also detects "device reset" events: if the accumulator
went backward further than one rollover wrap can explain (PSU replaced, or
its accumulator cleared some other way), it re-baselines by resuming
forward from the current raw value, so the exposed counter never decreases.
This costs one lost delta window across the reset - an explicit, documented
tradeoff, not a bug.

State persists to `/var/lib/zabbix-supermicro-psu/state/<bus>_<addr>.json`,
guarded by an `flock`-based lock (`psu_common.StateLock`) for the
read-modify-write cycle. Living in `/var/lib` (not `/tmp`/`/run`) means the
design survives reboots and agent restarts with zero special-casing: state
is keyed purely on last-seen raw values, not wall-clock time or poll count.

## Discovery

`psu_discover.py` probes every `(bus, bay)` combination from the
comma-separated `{$PSU.I2C.BUSES}` host macro against the fixed, confirmed
bay→address table:

| Bay | PMBus addr | FRU addr |
|-----|-----------|----------|
| 1   | 0x78      | 0x70     |
| 2   | 0x7A      | 0x72     |
| 3   | 0x7C      | 0x74     |
| 4   | 0x7E      | 0x76     |

A `READ_EIN` probe doubles as the presence check - a NACK/error means the
bay is empty or absent, and it's simply omitted from the LLD JSON (never an
error; empty bays are the common case). A successful probe triggers a
best-effort FRU EEPROM read (IPMI FRU common header → Product Info Area) to
populate `{#PSU.MODEL}`/`{#PSU.SERIAL}`/`{#PSU.VENDOR}`; a failed or
unparsable FRU read never suppresses discovery of an otherwise-present
PSU - it just falls back to `"unknown"`. Successful FRU reads are cached
(`fru_cache.json`, identity rarely changes); failed reads are *not* cached,
so a PSU whose FRU couldn't be parsed keeps getting retried on later
discovery cycles.

There is deliberately no blind auto-probing across every possible I2C bus
number - that risks touching unrelated devices on shared buses. Bus number
is a manual-but-documented per-chassis setting via `{$PSU.I2C.BUSES}`.

## Error handling philosophy

- **Empty bay at discovery time** → silently omitted. Not an error.
- **Bay removed after discovery** → the status item cleanly returns `0` (a
  valid, supported value) rather than going "not supported" - avoids noisy
  Zabbix item-state flags for routine PSU hot-swaps.
- **Genuine failure** (missing `ipmitool`, permission denied, lock timeout,
  unexpected output shape) → non-zero exit, surfaces as Zabbix "not
  supported" - the right visibility level for a real config/environment
  problem, distinct from "PSU bay is just empty."
- No internal retries anywhere in the scripts - Zabbix's own poll interval
  is the retry mechanism, and the read-failure trigger's consecutive-poll
  condition absorbs single-poll transient NACKs.

## Privilege model

`ipmitool i2c` needs `/dev/ipmi0` access; `zabbix-agentd` runs
unprivileged. `packaging/install.sh` creates a dedicated `zbxipmi` group, a
udev rule granting that group rw on `/dev/ipmi0`, and adds the `zabbix`
user to it - the standard least-privilege pattern (narrower blast radius
than sudoers, no setuid binary). See `docs/TROUBLESHOOTING.md` for a
sudoers-based alternative for environments that restrict udev/group
changes.

## Open risks

- **Accumulator byte order is assumed big-endian**, based on typical PMBus
  block-read convention - not yet confirmed against a second real
  before/after capture. `parse_read_ein()` in `psu_common.py` isolates this
  so flipping endianness is a one-line change if a real deployment shows
  implausible (e.g. wildly jumping) values.
- **No PEC/CRC byte observed** in the 7-byte responses captured so far
  (count + 6 data bytes). The length-validation step in `parse_read_ein()`
  is written so accepting an 8th PEC byte later is additive, not a
  rewrite.
- **FRU layout may not be byte-identical across Supermicro generations** -
  `parse_product_info_area()` is defensive and degrades to "unknown"
  fields rather than raising.
- **No Wh/J unit conversion in v1** - the exposed counter is an opaque
  monotonic accumulator. Converting to real energy units needs the PMBus
  scaling exponent (`VOUT_MODE` or a manufacturer-specific linear format),
  deferred to a future version.
- All `ipmitool` invocations use explicit subprocess timeouts (5s default)
  well under typical Zabbix item/agent timeouts, since discovery's
  multi-call fan-out (`buses × 4 bays × (PMBus + FRU)`) needs bounded total
  runtime.

## Real-hardware verification checklist

Once chassis access is available:

1. Run the raw `ipmitool i2c` commands directly to confirm bay
   addresses/framing, and specifically confirm the accumulator's byte
   order by comparing two readings taken a known time apart under a
   roughly known load.
2. Run `bin/psu_discover.py "<bus>"` as root and cross-check the JSON
   output against physically-known populated bays.
3. Run `bin/psu_read_energy.py --bus <n> --addr <addr>` twice, seconds
   apart, and sanity-check the counter's rate of increase against
   approximate wattage × elapsed seconds.
4. On a redundant chassis, pull and reinsert a PSU to exercise the
   discovery lifecycle and the reset/re-baseline path end-to-end.
5. Install via `packaging/install.sh`, attach the template to a real
   Zabbix host, and confirm the full LLD → items → preprocessing →
   triggers pipeline in the Zabbix frontend.
