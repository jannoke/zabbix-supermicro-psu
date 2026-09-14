# Changelog

## Unreleased

- Initial implementation: PSU bay discovery (LLD), rollover-aware energy
  counter reader, presence/status check, Zabbix template, and packaging
  for RPM/deb-based installs.
- Not yet verified against real Supermicro hardware - see
  `docs/ARCHITECTURE.md` "Open risks" (accumulator byte order in
  particular).
