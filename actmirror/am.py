"""
🪪 Action Mirror — agent action provenance ledger + mutual witness network.

Third member of the mirror family:
  measure-mirror     audits "AI evaluation claims"   (is the CLAIM honest?)
  provenance-mirror  audits "content authenticity"   (is the ORIGIN proven?)
  action-mirror      audits "agent behaviour"        (WHO DID WHAT, provably?)

Two capabilities, one ledger format:

A. ACTION PROVENANCE — every agent action (tool call, file write, commit,
   ticket, message) is appended to a chain-hashed ledger. Content is recorded
   as a SHA-256 hash only (privacy + size). attest() later answers
   "did agent X really do Y to Z?" — and catches content modified afterwards.

B. MUTUAL WITNESS — agents periodically seal each other's ledger head
   (entry_count + head_seal) into their own ledger. A chain hash alone cannot
   detect *complete ledger replacement* (measure-mirror's documented gap);
   position-pinned cross-witness records can: if a peer ledger is truncated
   or rewritten, the historical head no longer matches at its pinned position.
   To erase one agent's history, an attacker must rewrite EVERY family
   member's ledger simultaneously.

Honest threat model (read this before trusting it):
  - This provides tamper-EVIDENCE within a family of agents whose ledgers
    live in separate trust domains (different processes/users/machines).
  - A host-level attacker (root on the single machine holding all ledgers)
    can rewrite everything consistently. This tool does not stop that;
    nothing local-only can.
  - Timestamps come from the local clock — sealed order is trustworthy,
    wall-clock time is not proof.
  - The ledger stores hashes of content, not content. attest() with content
    verification needs the actual bytes presented again.

Zero dependencies (stdlib only). Deterministic. Same DNA as measure-mirror.
"""
from __future__ import annotations
import hashlib, json, os, time
from dataclasses import dataclass


# ─────────────────────────────────────────────────────────────
# Result type (family-standard)
# ─────────────────────────────────────────────────────────────
@dataclass
class Finding:
    probe: str
    level: str   # OK / WARN / FAIL
    msg: str


# ─────────────────────────────────────────────────────────────
# Ledger primitives (chain-hashed, ported from measure-mirror)
# ─────────────────────────────────────────────────────────────
def _load_entries(ledger_path: str) -> list[dict]:
    """Parse all non-empty lines. Corrupt lines become {'_corrupt': ...} so
    position semantics stay stable and chain verification fails loudly."""
    if not os.path.exists(ledger_path):
        return []
    out: list[dict] = []
    with open(ledger_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"_corrupt": line[:80]})
    return out


def _get_last_seal(ledger_path: str, _chunk: int = 8192) -> str:
    """The seal of the last sealed entry — read from the END of the file.

    This runs on EVERY append. Parsing the whole ledger to find its last line made
    append O(n): on the family ledger (3,097 entries / 3.4 MB) one `record` spent
    50 ms here, and the cost grows with every entry ever written — the ledger gets
    slower precisely because it is being used.

    Deliberately NOT cached in memory: this ledger is appended by other processes
    (cron jobs, sibling agents), and a cached head would hand out a prev_seal that
    is no longer last, forking the chain. The file stays the single source of truth;
    only the amount of it we read changes.

    Semantics are unchanged, including the awkward cases: unsealed or unparseable
    trailing lines are skipped (as _load_entries' {_corrupt} placeholders were), a
    ledger with no sealed entry at all still answers GENESIS, and CRLF / CR / LF
    endings all read the same — text mode used to normalise those for us.
    """
    if not os.path.exists(ledger_path):
        return "GENESIS"
    with open(ledger_path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        buf = b""
        while pos > 0:
            step = min(_chunk, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
            # Split on every line ending, not just \n. Reading bytes means universal-newline
            # translation no longer happens for us: a ledger written with CR-only endings
            # parsed as ONE line and the lookup answered GENESIS — which would have appended
            # a second genesis entry into the middle of a live chain. Normalising first is
            # safe because a raw CR or LF inside a JSON string is not valid JSON anyway.
            parts = buf.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
            # parts[0] may be the tail of a line that starts earlier in the file —
            # only safe to read once we have reached the beginning.
            head, complete = parts[0], parts[1:]
            for line in reversed(complete):
                seal = _seal_of(line)
                if seal is not None:
                    return seal
            if pos == 0:
                seal = _seal_of(head)
                if seal is not None:
                    return seal
                break
            buf = head
    return "GENESIS"


def _seal_of(raw: bytes):
    """`seal` of one raw ledger line, or None if it has none / does not parse."""
    line = raw.strip()
    if not line:
        return None
    try:
        entry = json.loads(line.decode("utf-8"))
    except Exception:
        return None
    return entry["seal"] if isinstance(entry, dict) and "seal" in entry else None


def _seal(ledger_path: str, entry: dict, sign_key: str | None = None) -> dict:
    entry["prev_seal"] = _get_last_seal(ledger_path)
    # `seal` and `sig` are attestation fields, excluded from the content hash.
    body = {k: v for k, v in entry.items() if k not in ("seal", "sig")}
    entry["seal"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    if sign_key is not None:
        from . import identity
        entry["sig"] = identity.sign(sign_key, entry["seal"])
    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _content_hash(content) -> str:
    b = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(b).hexdigest()


_LEGACY_HASH_LEN = 16   # pre-v0.6 truncated seals/hashes — see _hash_matches


def _hash_matches(stored: str, full_hex: str) -> bool:
    """Match a stored seal/hash against the full SHA-256 hex digest.

    New entries use the FULL 64-hex digest: 16-hex (64-bit) truncation lets a
    dishonest sealer birthday-search (~2^32) two entries sharing one seal.
    Legacy 16-hex values stay verifiable via prefix match (their original,
    weaker strength is unchanged — the upgrade protects new entries)."""
    if stored == full_hex:
        return True
    return len(stored) == _LEGACY_HASH_LEN and stored == full_hex[:_LEGACY_HASH_LEN]


def verify_chain(ledger_path: str) -> list[Finding]:
    """Full chain verification: every seal recomputes, every link connects."""
    entries = _load_entries(ledger_path)
    if not entries:
        return [Finding("⛓ chain", "OK", "Ledger empty — nothing to verify.")]
    prev = "GENESIS"
    for i, e in enumerate(entries, 1):
        if "_corrupt" in e:
            return [Finding("⛓ chain", "FAIL",
                            f"Entry {i} is not valid JSON — ledger corrupted.")]
        if e.get("prev_seal") != prev:
            return [Finding("⛓ chain", "FAIL",
                            f"Entry {i}: prev_seal broken — "
                            "deletion/insertion/reorder detected.")]
        body = {k: v for k, v in e.items() if k not in ("seal", "sig")}
        expect_full = hashlib.sha256(
            json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        if not _hash_matches(str(e.get("seal", "")), expect_full):
            return [Finding("⛓ chain", "FAIL",
                            f"Entry {i}: seal mismatch — content modified.")]
        prev = e["seal"]
    return [Finding("⛓ chain", "OK",
                    f"Chain intact — {len(entries)} entries verified.")]


def verify_signatures(ledger_path: str) -> list[Finding]:
    """Verify the cryptographic identity layer: every entry that carries a pubkey must have a
    valid signature of its seal under that key. Entries without a pubkey are unsigned (OK —
    the 'who' is self-asserted, see README). Requires the [signing] extra to check signatures."""
    from . import identity
    entries = _load_entries(ledger_path)
    signed = [e for e in entries if e.get("pubkey")]
    if not signed:
        return [Finding("🔑 identity", "OK",
                        "No signed entries — 'who' is self-asserted (unsigned).")]
    if not identity.available():
        return [Finding("🔑 identity", "WARN",
                        f"{len(signed)} signed entries present but cryptography not installed — "
                        "pip install action-mirror[signing] to verify.")]
    bad = []
    for i, e in enumerate(entries, 1):
        if not e.get("pubkey"):
            continue
        # recompute the seal from content so a tampered body is caught here too, not only by
        # verify_chain — the signature must vouch for the ACTUAL content, not a stale seal.
        body = {k: v for k, v in e.items() if k not in ("seal", "sig")}
        full = hashlib.sha256(
            json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        # legacy entries signed the truncated seal — verify against the stored-length form
        real_seal = full[:_LEGACY_HASH_LEN] if len(str(e.get("seal", ""))) == _LEGACY_HASH_LEN else full
        if not e.get("sig") or not identity.verify(e["pubkey"], real_seal, e["sig"]):
            bad.append(i)
    if bad:
        return [Finding("🔑 identity", "FAIL",
                        f"Invalid/forged signature at entr{'y' if len(bad)==1 else 'ies'} {bad} "
                        "— signed 'who' does not verify.")]
    keys = {e["pubkey"][:12] for e in signed}
    return [Finding("🔑 identity", "OK",
                    f"{len(signed)} signed entries verified across {len(keys)} key(s). "
                    "(Attribution proven; independence is NOT — one operator may hold many keys.)")]


# ─────────────────────────────────────────────────────────────
# A. Action provenance
# ─────────────────────────────────────────────────────────────
def record(ledger_path: str, *, agent: str, action: str,
           target: str | None = None, payload: dict | None = None,
           content=None, sign_key: str | None = None) -> dict:
    """Record one agent action as a chain-sealed ledger entry.

    Args:
        agent:   who acted (e.g. "jebi", "seara", "sonnet")
        action:  what kind (free-form: "tool_call", "file_write", "commit",
                 "ticket", "msg", ...)
        target:  what it acted on (path, ticket id, claim_id, ...)
        payload: small JSON-serializable metadata (args summary, exit code...)
        content: bytes/str of the produced artifact — only its SHA-256 is
                 stored (privacy + size), enabling later attest(content=...).
        sign_key: optional path to an Ed25519 private key ([signing] extra). When given, the
                 entry carries a `pubkey` (chained) and a `sig` over its seal — upgrading the
                 self-asserted `agent` to a verifiable identity. See identity.py for honest scope.
    """
    entry: dict = {
        "_type":  "action",
        "ts":     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "agent":  agent,
        "action": action,
    }
    if target is not None:
        entry["target"] = target
    if content is not None:
        entry["content_hash"] = _content_hash(content)
    if payload is not None:
        entry["payload"] = payload
    if sign_key is not None:
        from . import identity
        entry["pubkey"] = identity.public_hex(sign_key)   # chained: claimed key can't be swapped
    return _seal(ledger_path, entry, sign_key=sign_key)


def history(ledger_path: str, *, agent: str | None = None,
            action: str | None = None, target: str | None = None) -> list[dict]:
    """Query recorded actions with optional filters."""
    out = []
    for e in _load_entries(ledger_path):
        if e.get("_type") != "action":
            continue
        if agent is not None and e.get("agent") != agent:
            continue
        if action is not None and e.get("action") != action:
            continue
        if target is not None and e.get("target") != target:
            continue
        out.append(e)
    return out


def attest(ledger_path: str, *, agent: str | None = None,
           action: str | None = None, target: str | None = None,
           content=None) -> dict:
    """Answer "did this happen?" from the sealed ledger.

    Verdicts:
      ATTESTED         — matching sealed record(s) found
                         (and content hash matches, when content is given)
      CONTENT-MISMATCH — the action is recorded, but the presented content's
                         hash differs from what was sealed → the artifact was
                         modified after the recorded action
      NOT-FOUND        — no sealed record matches (absence of record is not
                         proof the event never happened — only that this
                         ledger never sealed it)
    """
    matches = history(ledger_path, agent=agent, action=action, target=target)
    if not matches:
        return {"verdict": "NOT-FOUND", "matches": [],
                "note": "No sealed record matches. Absence of record ≠ proof of absence."}
    if content is None:
        return {"verdict": "ATTESTED", "matches": matches,
                "note": f"{len(matches)} sealed record(s) match."}
    h = _content_hash(content)
    hash_hits = [e for e in matches
                 if _hash_matches(str(e.get("content_hash", "")), h)]
    if hash_hits:
        return {"verdict": "ATTESTED", "matches": hash_hits,
                "note": f"{len(hash_hits)} sealed record(s) match, content hash verified ({h})."}
    return {"verdict": "CONTENT-MISMATCH", "matches": matches,
            "note": "Action is recorded but the presented content's hash "
                    f"({h}) differs from the sealed hash — artifact was "
                    "modified after recording."}


# ─────────────────────────────────────────────────────────────
# B. Mutual witness network
# ─────────────────────────────────────────────────────────────
def witness_peer(my_ledger: str, peer_ledger: str, *, peer_name: str) -> dict:
    """Seal the peer ledger's current head (position-pinned) into MY ledger.

    Records (peer_entries, peer_head_seal): "at this moment, peer's ledger had
    N entries and entry N's seal was X". Appends by the peer later are fine;
    truncation or rewriting of entry ≤ N is permanently detectable.
    """
    peer_entries = _load_entries(peer_ledger)
    n = len(peer_entries)
    head = peer_entries[-1].get("seal", "INVALID") if n else "GENESIS"
    anchor = "empty"
    if os.path.exists(peer_ledger):
        with open(peer_ledger, "rb") as f:
            anchor = hashlib.sha256(f.read()).hexdigest()
    entry = {
        "_type":          "peer_witness",
        "ts":             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "peer":           peer_name,
        "peer_entries":   n,
        "peer_head_seal": head,
        "peer_anchor":    anchor,   # forensic only — appends legitimately change it
    }
    return _seal(my_ledger, entry)


def verify_peer(my_ledger: str, peer_ledger: str, *, peer_name: str) -> Finding:
    """Check the peer's ledger against every witness record I hold.

    For each witness record (n, head_seal): the peer ledger must still have
    ≥ n entries AND entry n's seal must equal head_seal. Append-only history
    passes; truncation/rewrite/replacement fails.

    Levels:
      FAIL — at least one pinned head no longer matches (ROLLBACK/REWRITE)
      WARN — I hold no witness records for this peer (can't say anything)
      OK   — every pinned head still present at its position
    """
    wits = [e for e in _load_entries(my_ledger)
            if e.get("_type") == "peer_witness" and e.get("peer") == peer_name]
    if not wits:
        return Finding("👁 peer-witness", "WARN",
                       f"No witness records for peer '{peer_name}' — "
                       "nothing to verify against.")
    peer_now = _load_entries(peer_ledger)
    problems: list[str] = []
    for w in wits:
        n = w.get("peer_entries", 0)
        if n == 0:
            continue   # witnessed an empty ledger — pins nothing
        if len(peer_now) < n:
            problems.append(
                f"TRUNCATED: witnessed {n} entries at {w['ts']}, now only {len(peer_now)}")
        elif peer_now[n - 1].get("seal") != w.get("peer_head_seal"):
            problems.append(
                f"REWRITTEN: entry {n} seal ≠ head witnessed at {w['ts']} "
                f"({peer_now[n-1].get('seal')} ≠ {w.get('peer_head_seal')})")
    if problems:
        return Finding("👁 peer-witness", "FAIL",
                       f"Peer '{peer_name}' ledger ROLLBACK detected: "
                       + "; ".join(problems) + ".")
    pinned = sum(1 for w in wits if w.get("peer_entries", 0) > 0)
    return Finding("👁 peer-witness", "OK",
                   f"Peer '{peer_name}': {pinned} pinned head(s) all consistent "
                   "— append-only history respected.")


def cross_witness(ledger_a: str, ledger_b: str, *,
                  name_a: str, name_b: str) -> tuple[dict, dict]:
    """Mutual witness in both directions: A pins B's head, then B pins A's
    (B's record includes A's fresh witness entry — deliberate interlock)."""
    wa = witness_peer(ledger_a, ledger_b, peer_name=name_b)
    wb = witness_peer(ledger_b, ledger_a, peer_name=name_a)
    return wa, wb


def family_round(ledgers: dict[str, str]) -> list[dict]:
    """One witness round over a family: every agent pins every other agent.

    Args:
        ledgers: {agent_name: ledger_path}
    Returns the witness entries created (n·(n-1) of them).
    """
    out: list[dict] = []
    names = sorted(ledgers)
    for me in names:
        for peer in names:
            if me == peer:
                continue
            out.append(witness_peer(ledgers[me], ledgers[peer], peer_name=peer))
    return out


def family_verify(ledgers: dict[str, str]) -> list[Finding]:
    """Verify every pair in the family. One Finding per (observer → peer)."""
    findings: list[Finding] = []
    names = sorted(ledgers)
    for me in names:
        for peer in names:
            if me == peer:
                continue
            f = verify_peer(ledgers[me], ledgers[peer], peer_name=peer)
            findings.append(Finding(f"👁 {me}→{peer}", f.level, f.msg))
    return findings


# ─────────────────────────────────────────────────────────────
# Report printer (family-standard)
# ─────────────────────────────────────────────────────────────
def report(title: str, findings: list[Finding]) -> str:
    """Print the family-standard report; return the worst level so the CLI can
    turn a printed 🔴 into a non-zero exit code instead of a silent 0."""
    icon = {"OK": "✅", "WARN": "⚠️ ", "FAIL": "🔴"}
    worst = "FAIL" if any(f.level == "FAIL" for f in findings) else \
            "WARN" if any(f.level == "WARN" for f in findings) else "OK"
    print(f"\n🪪 Action Mirror: {title}")
    print(f"   Overall: {icon[worst]} {worst}")
    for f in findings:
        print(f"   {icon[f.level]} [{f.probe}] {f.msg}")
    return worst


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────
def _cli() -> int:
    """Exit codes: 0 — command ran and any verdict was OK/WARN (or the command
    has no verdict); 1 — a verdict-bearing command answered negatively
    (chain/signature/peer FAIL, attest CONTENT-MISMATCH or NOT-FOUND).
    Before 0.3.0 every path exited 0, so `am verify && …` shipped a tampered
    ledger — the verdict was print-only. Found when a commit-binding tool's
    tamper demo passed its ledger-mutation case.
    """
    import argparse
    p = argparse.ArgumentParser(
        prog="am", description="🪪 Action Mirror — agent action provenance + mutual witness")
    p.add_argument("--ledger", default=os.environ.get("AM_LEDGER", "am_ledger.jsonl"),
                   help="Ledger path (default: $AM_LEDGER or ./am_ledger.jsonl)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="Seal one agent action")
    r.add_argument("--agent", required=True)
    r.add_argument("--action", required=True)
    r.add_argument("--target", default=None)
    r.add_argument("--payload", default=None, help="JSON string of metadata")
    r.add_argument("--content-file", default=None,
                   help="File whose SHA-256 to seal as content_hash")
    r.add_argument("--sign", default=None, metavar="KEYFILE",
                   help="Sign the entry with this Ed25519 private key ([signing] extra)")

    kg = sub.add_parser("keygen", help="Generate an Ed25519 identity keypair ([signing] extra)")
    kg.add_argument("--out", required=True, help="Path to write the private key")

    sub.add_parser("verify-sig", help="Verify the signed-identity layer of my ledger")

    h = sub.add_parser("history", help="Query sealed actions")
    h.add_argument("--agent", default=None)
    h.add_argument("--action", default=None)
    h.add_argument("--target", default=None)

    a = sub.add_parser("attest", help='Prove "did agent X do Y to Z?"')
    a.add_argument("--agent", default=None)
    a.add_argument("--action", default=None)
    a.add_argument("--target", default=None)
    a.add_argument("--content-file", default=None,
                   help="Verify this file's bytes against the sealed hash")

    sub.add_parser("verify", help="Verify my ledger's chain integrity")

    w = sub.add_parser("witness", help="Pin a peer ledger's head into my ledger")
    w.add_argument("peer_ledger")
    w.add_argument("--name", required=True, help="Peer agent name")

    vp = sub.add_parser("verify-peer", help="Check a peer ledger against my witness records")
    vp.add_argument("peer_ledger")
    vp.add_argument("--name", required=True)

    cr = sub.add_parser("cross", help="Mutual witness between two ledgers")
    cr.add_argument("ledger_a")
    cr.add_argument("ledger_b")
    cr.add_argument("--names", nargs=2, required=True, metavar=("NAME_A", "NAME_B"))

    args = p.parse_args()
    if args.cmd == "keygen":
        from . import identity
        pub = identity.generate(args.out)
        print(f"🔑 keypair written: {args.out}  (keep private!)\n   pubkey: {pub}")
        return 0
    if args.cmd == "verify-sig":
        fs = verify_signatures(args.ledger)
        for f in fs:
            print(f"   {f}")
        return 1 if any(f.level == "FAIL" for f in fs) else 0
    if args.cmd == "record":
        content = None
        if args.content_file:
            with open(args.content_file, "rb") as f:
                content = f.read()
        payload = json.loads(args.payload) if args.payload else None
        e = record(args.ledger, agent=args.agent, action=args.action,
                   target=args.target, payload=payload, content=content,
                   sign_key=args.sign)
        print(f"🪪 Sealed: {e['agent']} {e['action']}"
              + (f" → {e['target']}" if "target" in e else "")
              + f"  seal={e['seal']}"
              + ("  🔑signed" if "sig" in e else ""))
    elif args.cmd == "history":
        for e in history(args.ledger, agent=args.agent,
                         action=args.action, target=args.target):
            print(f"  {e['ts']}  {e['agent']:<8} {e['action']:<12} "
                  f"{e.get('target','-'):<30} seal={e['seal']}")
    elif args.cmd == "attest":
        content = None
        if args.content_file:
            with open(args.content_file, "rb") as f:
                content = f.read()
        res = attest(args.ledger, agent=args.agent, action=args.action,
                     target=args.target, content=content)
        icon = {"ATTESTED": "✅", "CONTENT-MISMATCH": "🔴", "NOT-FOUND": "⚪"}
        print(f"{icon[res['verdict']]} {res['verdict']}: {res['note']}")
        for e in res["matches"]:
            print(f"   {e['ts']}  {e['agent']} {e['action']} seal={e['seal']}")
        return 0 if res["verdict"] == "ATTESTED" else 1
    elif args.cmd == "verify":
        return 1 if report("chain integrity",
                           verify_chain(args.ledger)) == "FAIL" else 0
    elif args.cmd == "witness":
        e = witness_peer(args.ledger, args.peer_ledger, peer_name=args.name)
        print(f"👁 Witnessed '{args.name}': {e['peer_entries']} entries, "
              f"head={e['peer_head_seal']}  seal={e['seal']}")
    elif args.cmd == "verify-peer":
        worst = report(f"peer '{args.name}'",
                       [verify_peer(args.ledger, args.peer_ledger,
                                    peer_name=args.name)])
        return 1 if worst == "FAIL" else 0
    elif args.cmd == "cross":
        na, nb = args.names
        cross_witness(args.ledger_a, args.ledger_b, name_a=na, name_b=nb)
        print(f"👁👁 Mutual witness sealed: {na} ⇄ {nb}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
