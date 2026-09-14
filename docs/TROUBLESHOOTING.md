# Troubleshooting

## Discovery finds no PSU bays at all

- Confirm the I2C bus number: `ipmitool i2c bus=<N> 0x78 7 0x86` manually
  for a few candidate bus numbers (0-15 is a reasonable sweep range on most
  Supermicro boards) and look for a 7-byte response instead of an
  error/NACK. Once found, set `{$PSU.I2C.BUSES}` on the host to that value.
- Confirm `ipmitool` is installed and runnable by the user zabbix-agentd
  runs as: `sudo -u zabbix ipmitool i2c bus=3 0x78 7 0x86` (after running
  `packaging/install.sh`, this should work without `sudo -u` needing extra
  privileges beyond group membership).
- Check `/dev/ipmi0` permissions: `ls -l /dev/ipmi0` should show group
  `zbxipmi` (or whatever `packaging/install.sh` created) with rw for that
  group, and the `zabbix` user should be a member of it (`groups zabbix`).
  A recent group membership change requires restarting `zabbix-agentd` to
  take effect.

## Energy items go "not supported"

This means a genuine error, not an empty bay (empty bays simply aren't
discovered). Check `zabbix_agentd.log` or run the script by hand:

```sh
sudo -u zabbix /usr/lib/zabbix-supermicro-psu/psu_read_energy.py --bus 3 --addr 0x78
```

The stderr message identifies the failure class: missing `ipmitool`
binary, a permission/device-access problem, a state-file lock timeout
(likely a stuck previous invocation - check for other processes holding
`/var/lib/zabbix-supermicro-psu/state/*.lock`), or unexpected PMBus framing
(byte count other than `0x06`, or a length other than 7 bytes - see
`docs/ARCHITECTURE.md` "Open risks" if this happens on hardware not yet
validated).

## Sudoers-based privilege alternative

If your environment restricts udev rule changes or dedicated groups, grant
`zabbix` narrowly-scoped sudo rights instead, e.g. in
`/etc/sudoers.d/zabbix-supermicro-psu`:

```
zabbix ALL=(root) NOPASSWD: /usr/bin/ipmitool i2c bus=* * * *
```

and have the scripts invoke `sudo ipmitool ...` instead of `ipmitool ...`
directly (change `IPMITOOL_BIN` handling in `bin/psu_common.py`). This is
not the shipped default because a sudoers entry is a broader, harder to
audit grant than a udev device permission, but it is a reasonable fallback
where group/udev management isn't available.

## Values look implausible / jump around wildly

The energy accumulator's byte order is assumed big-endian and has not been
confirmed against a second real hardware capture (see
`docs/ARCHITECTURE.md` "Open risks"). If readings look wrong, capture two
`ipmitool i2c bus=<N> <addr> 7 0x86` outputs a known number of seconds
apart under roughly known load, and check whether treating the 3
accumulator bytes as little-endian instead produces a more plausible
(smaller, monotonic-looking) delta. If so, flip the byte order in
`parse_read_ein()` in `bin/psu_common.py`.
