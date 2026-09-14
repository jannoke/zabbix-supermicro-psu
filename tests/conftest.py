import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_DIR = os.path.join(REPO_ROOT, "bin")
if BIN_DIR not in sys.path:
    sys.path.insert(0, BIN_DIR)

import psu_common as psu  # noqa: E402


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    d = tmp_path / "state"
    d.mkdir()
    monkeypatch.setattr(psu, "STATE_DIR", str(d))
    return str(d)


class FakeIpmitool:
    """Stand-in for subprocess.run(["ipmitool", "i2c", ...]) driven by a
    dict mapping (bus, addr, tuple(write_bytes)) -> a canned result.

    A canned result is one of:
      - a string: treated as stdout, returncode 0
      - {"stdout": str, "returncode": int, "stderr": str}
      - {"timeout": True}
      - {"missing": True}  (raises FileNotFoundError, like ipmitool absent)
    """

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, cmd, capture_output, text, timeout):
        # cmd == [ipmitool, "i2c", "bus=<n>", "<addr>", "<len>", <write bytes...>]
        bus = int(cmd[2].split("=", 1)[1])
        addr = int(cmd[3], 16)
        write_bytes = tuple(int(tok, 16) for tok in cmd[5:])
        key = (bus, addr, write_bytes)
        self.calls.append(key)

        if key not in self.responses:
            raise AssertionError(f"no canned ipmitool response for {key}")
        result = self.responses[key]

        if isinstance(result, str):
            result = {"stdout": result}
        if result.get("missing"):
            raise FileNotFoundError("ipmitool")
        if result.get("timeout"):
            raise subprocess.TimeoutExpired(cmd, timeout)

        return subprocess.CompletedProcess(
            cmd,
            result.get("returncode", 0),
            stdout=result.get("stdout", ""),
            stderr=result.get("stderr", ""),
        )


@pytest.fixture
def fake_ipmitool(monkeypatch):
    def _install(responses):
        fake = FakeIpmitool(responses)
        monkeypatch.setattr(psu.subprocess, "run", fake)
        return fake
    return _install
