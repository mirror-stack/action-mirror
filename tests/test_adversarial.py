"""Adversarial tests for action-mirror.

The tool's guarantee is "who did what, provably". Each test here plays a
motivated attacker: build an honest ledger, perform a concrete attack, and
assert that verification returns the *exact* Finding that exposes it.

Scenarios whose whole point is a documented limitation (tail truncation,
wholesale replacement, last-entry reseal) assert BOTH sides of the truth:
self verify_chain passes (append-only chains cannot see their own missing
tail / fresh rebuild) AND the peer-witness pin fails. That asymmetry is the
design's honest boundary, and these tests freeze it as executable doc.
"""
from __future__ import annotations
import hashlib, json, os
from actmirror import am


def L(tmp_path, name="l.jsonl"):
    return str(tmp_path / name)


# ─── low-level attacker toolkit ──────────────────────────────

def _read_lines(path):
    return open(path).read().splitlines()


def _write_lines(path, lines):
    open(path, "w").write("\n".join(lines) + "\n")


def _seal_of(entry: dict) -> str:
    """Recompute a full-length seal exactly like the sealer does."""
    body = {k: v for k, v in entry.items() if k not in ("seal", "sig")}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _forge_entry(prev_seal: str, **fields) -> str:
    """Build a self-consistent forged entry (correct prev_seal + correct
    seal for its own content) — the strongest lone-line forgery possible."""
    entry = {"_type": "action", "ts": "2026-07-21T00:00:00Z", **fields}
    entry["prev_seal"] = prev_seal
    entry["seal"] = _seal_of(entry)
    return json.dumps(entry, ensure_ascii=False)


def _legacy_record(ledger_path: str, **fields) -> dict:
    """Append a pre-v0.6 style entry: seal truncated to 16 hex chars.
    Built directly (the current sealer only writes full digests)."""
    entry = {"_type": "action", "ts": "2026-01-01T00:00:00Z", **fields}
    entry["prev_seal"] = am._get_last_seal(ledger_path)
    entry["seal"] = _seal_of(entry)[: am._LEGACY_HASH_LEN]
    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _chain3(l):
    """Honest 3-entry chain."""
    return [am.record(l, agent="jebi", action=f"step{i}", target=f"t{i}")
            for i in range(3)]


# ─── 1. delete a middle entry ────────────────────────────────

def test_attack_delete_middle_entry(tmp_path):
    """Drop entry 2 of 3 → entry 3's prev_seal no longer links to entry 1."""
    l = L(tmp_path)
    _chain3(l)
    lines = _read_lines(l)
    _write_lines(l, [lines[0], lines[2]])           # entry 2 vanishes
    fs = am.verify_chain(l)
    assert fs[0].level == "FAIL"
    assert "prev_seal broken" in fs[0].msg
    assert "Entry 2" in fs[0].msg                    # localized to the gap


# ─── 2. insert a forged entry in the middle ──────────────────

def test_attack_insert_forged_middle_entry(tmp_path):
    """Attacker inserts a *self-consistent* forgery (valid own seal, valid
    prev_seal pointing at entry 2). The next honest entry still points at
    entry 2's seal, not the forgery's → the chain breaks right after it."""
    l = L(tmp_path)
    e = _chain3(l)
    forged = _forge_entry(e[1]["seal"], agent="jebi", action="innocent_looking")
    lines = _read_lines(l)
    _write_lines(l, [lines[0], lines[1], forged, lines[2]])
    fs = am.verify_chain(l)
    assert fs[0].level == "FAIL"
    assert "prev_seal broken" in fs[0].msg
    assert "Entry 4" in fs[0].msg                    # break lands on the entry AFTER the forgery


# ─── 3. tail truncation — chain blind, witness catches ───────

def test_attack_tail_truncation_needs_witness(tmp_path):
    """Delete the last N entries. A hash chain is a valid chain at every
    prefix, so self-verification CANNOT see a missing tail (honest limit of
    append-only chains — documented, not a bug). The peer's position-pinned
    head is what makes the rollback permanent evidence."""
    me, peer = L(tmp_path, "me.jsonl"), L(tmp_path, "peer.jsonl")
    for i in range(4):
        am.record(me, agent="jebi", action=f"s{i}")
    am.witness_peer(peer, me, peer_name="jebi")      # peer pins 4 entries
    _write_lines(me, _read_lines(me)[:2])            # drop last 2 entries
    # honest limit: my own chain still verifies OK
    assert am.verify_chain(me)[0].level == "OK"
    # witness pin: exact TRUNCATED finding
    f = am.verify_peer(peer, me, peer_name="jebi")
    assert f.level == "FAIL"
    assert "TRUNCATED" in f.msg
    assert "witnessed 4" in f.msg and "now only 2" in f.msg


# ─── 4. wholesale ledger replacement ─────────────────────────

def test_attack_wholesale_replacement_only_witness_catches(tmp_path):
    """Replace the whole ledger with a freshly built, internally valid chain.
    Self-verification passes by construction (the new chain IS valid) — this
    blind spot is the tool's raison d'être: only the peer's pinned head,
    living in another trust domain, proves the history was swapped."""
    me, peer = L(tmp_path, "me.jsonl"), L(tmp_path, "peer.jsonl")
    am.record(me, agent="jebi", action="embarrassing_result", target="exp1")
    am.record(me, agent="jebi", action="kill_verdict", target="exp1")
    am.witness_peer(peer, me, peer_name="jebi")
    os.remove(me)                                    # rebuild from scratch
    am.record(me, agent="jebi", action="clean_history", target="exp1")
    am.record(me, agent="jebi", action="pass_verdict", target="exp1")
    # honest limit: the replacement chain is self-consistent
    assert am.verify_chain(me)[0].level == "OK"
    # same length as before, so not TRUNCATED — must be the REWRITTEN finding
    f = am.verify_peer(peer, me, peer_name="jebi")
    assert f.level == "FAIL"
    assert "REWRITTEN" in f.msg


# ─── 5. modify a middle entry's payload (seal kept) ──────────

def test_attack_modify_payload_keep_seal(tmp_path):
    """Change one payload field of a middle entry, keep the old seal."""
    l = L(tmp_path)
    am.record(l, agent="jebi", action="eval", payload={"acc": 0.52})
    am.record(l, agent="jebi", action="eval", payload={"acc": 0.55})
    am.record(l, agent="jebi", action="report")
    lines = _read_lines(l)
    row = json.loads(lines[1])
    row["payload"]["acc"] = 0.95                     # doctor the number
    lines[1] = json.dumps(row, ensure_ascii=False)
    _write_lines(l, lines)
    fs = am.verify_chain(l)
    assert fs[0].level == "FAIL"
    assert "seal mismatch" in fs[0].msg
    assert "Entry 2" in fs[0].msg


# ─── 6. modify + reseal consistently ─────────────────────────

def test_attack_reseal_middle_entry_breaks_next_link(tmp_path):
    """Smarter attacker: modify entry 2 AND recompute its seal so the entry
    is self-consistent. The next entry's prev_seal still stores the OLD seal
    → the chain breaks at entry 3, not 2."""
    l = L(tmp_path)
    am.record(l, agent="jebi", action="eval", payload={"acc": 0.52})
    am.record(l, agent="jebi", action="eval", payload={"acc": 0.55})
    am.record(l, agent="jebi", action="report")
    lines = _read_lines(l)
    row = json.loads(lines[1])
    row["payload"]["acc"] = 0.95
    row["seal"] = _seal_of(row)                      # reseal consistently
    lines[1] = json.dumps(row, ensure_ascii=False)
    _write_lines(l, lines)
    fs = am.verify_chain(l)
    assert fs[0].level == "FAIL"
    assert "prev_seal broken" in fs[0].msg
    assert "Entry 3" in fs[0].msg


def test_attack_reseal_last_entry_only_witness_catches(tmp_path):
    """Modify + reseal the LAST entry: no successor stores its old seal, so
    self-verification passes — the honest limit of a chain with no entry
    after the head. The peer's pinned head (position N, old seal) is the
    only durable evidence, and it flags exactly REWRITTEN."""
    me, peer = L(tmp_path, "me.jsonl"), L(tmp_path, "peer.jsonl")
    am.record(me, agent="jebi", action="eval", payload={"acc": 0.52})
    am.record(me, agent="jebi", action="verdict", payload={"result": "KILL"})
    am.witness_peer(peer, me, peer_name="jebi")
    lines = _read_lines(me)
    row = json.loads(lines[-1])
    row["payload"]["result"] = "PASS"                # flip the verdict
    row["seal"] = _seal_of(row)
    lines[-1] = json.dumps(row, ensure_ascii=False)
    _write_lines(me, lines)
    # honest limit: chain has no later link that remembers the old head seal
    assert am.verify_chain(me)[0].level == "OK"
    f = am.verify_peer(peer, me, peer_name="jebi")
    assert f.level == "FAIL"
    assert "REWRITTEN" in f.msg


# ─── 7. legacy (16-hex) / new mixed chains ───────────────────

def _mixed_chain(l):
    """2 legacy truncated-seal entries followed by 2 modern full-seal ones."""
    e1 = _legacy_record(l, agent="jebi", action="old0", target="t0")
    e2 = _legacy_record(l, agent="jebi", action="old1", target="t1")
    e3 = am.record(l, agent="jebi", action="new0", target="t2")
    e4 = am.record(l, agent="jebi", action="new1", target="t3")
    return [e1, e2, e3, e4]


def test_legacy_mixed_chain_verifies_clean(tmp_path):
    """Sanity: an untampered legacy+new mixed chain must verify OK,
    otherwise the attack tests below would fail for the wrong reason."""
    l = L(tmp_path)
    e = _mixed_chain(l)
    assert len(e[0]["seal"]) == am._LEGACY_HASH_LEN
    assert len(e[2]["seal"]) == 64
    fs = am.verify_chain(l)
    assert fs[0].level == "OK"
    assert "4" in fs[0].msg


def test_attack_modify_legacy_entry_keep_seal(tmp_path):
    """Tampering inside the legacy segment: prefix matching still binds the
    truncated seal to the content → seal mismatch at the legacy entry."""
    l = L(tmp_path)
    _mixed_chain(l)
    lines = _read_lines(l)
    row = json.loads(lines[0])
    row["target"] = "doctored"
    lines[0] = json.dumps(row, ensure_ascii=False)
    _write_lines(l, lines)
    fs = am.verify_chain(l)
    assert fs[0].level == "FAIL"
    assert "seal mismatch" in fs[0].msg
    assert "Entry 1" in fs[0].msg


def test_attack_delete_legacy_entry(tmp_path):
    """Deleting a legacy entry breaks the truncated prev_seal link."""
    l = L(tmp_path)
    _mixed_chain(l)
    lines = _read_lines(l)
    _write_lines(l, [lines[0]] + lines[2:])          # drop legacy entry 2
    fs = am.verify_chain(l)
    assert fs[0].level == "FAIL"
    assert "prev_seal broken" in fs[0].msg


def test_attack_insert_forged_legacy_style_entry(tmp_path):
    """Forgery inserted at the legacy/new boundary — even a truncated-seal
    forgery that is self-consistent breaks the next entry's prev_seal."""
    l = L(tmp_path)
    e = _mixed_chain(l)
    forged_entry = {"_type": "action", "ts": "2026-01-01T00:00:00Z",
                    "agent": "jebi", "action": "forged_old",
                    "prev_seal": e[1]["seal"]}
    forged_entry["seal"] = _seal_of(forged_entry)[: am._LEGACY_HASH_LEN]
    lines = _read_lines(l)
    _write_lines(l, lines[:2] + [json.dumps(forged_entry, ensure_ascii=False)]
                 + lines[2:])
    fs = am.verify_chain(l)
    assert fs[0].level == "FAIL"
    assert "prev_seal broken" in fs[0].msg
    assert "Entry 4" in fs[0].msg


def test_attack_reseal_legacy_entry_breaks_next_link(tmp_path):
    """Modify + consistently reseal a legacy entry — the successor's stored
    truncated prev_seal betrays it, same as in the modern segment."""
    l = L(tmp_path)
    _mixed_chain(l)
    lines = _read_lines(l)
    row = json.loads(lines[0])
    row["target"] = "doctored"
    row["seal"] = _seal_of(row)[: am._LEGACY_HASH_LEN]
    lines[0] = json.dumps(row, ensure_ascii=False)
    _write_lines(l, lines)
    fs = am.verify_chain(l)
    assert fs[0].level == "FAIL"
    assert "prev_seal broken" in fs[0].msg
    assert "Entry 2" in fs[0].msg


# ─── 8. content_hash forgery ─────────────────────────────────

def test_attack_present_different_content_after_sealing(tmp_path):
    """Seal an artifact's hash, then try to pass off different bytes as the
    sealed artifact → CONTENT-MISMATCH, never ATTESTED."""
    l = L(tmp_path)
    am.record(l, agent="jebi", action="file_write", target="results.json",
              content=b'{"acc": 0.52}')
    res = am.attest(l, agent="jebi", target="results.json",
                    content=b'{"acc": 0.95}')        # doctored artifact
    assert res["verdict"] == "CONTENT-MISMATCH"
    assert "modified" in res["note"]
    # and the genuine bytes still attest, proving the mismatch is specific
    ok = am.attest(l, agent="jebi", target="results.json",
                   content=b'{"acc": 0.52}')
    assert ok["verdict"] == "ATTESTED"


def test_attack_legacy_content_hash_still_binds(tmp_path):
    """Legacy entries stored truncated content hashes too — prefix matching
    must accept the true bytes and reject doctored ones."""
    l = L(tmp_path)
    full = am._content_hash(b"real artifact")
    _legacy_record(l, agent="jebi", action="file_write", target="a.txt",
                   content_hash=full[: am._LEGACY_HASH_LEN])
    ok = am.attest(l, target="a.txt", content=b"real artifact")
    assert ok["verdict"] == "ATTESTED"
    bad = am.attest(l, target="a.txt", content=b"fake artifact")
    assert bad["verdict"] == "CONTENT-MISMATCH"
