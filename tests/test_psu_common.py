import os

import pytest

import psu_common as psu


# --- READ_EIN framing --------------------------------------------------------

def test_parse_read_ein_real_capture():
    # ipmitool i2c bus=3 0x78 7 0x86  ->  06 ba 72 3b b8 74 71
    reading = psu.parse_read_ein([0x06, 0xBA, 0x72, 0x3B, 0xB8, 0x74, 0x71])
    assert reading.rollover == 0xBA
    assert reading.accumulator == 0x723BB8
    assert reading.sample_count == 0x7471


def test_parse_read_ein_wrong_length():
    with pytest.raises(psu.PsuFramingError):
        psu.parse_read_ein([0x06, 0xBA, 0x72])


def test_parse_read_ein_wrong_byte_count():
    with pytest.raises(psu.PsuFramingError):
        psu.parse_read_ein([0x05, 0xBA, 0x72, 0x3B, 0xB8, 0x74, 0x71])


# --- ipmitool wrapper ---------------------------------------------------------

def test_run_ipmitool_i2c_success(fake_ipmitool):
    fake_ipmitool({(3, 0x78, (0x86,)): "06 ba 72 3b b8 74 71\n"})
    result = psu.run_ipmitool_i2c(3, 0x78, 7, [0x86])
    assert result == [0x06, 0xBA, 0x72, 0x3B, 0xB8, 0x74, 0x71]


def test_run_ipmitool_i2c_nonzero_exit(fake_ipmitool):
    fake_ipmitool({(3, 0x78, (0x86,)): {"returncode": 1, "stderr": "NACK on write"}})
    with pytest.raises(psu.PsuI2CError):
        psu.run_ipmitool_i2c(3, 0x78, 7, [0x86])


def test_run_ipmitool_i2c_timeout(fake_ipmitool):
    fake_ipmitool({(3, 0x78, (0x86,)): {"timeout": True}})
    with pytest.raises(psu.PsuI2CError):
        psu.run_ipmitool_i2c(3, 0x78, 7, [0x86])


def test_run_ipmitool_i2c_missing_binary(fake_ipmitool):
    fake_ipmitool({(3, 0x78, (0x86,)): {"missing": True}})
    with pytest.raises(psu.PsuConfigError):
        psu.run_ipmitool_i2c(3, 0x78, 7, [0x86])


def test_run_ipmitool_i2c_unparsable_output(fake_ipmitool):
    fake_ipmitool({(3, 0x78, (0x86,)): "not hex at all\n"})
    with pytest.raises(psu.PsuFramingError):
        psu.run_ipmitool_i2c(3, 0x78, 7, [0x86])


def test_probe_present_true_and_false(fake_ipmitool):
    fake_ipmitool({
        (3, 0x78, (0x86,)): "06 ba 72 3b b8 74 71\n",
        (3, 0x7A, (0x86,)): {"returncode": 1, "stderr": "NACK"},
    })
    assert psu.probe_present(3, 0x78) is True
    assert psu.probe_present(3, 0x7A) is False


# --- Rollover-aware counter math ----------------------------------------------

def test_combine_energy_first_run_has_no_prior_state():
    reading = psu.EinReading(rollover=5, accumulator=1000, sample_count=1)
    combined, reset = psu.combine_energy(None, reading)
    assert combined == 5 * psu.ACC_MAX + 1000
    assert reset is False


def test_combine_energy_normal_increase_no_wrap():
    prev = {"last_rollover": 5, "last_accumulator": 1000, "combined_counter": 5 * psu.ACC_MAX + 1000}
    reading = psu.EinReading(rollover=5, accumulator=1500, sample_count=2)
    combined, reset = psu.combine_energy(prev, reading)
    assert combined == prev["combined_counter"] + 500
    assert reset is False


def test_combine_energy_accumulator_wraps_once():
    last_accumulator = psu.ACC_MAX - 16
    prev = {"last_rollover": 10, "last_accumulator": last_accumulator,
            "combined_counter": 10 * psu.ACC_MAX + last_accumulator}
    new_accumulator = 16  # wrapped around and continued for 16 more units
    reading = psu.EinReading(rollover=11, accumulator=new_accumulator, sample_count=3)
    combined, reset = psu.combine_energy(prev, reading)
    # true increase since last poll = (ACC_MAX - last_accumulator) + new_accumulator = 32
    assert combined == prev["combined_counter"] + 32
    assert reset is False


def test_combine_energy_rollover_byte_wraps():
    prev = {"last_rollover": 254, "last_accumulator": 100, "combined_counter": 254 * psu.ACC_MAX + 100}
    # rollover wrapped 254 -> 1 (i.e. advanced by 3: 254->255->0->1)
    reading = psu.EinReading(rollover=1, accumulator=50, sample_count=4)
    combined, reset = psu.combine_energy(prev, reading)
    raw_rollover_delta = (1 - 254) % psu.ROLLOVER_MAX
    assert raw_rollover_delta == 3
    expected_delta = 50 - 100 + 3 * psu.ACC_MAX
    assert combined == prev["combined_counter"] + expected_delta
    assert reset is False


def test_combine_energy_detects_reset_on_negative_delta():
    prev = {"last_rollover": 5, "last_accumulator": 500_000, "combined_counter": 5 * psu.ACC_MAX + 500_000}
    # PSU replaced: same rollover byte, accumulator dropped to near zero
    reading = psu.EinReading(rollover=5, accumulator=10, sample_count=0)
    combined, reset = psu.combine_energy(prev, reading)
    assert reset is True
    assert combined == prev["combined_counter"] + 10
    assert combined >= prev["combined_counter"]  # never decreases


def test_combine_energy_detects_reset_on_implausible_rollover_jump():
    prev = {"last_rollover": 5, "last_accumulator": 100, "combined_counter": 5 * psu.ACC_MAX + 100}
    reading = psu.EinReading(rollover=200, accumulator=50, sample_count=0)
    combined, reset = psu.combine_energy(prev, reading)
    assert reset is True
    assert combined == prev["combined_counter"] + 50
    assert combined >= prev["combined_counter"]


# --- State persistence --------------------------------------------------------

def test_state_roundtrip(state_dir):
    reading = psu.EinReading(rollover=1, accumulator=42, sample_count=0)
    psu.save_state(3, 0x78, reading, combined_counter=42)
    loaded = psu.load_state(3, 0x78)
    assert loaded["last_rollover"] == 1
    assert loaded["last_accumulator"] == 42
    assert loaded["combined_counter"] == 42


def test_state_missing_file_returns_none(state_dir):
    assert psu.load_state(3, 0x78) is None


def test_state_corrupt_file_treated_as_missing(state_dir):
    path = psu._state_path(3, 0x78)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("{not valid json")
    assert psu.load_state(3, 0x78) is None


def test_update_energy_counter_end_to_end(state_dir, fake_ipmitool):
    fake_ipmitool({(3, 0x78, (0x86,)): "06 ba 72 3b b8 74 71\n"})
    reading = psu.read_energy(3, 0x78)
    combined1, reset1 = psu.update_energy_counter(3, 0x78, reading)
    assert reset1 is False
    assert combined1 == reading.rollover * psu.ACC_MAX + reading.accumulator

    # Second poll, accumulator advanced.
    fake_ipmitool({(3, 0x78, (0x86,)): "06 2a 7a 3b c6 74 71\n"})
    reading2 = psu.read_energy(3, 0x78)
    combined2, reset2 = psu.update_energy_counter(3, 0x78, reading2)
    assert combined2 >= combined1


# --- Locking -------------------------------------------------------------------

def test_lock_blocks_concurrent_access(state_dir):
    path = psu._state_path(3, 0x78)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock1 = psu.StateLock(path, timeout=0.3)
    lock1.__enter__()
    try:
        lock2 = psu.StateLock(path, timeout=0.3)
        with pytest.raises(psu.PsuLockError):
            lock2.__enter__()
    finally:
        lock1.__exit__(None, None, None)


def test_lock_released_allows_next_acquire(state_dir):
    path = psu._state_path(3, 0x78)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with psu.StateLock(path, timeout=0.3):
        pass
    with psu.StateLock(path, timeout=0.3):
        pass


# --- Bus list parsing ----------------------------------------------------------

def test_parse_bus_list_single():
    assert psu.parse_bus_list("3") == [3]


def test_parse_bus_list_multi():
    assert psu.parse_bus_list(" 1, 3 ") == [1, 3]


def test_parse_bus_list_invalid():
    with pytest.raises(ValueError):
        psu.parse_bus_list("not-a-bus")


def test_parse_bus_list_empty():
    with pytest.raises(ValueError):
        psu.parse_bus_list("")
