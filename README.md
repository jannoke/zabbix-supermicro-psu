# zabbix-supermicro-psu

Zabbix monitoring for Supermicro server PSUs' **cumulative energy usage**,
read over PMBus/I2C via the host's local BMC (`ipmitool i2c`) - not just
instantaneous power draw.

## How it works

Supermicro PSU backplanes expose PMBus at I2C addresses `0x78/0x7A/0x7C/0x7E`
(one per bay) and FRU identification EEPROMs at the paired addresses
`0x70/0x72/0x74/0x76`. This project:

1. Discovers which bays are actually populated (Zabbix LLD), on whichever
   I2C bus number your chassis uses.
2. Reads each populated bay's PMBus `READ_EIN` (0x86) energy accumulator on
   every poll, linearizes it (handling the accumulator's own 24-bit
   rollover and the device's 8-bit rollover counter) into a single
   ever-increasing counter, and exposes that to Zabbix.
3. Lets Zabbix's own delta/`change_per_second` preprocessing turn that
   counter into a usable rate - no PMBus unit-scaling/exponent conversion
   is done in v1, so the counter is an opaque monotonic accumulator, not
   Watt-hours (yet).

See `docs/ARCHITECTURE.md` for the full design and the known open risks
(byte-order assumption, FRU layout variance, etc).

## Requirements

- A Supermicro chassis whose PSU backplane is reachable via `ipmitool i2c`
  from the host OS (local BMC access, not a network IPMI target).
- Zabbix Agent (not agent-less) running on that host, since the scripts
  need local access to `/dev/ipmi0`.
- Python 3 (stdlib only, no extra dependencies) on the monitored host.

## Install

```sh
sudo packaging/install.sh
```

This installs the scripts to `/usr/lib/zabbix-supermicro-psu/`, the
UserParameter config to `/etc/zabbix/zabbix_agentd.d/`, creates the
`/var/lib/zabbix-supermicro-psu/state` directory, and sets up a udev rule
+ dedicated group so the unprivileged `zabbix` user can access
`/dev/ipmi0` without sudo or a setuid wrapper. See
`docs/TROUBLESHOOTING.md` for a sudoers-based alternative.

Then restart `zabbix-agentd`, import
`zabbix/templates/template_supermicro_psu.yaml` into Zabbix, and attach it
to the host. If the chassis doesn't use I2C bus 3 for the PSU backplane,
set the `{$PSU.I2C.BUSES}` host macro accordingly.

## Testing

```sh
python3 -m venv .venv && .venv/bin/pip install pytest flake8
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m flake8 bin/ tests/
```

All tests mock `ipmitool` - no hardware or root access needed to run the
suite. See `docs/ARCHITECTURE.md` for the real-hardware verification
checklist to run once you have chassis access.

## Status

Early / unverified against real hardware. The PMBus framing and addressing
are based on a real capture and the user's cross-product knowledge of
Supermicro's addressing conventions, but the accumulator's byte order in
particular has not yet been confirmed against a second real before/after
reading. See `docs/ARCHITECTURE.md` "Open risks."
