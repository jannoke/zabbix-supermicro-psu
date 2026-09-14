# Supported hardware

## Confirmed

- I2C bus 3, PMBus addresses `0x78`/`0x7A` responding to `READ_EIN` (0x86)
  with a 7-byte block (`0x06` byte count + 6 data bytes, no PEC) - captured
  from a live Supermicro chassis. Exact PSU model/motherboard generation
  not yet recorded; add it here once confirmed.

## Assumed, pending confirmation

- PMBus addresses `0x78`/`0x7A`/`0x7C`/`0x7E` and paired FRU addresses
  `0x70`/`0x72`/`0x74`/`0x76` are expected to be consistent across
  Supermicro PSU backplanes generally, per vendor-wide addressing
  convention - not yet confirmed on more than one bus/chassis.
- I2C bus number is **not** assumed constant across chassis/motherboard
  generations. Bus 3 is the only value confirmed so far and is shipped as
  the template's default; other chassis will need `{$PSU.I2C.BUSES}` set
  explicitly until more data points are collected.

## How to add an entry

Once you've verified a chassis/motherboard model, add a row here:
motherboard/chassis model, I2C bus number, number of PSU bays, and any
FRU-parsing quirks observed (e.g. non-ASCII field encoding). This is the
main mechanism for `{$PSU.I2C.BUSES}` defaults to improve over time.
