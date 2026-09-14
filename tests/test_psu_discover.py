import json

import psu_common as psu
import psu_discover


def test_discover_no_bays_present(state_dir, monkeypatch):
    monkeypatch.setattr(psu, "probe_present", lambda bus, addr: False)
    assert psu_discover.discover([3]) == []


def test_discover_all_bays_present(state_dir, monkeypatch):
    monkeypatch.setattr(psu, "probe_present", lambda bus, addr: True)
    monkeypatch.setattr(psu, "read_fru_identity", lambda bus, addr: {
        "manufacturer": "SUPERMICRO", "product_name": "PWS-1K21P-1R", "serial_number": "S12345",
    })
    results = psu_discover.discover([3])
    assert len(results) == len(psu.BAY_ADDRESSES)
    bays = sorted(int(r["{#PSU.BAY}"]) for r in results)
    assert bays == [1, 2, 3, 4]
    for r in results:
        assert r["{#PSU.BUS}"] == "3"
        assert r["{#PSU.MODEL}"] == "PWS-1K21P-1R"
        assert r["{#PSU.SERIAL}"] == "S12345"
        assert r["{#PSU.VENDOR}"] == "SUPERMICRO"


def test_discover_partial_bays_only_present_ones_returned(state_dir, monkeypatch):
    present_addrs = {psu.BAY_ADDRESSES[0][0], psu.BAY_ADDRESSES[2][0]}
    monkeypatch.setattr(psu, "probe_present", lambda bus, addr: addr in present_addrs)
    monkeypatch.setattr(psu, "read_fru_identity", lambda bus, addr: {})
    results = psu_discover.discover([3])
    bays = sorted(int(r["{#PSU.BAY}"]) for r in results)
    assert bays == [1, 3]


def test_discover_falls_back_to_unknown_when_fru_fails(state_dir, monkeypatch):
    monkeypatch.setattr(psu, "probe_present", lambda bus, addr: True)
    monkeypatch.setattr(psu, "read_fru_identity", lambda bus, addr: None)
    results = psu_discover.discover([3])
    assert all(r["{#PSU.MODEL}"] == "unknown" for r in results)
    assert all(r["{#PSU.SERIAL}"] == "unknown" for r in results)


def test_discover_multiple_buses(state_dir, monkeypatch):
    monkeypatch.setattr(psu, "probe_present", lambda bus, addr: True)
    monkeypatch.setattr(psu, "read_fru_identity", lambda bus, addr: {})
    results = psu_discover.discover([1, 3])
    buses_seen = sorted(set(r["{#PSU.BUS}"] for r in results))
    assert buses_seen == ["1", "3"]
    assert len(results) == 2 * len(psu.BAY_ADDRESSES)


def test_discover_caches_successful_fru_reads_across_calls(state_dir, monkeypatch):
    call_count = {"n": 0}

    def fake_read_fru_identity(bus, addr):
        call_count["n"] += 1
        return {"manufacturer": "SUPERMICRO", "product_name": "PWS-X", "serial_number": "S1"}

    monkeypatch.setattr(psu, "probe_present", lambda bus, addr: True)
    monkeypatch.setattr(psu, "read_fru_identity", fake_read_fru_identity)

    psu_discover.discover([3])
    first_call_count = call_count["n"]
    psu_discover.discover([3])
    # Second discovery run should hit the FRU cache instead of re-reading.
    assert call_count["n"] == first_call_count


def test_discover_does_not_cache_failed_fru_reads(state_dir, monkeypatch):
    call_count = {"n": 0}

    def fake_read_fru_identity(bus, addr):
        call_count["n"] += 1
        return None

    monkeypatch.setattr(psu, "probe_present", lambda bus, addr: True)
    monkeypatch.setattr(psu, "read_fru_identity", fake_read_fru_identity)

    psu_discover.discover([3])
    first_call_count = call_count["n"]
    psu_discover.discover([3])
    # A failed FRU read is retried on the next discovery cycle, not cached.
    assert call_count["n"] > first_call_count


def test_main_emits_valid_lld_json_and_exits_zero(state_dir, monkeypatch, capsys):
    monkeypatch.setattr(psu, "probe_present", lambda bus, addr: False)
    monkeypatch.setattr("sys.argv", ["psu_discover.py", "3"])
    rc = psu_discover.main()
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"data": []}


def test_main_rejects_invalid_bus_list(state_dir, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["psu_discover.py", "not-a-bus"])
    rc = psu_discover.main()
    assert rc == 1
