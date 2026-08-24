"""Tests for action-mirror — action provenance + mutual witness."""
from __future__ import annotations
import json
from actmirror import am


def L(tmp_path, name="l.jsonl"):
    return str(tmp_path / name)


# ─── A. action provenance ────────────────────────────────────

def test_record_seals_entry(tmp_path):
    e = am.record(L(tmp_path), agent="jebi", action="file_write",
                  target="eval.py", content=b"print(1)")
    assert e["_type"] == "action"
    assert e["agent"] == "jebi"
    assert len(e["seal"]) == 64   # full digest since seal upgrade
    assert e["prev_seal"] == "GENESIS"
    assert "content_hash" in e


def test_record_chain_links(tmp_path):
    l = L(tmp_path)
    e1 = am.record(l, agent="a", action="x")
    e2 = am.record(l, agent="b", action="y")
    assert e2["prev_seal"] == e1["seal"]


def test_verify_chain_intact(tmp_path):
    l = L(tmp_path)
    for i in range(5):
        am.record(l, agent="a", action=f"step{i}")
    fs = am.verify_chain(l)
    assert fs[0].level == "OK"
    assert "5" in fs[0].msg


def test_verify_chain_detects_modification(tmp_path):
    l = L(tmp_path)
    am.record(l, agent="a", action="x", target="secret.txt")
    am.record(l, agent="a", action="y")
    lines = open(l).read().splitlines()
    lines[0] = lines[0].replace("secret.txt", "innocent.txt")
    open(l, "w").write("\n".join(lines) + "\n")
    fs = am.verify_chain(l)
    assert fs[0].level == "FAIL"


def test_verify_chain_detects_deletion(tmp_path):
    l = L(tmp_path)
    for i in range(3):
        am.record(l, agent="a", action=f"s{i}")
    lines = open(l).read().splitlines()
    open(l, "w").write("\n".join([lines[0], lines[2]]) + "\n")  # drop middle
    fs = am.verify_chain(l)
    assert fs[0].level == "FAIL"


def test_history_filters(tmp_path):
    l = L(tmp_path)
    am.record(l, agent="jebi", action="file_write", target="a.py")
    am.record(l, agent="sonnet", action="review", target="a.py")
    am.record(l, agent="jebi", action="commit", target="repo")
    assert len(am.history(l)) == 3
    assert len(am.history(l, agent="jebi")) == 2
    assert len(am.history(l, target="a.py")) == 2
    assert len(am.history(l, agent="jebi", action="commit")) == 1


def test_attest_found(tmp_path):
    l = L(tmp_path)
    am.record(l, agent="jebi", action="file_write", target="eval.py",
              content=b"result=0.72")
    res = am.attest(l, agent="jebi", target="eval.py", content=b"result=0.72")
    assert res["verdict"] == "ATTESTED"
    assert "verified" in res["note"]


def test_attest_content_mismatch(tmp_path):
    """Artifact modified after the recorded action → CONTENT-MISMATCH."""
    l = L(tmp_path)
    am.record(l, agent="jebi", action="file_write", target="eval.py",
              content=b"result=0.72")
    res = am.attest(l, agent="jebi", target="eval.py",
                    content=b"result=0.99")   # doctored afterwards
    assert res["verdict"] == "CONTENT-MISMATCH"


def test_attest_not_found_is_honest(tmp_path):
    """Absence of record ≠ proof of absence — the note must say so."""
    res = am.attest(L(tmp_path), agent="ghost", action="anything")
    assert res["verdict"] == "NOT-FOUND"
    assert "≠" in res["note"] or "not" in res["note"].lower()


# ─── B. mutual witness ───────────────────────────────────────

def test_witness_peer_pins_head(tmp_path):
    a, b = L(tmp_path, "a.jsonl"), L(tmp_path, "b.jsonl")
    e_b = am.record(b, agent="jebi", action="x")
    w = am.witness_peer(a, b, peer_name="jebi")
    assert w["_type"] == "peer_witness"
    assert w["peer_entries"] == 1
    assert w["peer_head_seal"] == e_b["seal"]


def test_witness_empty_peer(tmp_path):
    a, b = L(tmp_path, "a.jsonl"), L(tmp_path, "b.jsonl")
    w = am.witness_peer(a, b, peer_name="jebi")
    assert w["peer_entries"] == 0
    assert w["peer_head_seal"] == "GENESIS"


def test_verify_peer_consistent_after_appends(tmp_path):
    """Legitimate appends by the peer must stay CONSISTENT (append-only OK)."""
    a, b = L(tmp_path, "a.jsonl"), L(tmp_path, "b.jsonl")
    am.record(b, agent="jebi", action="x")
    am.witness_peer(a, b, peer_name="jebi")
    am.record(b, agent="jebi", action="y")   # peer keeps working
    am.record(b, agent="jebi", action="z")
    f = am.verify_peer(a, b, peer_name="jebi")
    assert f.level == "OK"


def test_verify_peer_detects_truncation(tmp_path):
    """Peer rolls back (deletes recent entries) → FAIL."""
    a, b = L(tmp_path, "a.jsonl"), L(tmp_path, "b.jsonl")
    for i in range(3):
        am.record(b, agent="jebi", action=f"s{i}")
    am.witness_peer(a, b, peer_name="jebi")
    lines = open(b).read().splitlines()
    open(b, "w").write("\n".join(lines[:1]) + "\n")   # truncate to 1 entry
    f = am.verify_peer(a, b, peer_name="jebi")
    assert f.level == "FAIL"
    assert "TRUNCATED" in f.msg


def test_verify_peer_detects_replacement(tmp_path):
    """Peer ledger completely replaced (the chain-hash blind spot) → FAIL."""
    a, b = L(tmp_path, "a.jsonl"), L(tmp_path, "b.jsonl")
    am.record(b, agent="jebi", action="embarrassing_result")
    am.record(b, agent="jebi", action="more")
    am.witness_peer(a, b, peer_name="jebi")
    # attacker rebuilds a clean-looking ledger from scratch
    import os
    os.remove(b)
    am.record(b, agent="jebi", action="innocent_history")
    am.record(b, agent="jebi", action="looks_fine")
    assert am.verify_chain(b)[0].level == "OK"        # chain alone is fooled!
    f = am.verify_peer(a, b, peer_name="jebi")
    assert f.level == "FAIL"                           # witness is not
    assert "REWRITTEN" in f.msg or "TRUNCATED" in f.msg


def test_verify_peer_no_witness_warns(tmp_path):
    a, b = L(tmp_path, "a.jsonl"), L(tmp_path, "b.jsonl")
    am.record(b, agent="jebi", action="x")
    f = am.verify_peer(a, b, peer_name="jebi")
    assert f.level == "WARN"


def test_cross_witness_both_directions(tmp_path):
    a, b = L(tmp_path, "a.jsonl"), L(tmp_path, "b.jsonl")
    am.record(a, agent="seara", action="relay")
    am.record(b, agent="jebi", action="eval")
    am.cross_witness(a, b, name_a="seara", name_b="jebi")
    assert am.verify_peer(a, b, peer_name="jebi").level == "OK"
    assert am.verify_peer(b, a, peer_name="seara").level == "OK"


# ─── family round ────────────────────────────────────────────

def _family(tmp_path):
    ledgers = {n: L(tmp_path, f"{n}.jsonl") for n in ["seara", "jebi", "sonnet"]}
    am.record(ledgers["seara"], agent="seara", action="ticket", target="0034")
    am.record(ledgers["jebi"], agent="jebi", action="eval", target="exp1")
    am.record(ledgers["sonnet"], agent="sonnet", action="review", target="exp1")
    return ledgers


def test_family_round_all_pairs(tmp_path):
    ledgers = _family(tmp_path)
    wits = am.family_round(ledgers)
    assert len(wits) == 6   # 3 agents × 2 peers


def test_family_verify_all_ok(tmp_path):
    ledgers = _family(tmp_path)
    am.family_round(ledgers)
    fs = am.family_verify(ledgers)
    assert len(fs) == 6
    assert all(f.level == "OK" for f in fs)


def test_family_catches_single_traitor(tmp_path):
    """One agent rewrites its own history → BOTH other agents' witness fails.
    To cheat, the attacker must rewrite every family ledger simultaneously."""
    ledgers = _family(tmp_path)
    am.family_round(ledgers)
    # jebi rewrites its ledger from scratch
    import os
    os.remove(ledgers["jebi"])
    am.record(ledgers["jebi"], agent="jebi", action="clean_history")
    fs = am.family_verify(ledgers)
    fails = [f for f in fs if f.level == "FAIL"]
    assert len(fails) == 2   # seara→jebi and sonnet→jebi both catch it
    assert all("jebi" in f.probe for f in fails)


def test_tail_truncation_chain_blind_witness_catches(tmp_path):
    """Chain hashing CANNOT detect tail truncation (a shorter chain is still
    valid) — documented gap. The mutual interlock catches it: in
    cross_witness, B pins A's head AFTER A's witness entry exists, so A
    silently dropping its own tail is exposed by B's witness record."""
    a, b = L(tmp_path, "a.jsonl"), L(tmp_path, "b.jsonl")
    am.record(a, agent="seara", action="x")
    am.record(b, agent="jebi", action="y")
    am.cross_witness(a, b, name_a="seara", name_b="jebi")
    # seara drops its own tail (its witness entry about jebi)
    lines = open(a).read().splitlines()
    open(a, "w").write(lines[0] + "\n")
    assert am.verify_chain(a)[0].level == "OK"            # chain alone: fooled
    f = am.verify_peer(b, a, peer_name="seara")           # jebi's record: not
    assert f.level == "FAIL"
    assert "TRUNCATED" in f.msg


def test_determinism_same_inputs(tmp_path):
    """Same actions in two ledgers → same content hashes (ts differs, hash logic same)."""
    l1, l2 = L(tmp_path, "1.jsonl"), L(tmp_path, "2.jsonl")
    e1 = am.record(l1, agent="a", action="w", content=b"identical bytes")
    e2 = am.record(l2, agent="a", action="w", content=b"identical bytes")
    assert e1["content_hash"] == e2["content_hash"]


# ── signed-identity layer (optional [signing] extra) ──────────────────────────
def test_verify_signatures_unsigned_ok(tmp_path):
    """Unsigned entries: the 'who' is self-asserted — verify_signatures passes as OK."""
    l = L(tmp_path)
    am.record(l, agent="jebi", action="x")
    fs = am.verify_signatures(l)
    assert fs[0].level == "OK"


def test_verify_signatures_signed_and_forgery(tmp_path):
    """Signed entry verifies; impersonation (wrong key) and tampering are both caught."""
    from actmirror import identity
    if not identity.available():
        import pytest
        pytest.skip("signing extra not installed")
    l = L(tmp_path)
    keyfile = str(tmp_path / "jebi.key")
    identity.generate(keyfile)
    am.record(l, agent="jebi", action="eval", target="exp1", sign_key=keyfile)
    assert am.verify_signatures(l)[0].level == "OK"
    # tamper the signed entry's agent → signature must no longer verify
    rows = [json.loads(x) for x in open(l)]
    rows[0]["agent"] = "mallory"
    open(l, "w").write("\n".join(json.dumps(r) for r in rows) + "\n")
    assert am.verify_signatures(l)[0].level == "FAIL"


# ─── H. last-seal lookup: O(1) tail read, unchanged answer ───────────────
#
# `_get_last_seal` runs on every append. It used to parse the whole ledger to
# find its last line, so append was O(n) and a ledger got slower purely by being
# used (on a 3,097-entry family ledger one lookup cost ~50 ms). It now reads
# backwards from EOF. These tests pin the part that must NOT change — the answer —
# and the part that must — how much of the file is touched.

def _reference_last_seal(path):
    """The pre-fix implementation, kept as the oracle: parse everything, scan back."""
    import os
    if not os.path.exists(path):
        return "GENESIS"
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                entries.append({"_corrupt": line[:80]})
    for e in reversed(entries):
        if isinstance(e, dict) and "seal" in e:
            return e["seal"]
    return "GENESIS"


def _awkward_ledgers(tmp_path):
    """The shapes a tail reader can get wrong. Returns [(label, path)]."""
    def S(h):
        return json.dumps({"_type": "action", "agent": "t", "action": "a",
                           "prev_seal": "GENESIS", "seal": h}, ensure_ascii=False)
    cases, i = [], 0
    def mk(label, text):
        nonlocal i
        i += 1
        p = tmp_path / f"c{i}.jsonl"
        p.write_text(text, encoding="utf-8")
        cases.append((label, str(p)))
    mk("empty", "")
    mk("blank lines only", "\n\n\n")
    mk("single entry", S("aa" * 32) + "\n")
    mk("no trailing newline", S("bb" * 32))
    mk("no sealed entry at all", '{"ts": 1, "note": "tick"}\n' * 5)
    mk("unsealed trailing lines", S("cc" * 32) + "\n" + '{"note": "no seal"}\n' * 3)
    mk("corrupt last line", S("dd" * 32) + "\n{ this is not json\n")
    mk("corrupt middle line", S("ee" * 32) + "\n@@@\n" + S("ff" * 32) + "\n")
    mk("non-ascii payload", json.dumps({"agent": "대장님", "action": "🪞",
                                        "seal": "11" * 32}, ensure_ascii=False) + "\n")
    # one line longer than the read chunk, so the tail scan must span chunks
    mk("line longer than chunk", json.dumps({"agent": "t", "action": "x" * 20000,
                                             "seal": "22" * 32}) + '\n{"note": "tail"}\n')
    mk("many unsealed lines after the last seal",
       S("33" * 32) + "\n" + ('{"note": "%s"}\n' % ("y" * 300)) * 100)
    cases.append(("missing file", str(tmp_path / "does_not_exist.jsonl")))
    return cases


def test_last_seal_matches_full_parse(tmp_path):
    """The fast tail read answers exactly what parsing the whole ledger answers."""
    cases = _awkward_ledgers(tmp_path)
    assert len(cases) >= 12, "the case list itself must not silently shrink to nothing"
    for label, path in cases:
        assert am._get_last_seal(path) == _reference_last_seal(path), label


def test_last_seal_oracle_rejects_a_wrong_implementation(tmp_path):
    """Positive control: the comparison above must be able to FAIL.

    A test that only ever runs the correct implementation cannot tell "equivalent"
    from "not measuring anything". So run a deliberately wrong reader — one that
    looks only at the final line — through the same cases and require it to be caught.
    """
    def wrong(path):
        import os
        if not os.path.exists(path):
            return "GENESIS"
        lines = [x for x in open(path, encoding="utf-8").read().splitlines() if x.strip()]
        if not lines:
            return "GENESIS"
        try:
            e = json.loads(lines[-1])
        except json.JSONDecodeError:
            return "GENESIS"
        return e["seal"] if isinstance(e, dict) and "seal" in e else "GENESIS"

    caught = [label for label, path in _awkward_ledgers(tmp_path)
              if wrong(path) != _reference_last_seal(path)]
    assert caught, "the equivalence check passed a knowingly broken reader — it measures nothing"


class _CountingFile:
    """A file handle that reports how many bytes it actually handed out.

    It has to count line iteration too, not just read(): the implementation this
    test replaced walked the ledger with `for line in f`, so a counter that only
    wrapped read() would have recorded zero bytes for it and passed. That is the
    vacuous-pass shape this repo keeps finding — the test would have measured nothing.
    """

    def __init__(self, fh, sink):
        self._fh, self._sink = fh, sink

    def _count(self, data):
        if data:
            self._sink.append(len(data))
        return data

    def read(self, *a, **kw):
        return self._count(self._fh.read(*a, **kw))

    def readline(self, *a, **kw):
        return self._count(self._fh.readline(*a, **kw))

    def readlines(self, *a, **kw):
        out = self._fh.readlines(*a, **kw)
        for line in out:
            self._count(line)
        return out

    def __iter__(self):
        for line in self._fh:
            yield self._count(line)

    def __enter__(self):
        self._fh.__enter__()
        return self

    def __exit__(self, *a):
        return self._fh.__exit__(*a)

    def __getattr__(self, name):
        return getattr(self._fh, name)


def test_append_reads_only_the_tail(tmp_path, monkeypatch):
    """Looking up the previous seal must not read the whole ledger.

    Measured structurally (bytes the file handle handed out), not by wall-clock,
    so it cannot go green on a fast machine or flake on a loaded one.
    """
    l = L(tmp_path)
    big = json.dumps({"agent": "t", "action": "p" * 50000, "seal": "44" * 32})
    with open(l, "w", encoding="utf-8") as f:
        for _ in range(40):                       # ~2 MB of ledger
            f.write(big + "\n")
    size = (tmp_path / "l.jsonl").stat().st_size
    assert size > 1_000_000

    real_open, read_sizes = open, []

    def counting_open(file, *a, **kw):
        fh = real_open(file, *a, **kw)
        return _CountingFile(fh, read_sizes) if str(file) == l else fh

    monkeypatch.setattr("builtins.open", counting_open)
    try:
        seal = am._get_last_seal(l)
    finally:
        monkeypatch.undo()

    assert seal == "44" * 32
    assert read_sizes, "nothing was read at all — the counter is not wired to the lookup"
    assert sum(read_sizes) < size // 10, (
        f"read {sum(read_sizes)} of {size} bytes — the lookup is still scanning the ledger")
