"""The CLI's verdicts must reach the exit code.

Before 0.3.0 every path exited 0: `am verify && deploy` shipped a tampered
ledger — the 🔴 FAIL was print-only. Found live, not hypothetically: a
commit-binding tool trusted the exit code, and its own tamper demo (ledger
byte-flip) came back green.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from actmirror import am

# Make `-m actmirror.am` resolvable both from a repo-root pytest run and from
# the clean-wheel CI job (installed package; PYTHONPATH entry is then inert).
_ENV = {**os.environ,
        "PYTHONPATH": os.pathsep.join(filter(None, [
            os.path.dirname(os.path.dirname(os.path.abspath(am.__file__))),
            os.environ.get("PYTHONPATH", "")]))}


def cli(*args):
    return subprocess.run([sys.executable, "-m", "actmirror.am", *args],
                          capture_output=True, text=True, env=_ENV)


def _tamper_field(ledger: str, field: str, value):
    lines = [json.loads(x) for x in open(ledger, encoding="utf-8")]
    lines[0][field] = value
    with open(ledger, "w", encoding="utf-8") as f:
        for e in lines:
            f.write(json.dumps(e) + "\n")


def test_verify_intact_exits_0(tmp_path):
    led = str(tmp_path / "l.jsonl")
    am.record(led, agent="a", action="x")
    r = cli("--ledger", led, "verify")
    assert r.returncode == 0 and "OK" in r.stdout


def test_verify_tampered_exits_1(tmp_path):
    led = str(tmp_path / "l.jsonl")
    am.record(led, agent="a", action="x")
    am.record(led, agent="a", action="y")
    _tamper_field(led, "agent", "someone-else")
    r = cli("--ledger", led, "verify")
    assert "FAIL" in r.stdout          # the verdict was already right…
    assert r.returncode == 1           # …now the exit code agrees with it


def test_attest_attested_exits_0(tmp_path):
    led = str(tmp_path / "l.jsonl")
    am.record(led, agent="a", action="x", target="t")
    r = cli("--ledger", led, "attest", "--agent", "a", "--action", "x")
    assert r.returncode == 0 and "ATTESTED" in r.stdout


def test_attest_not_found_exits_1(tmp_path):
    led = str(tmp_path / "l.jsonl")
    am.record(led, agent="a", action="x")
    r = cli("--ledger", led, "attest", "--agent", "nobody")
    assert r.returncode == 1 and "NOT-FOUND" in r.stdout


def test_attest_content_mismatch_exits_1(tmp_path):
    led = str(tmp_path / "l.jsonl")
    blob = tmp_path / "artifact.bin"
    blob.write_bytes(b"honest bytes")
    am.record(led, agent="a", action="produced", target="artifact.bin",
              content=b"honest bytes")
    blob.write_bytes(b"swapped bytes")
    r = cli("--ledger", led, "attest", "--agent", "a",
            "--content-file", str(blob))
    assert r.returncode == 1 and "CONTENT-MISMATCH" in r.stdout


def test_verify_peer_rewrite_exits_1(tmp_path):
    mine, peer = str(tmp_path / "mine.jsonl"), str(tmp_path / "peer.jsonl")
    am.record(peer, agent="peer", action="x")
    r = cli("--ledger", mine, "witness", peer, "--name", "peer")
    assert r.returncode == 0
    os.remove(peer)                     # peer rewrites history from scratch
    am.record(peer, agent="peer", action="rewritten")
    r = cli("--ledger", mine, "verify-peer", peer, "--name", "peer")
    assert "FAIL" in r.stdout and r.returncode == 1


def test_record_still_exits_0(tmp_path):
    led = str(tmp_path / "l.jsonl")
    r = cli("--ledger", led, "record", "--agent", "a", "--action", "x")
    assert r.returncode == 0 and "Sealed" in r.stdout
