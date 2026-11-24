"""IETF Agent Audit Trail export (draft-sharif-agent-audit-trail-11)."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest

from actmirror import am, aat
from test_cli_exit_codes import _ENV


def _ledger(tmp_path):
    l = str(tmp_path / "l.jsonl")
    am.record(l, agent="jebi", action="eval_run", target="exp1.txt",
              content=b"result", payload={"script": "eval.py", "exit": 0})
    am.record(l, agent="jebi", action="file_write", target="x.py", payload={"exit": 2})
    am.record(l, agent="seara", action="msg")
    return l


def _read(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


# ─── RFC 8785 ────────────────────────────────────────────────

@pytest.mark.parametrize("x,want", [
    (1.0, "1"), (0.5, "0.5"), (1e21, "1e+21"), (1e20, "100000000000000000000"),
    (1e-7, "1e-7"), (1e-6, "0.000001"), (-2.5e-10, "-2.5e-10"), (5e-324, "5e-324"),
    (1.7976931348623157e308, "1.7976931348623157e+308"), (333333333.3333333, "333333333.3333333"),
    (0.0, "0"), (-0.0, "0"),
])
def test_jcs_numbers_follow_ecmascript(x, want):
    assert aat.jcs(x).decode() == want


def test_jcs_sorts_keys_by_utf16_code_units():
    # RFC 8785 §3.2.3: U+1F600 (surrogate D83D) sorts before U+FB33.
    out = aat.jcs({"דּ": 1, "\U0001F600": 2, "b": 3, "a": 4}).decode()
    assert out == '{"a":4,"b":3,"\U0001F600":2,"דּ":1}'


def test_jcs_rejects_nan_and_unsafe_ints():
    with pytest.raises(ValueError):
        aat.jcs(float("nan"))
    with pytest.raises(ValueError):
        aat.jcs(2 ** 60)


# ─── export ──────────────────────────────────────────────────

def test_export_has_required_fields_and_chain(tmp_path):
    recs = aat.to_aat(_ledger(tmp_path))
    assert len(recs) == 3
    for r in recs:
        for k in aat.REQUIRED:
            assert k in r
        assert r["action_detail"]["export_profile"]["draft"] == "draft-sharif-agent-audit-trail-11"
    assert recs[0]["prev_hash"] is None and recs[0]["parent_record_id"] is None
    for prev, cur in zip(recs, recs[1:]):
        expect = hashlib.sha256(aat.jcs(prev)).hexdigest()
        assert cur["prev_hash"] == expect
        assert cur["parent_record_id"] == prev["record_id"]
    assert len({r["session_id"] for r in recs}) == 1


def test_export_maps_outcome_and_hashes(tmp_path):
    recs = aat.to_aat(_ledger(tmp_path))
    assert [r["outcome"] for r in recs] == ["success", "failure", "success"]
    assert recs[0]["output_hash"] == hashlib.sha256(b"result").hexdigest()
    assert recs[0]["action_detail"]["tool_name"] == "eval_run"
    assert recs[0]["agent_id"] == "urn:action-mirror:agent:jebi"
    # native entry preserved for cross-checking
    assert recs[0]["action_detail"]["action_mirror"]["seal"] == am._load_entries(
        str(tmp_path / "l.jsonl"))[0]["seal"]


def test_export_is_deterministic(tmp_path):
    l = _ledger(tmp_path)
    assert aat.to_aat(l) == aat.to_aat(l)


def test_peer_witness_becomes_decision(tmp_path):
    a, b = str(tmp_path / "a.jsonl"), str(tmp_path / "b.jsonl")
    am.record(b, agent="b", action="x")
    am.witness_peer(a, b, peer_name="b")
    r = aat.to_aat(a)[0]
    assert r["action_type"] == "decision"
    assert r["action_detail"]["decision_type"] == "action_mirror.peer_witness"


def test_export_refuses_tampered_native_ledger(tmp_path):
    l = _ledger(tmp_path)
    lines = open(l, encoding="utf-8").read().splitlines()
    e = json.loads(lines[1]); e["target"] = "forged.py"; lines[1] = json.dumps(e)
    open(l, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    with pytest.raises(ValueError, match="refusing to export"):
        aat.to_aat(l)


def test_export_validates_arguments(tmp_path):
    l = _ledger(tmp_path)
    with pytest.raises(ValueError):
        aat.to_aat(l, trust_level="L9")
    with pytest.raises(ValueError):
        aat.to_aat(l, agent_version="v1")
    with pytest.raises(ValueError):
        aat.to_aat(l, session_id="not-a-uuid")
    with pytest.raises(ValueError):
        aat.to_aat(l, principal={"principal_type": "robot", "principal_id": "urn:x"})


def test_empty_ledger_exports_nothing(tmp_path):
    assert aat.to_aat(str(tmp_path / "none.jsonl")) == []


# ─── verify ──────────────────────────────────────────────────

def test_verify_accepts_export(tmp_path):
    out = str(tmp_path / "aat.jsonl")
    assert aat.export_aat(_ledger(tmp_path), out) == 3
    assert aat.verify_aat(out)[0].level == "OK"


def test_verify_detects_modified_record(tmp_path):
    out = str(tmp_path / "aat.jsonl")
    aat.export_aat(_ledger(tmp_path), out)
    recs = _read(out)
    recs[0]["outcome"] = "failure"
    with open(out, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r) + "\n" for r in recs)
    fs = aat.verify_aat(out)
    assert fs[0].level == "FAIL" and "prev_hash" in fs[0].msg


def test_verify_detects_deletion_and_reorder(tmp_path):
    out = str(tmp_path / "aat.jsonl")
    aat.export_aat(_ledger(tmp_path), out)
    recs = _read(out)
    for mutated in (recs[:1] + recs[2:], [recs[0], recs[2], recs[1]]):
        with open(out, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r) + "\n" for r in mutated)
        assert aat.verify_aat(out)[0].level == "FAIL"


def test_verify_rejects_bad_enum(tmp_path):
    out = str(tmp_path / "aat.jsonl")
    aat.export_aat(_ledger(tmp_path), out)
    recs = _read(out)
    recs[0]["action_type"] = "file_write"
    with open(out, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r) + "\n" for r in recs)
    assert aat.verify_aat(out)[0].level == "FAIL"


def test_verify_compares_timestamps_as_instants(tmp_path):
    out = str(tmp_path / "aat.jsonl")
    aat.export_aat(_ledger(tmp_path), out)
    recs = _read(out)[:1]
    with open(out, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r) + "\n" for r in recs)
    assert aat.verify_aat(out)[0].level == "OK"
    assert aat._instant("2026-01-01T00:00:00.5Z") > aat._instant("2026-01-01T00:00:00Z")
    assert aat._instant("2026-01-01T09:00:00+09:00") == aat._instant("2026-01-01T00:00:00Z")


# ─── CLI ─────────────────────────────────────────────────────

def _cli(*args):
    return subprocess.run([sys.executable, "-m", "actmirror.am", *args],
                          capture_output=True, encoding="utf-8", env=_ENV)


def test_cli_export_and_verify(tmp_path):
    l, out = _ledger(tmp_path), str(tmp_path / "aat.jsonl")
    r = _cli("--ledger", l, "export-aat", "--out", out,
             "--principal-type", "human", "--principal-id", "urn:user:operator")
    assert r.returncode == 0, r.stderr
    assert _read(out)[0]["principal"] == {"principal_type": "human",
                                          "principal_id": "urn:user:operator",
                                          "authority_ref": None}
    assert _cli("verify-aat", out).returncode == 0


def test_cli_export_refusal_exits_nonzero(tmp_path):
    l = _ledger(tmp_path)
    with open(l, "a", encoding="utf-8") as f:
        f.write("{not json\n")
    assert _cli("--ledger", l, "export-aat", "--out", str(tmp_path / "o.jsonl")).returncode == 1
