"""
Export an action-mirror ledger in the IETF *Agent Audit Trail* (AAT) record format.

Target revision: draft-sharif-agent-audit-trail-11 (2026-11-21), an individual
Internet-Draft — NOT an IETF standard, NOT adopted by a working group, and still
changing between revisions. Field names, enums and the chaining rule below follow
-11 only. If a later revision changes them, this exporter must be updated; the
revision is written into every exported record's ``action_detail`` so a reader
can tell which one was used.

What this does
  * Converts each sealed action-mirror entry into one AAT JSON Lines record
    (draft §10.1), with the §3.1 REQUIRED fields and the §6.1 chain:
        prev_hash(N) = hex(SHA-256(JCS(record(N-1) with "batch" removed)))
    and ``prev_hash: null`` on the genesis record.
  * Preserves the original action-mirror entry (including ``seal``, ``prev_seal``
    and any ``sig``) under ``action_detail.action_mirror``, so the export can be
    cross-checked against the native ledger.
  * Refuses to export a ledger whose native chain does not verify: re-chaining a
    tampered ledger into a fresh, internally valid AAT chain would launder the
    tampering.

What it does NOT do (read before relying on an export)
  * It adds no new evidence. The AAT chain is derived from — and only as
    trustworthy as — the native ledger. Anyone holding the native ledger can
    regenerate the same export.
  * Several AAT REQUIRED fields have no counterpart in action-mirror and are
    filled with caller-supplied values or stated defaults:
        principal      default: the agent itself, principal_type "agent",
                       authority_ref null (self-asserted, not an authorization)
        agent_version  default "0.0.0" (unknown)
        trust_level    default "L0"
        outcome        "success"/"failure" only when payload.exit is an integer,
                       otherwise ``default_outcome`` (default "success", which here
                       means "recorded", not "verified to have succeeded")
        record_phase   "post_execution" (action-mirror records after the fact)
  * ``record_id`` and ``session_id`` are UUIDv4-*formatted* values derived
    deterministically from native seals (so re-exports are identical). They are
    not random as §3.1's UUIDv4 implies.
  * Every native action becomes ``action_type: "tool_call"`` (tool_name = the
    native ``action`` string); ``peer_witness`` entries become ``"decision"``.
    Native free-form action names are not mapped onto the other AAT types.
  * action-mirror's Ed25519 ``sig`` signs the native seal, not the AAT record, so
    it is NOT emitted as an AAT ``signature`` (which §6.2 defines as ES256 by
    default over the AAT record). It stays available in the preserved entry.
  * Timestamps keep the native one-second precision (§3.3 recommends ms).
  * JCS (RFC 8785) is implemented here for the JSON values action-mirror
    produces; it has not been tested against an external JCS conformance suite.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.parse
from datetime import datetime
from decimal import Decimal

from .am import Finding, _load_entries, verify_chain

DRAFT = "draft-sharif-agent-audit-trail-11"
DRAFT_DATE = "2026-11-21"

ACTION_TYPES = ("tool_call", "tool_response", "decision", "delegation",
                "escalation", "error", "lifecycle")
OUTCOMES = ("success", "failure", "timeout", "denied", "escalated")
TRUST_LEVELS = ("L0", "L1", "L2", "L3", "L4")
PRINCIPAL_TYPES = ("human", "organization", "service", "agent")
RECORD_PHASES = ("pre_execution", "post_execution", "concurrent")
REQUIRED = ("record_id", "timestamp", "agent_id", "principal", "agent_version",
            "session_id", "action_type", "action_detail", "outcome", "trust_level",
            "parent_record_id", "prev_hash", "record_phase")

_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
                     r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


# ─────────────────────────────────────────────────────────────
# RFC 8785 JSON Canonicalization Scheme
# ─────────────────────────────────────────────────────────────
def _jcs_number(x) -> str:
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, int):
        if abs(x) > 2 ** 53:
            raise ValueError(f"integer {x} is outside the IEEE-754 safe range (RFC 8785 §3.2.2.3)")
        return str(x)
    if not math.isfinite(x):
        raise ValueError("NaN/Infinity are not valid JSON (RFC 8785 §3.2.2.2)")
    if x == 0:
        return "0"
    # ECMAScript Number::toString on the shortest round-trip digits (Python repr).
    sign = "-" if x < 0 else ""
    t = Decimal(repr(abs(x))).as_tuple()
    raw = "".join(map(str, t.digits))
    n = len(raw) + t.exponent              # value = 0.<raw> × 10^n
    digits = raw.rstrip("0") or "0"
    k = len(digits)
    if k <= n <= 21:
        s = digits + "0" * (n - k)
    elif 0 < n <= 21:
        s = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        s = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        s = digits[0] + ("." + digits[1:] if k > 1 else "") + "e" + ("+" if e >= 0 else "-") + str(abs(e))
    return sign + s


def _jcs(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, (bool, int, float)):
        return _jcs_number(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(_jcs(i) for i in v) + "]"
    if isinstance(v, dict):
        for key in v:
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
        keys = sorted(v, key=lambda s: s.encode("utf-16-be"))   # UTF-16 code-unit order
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + _jcs(v[k]) for k in keys) + "}"
    raise TypeError(f"not JSON-serialisable: {type(v).__name__}")


def jcs(value) -> bytes:
    """RFC 8785 canonical UTF-8 bytes of a JSON value."""
    return _jcs(value).encode("utf-8")


def record_hash(rec: dict) -> str:
    """§6.1: hex(SHA-256(JCS(record with any "batch" object removed)))."""
    return hashlib.sha256(jcs({k: v for k, v in rec.items() if k != "batch"})).hexdigest()


# ─────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────
def _uuid4_from(label: str, seed: str) -> str:
    b = bytearray(hashlib.sha256(f"{label}:{seed}".encode()).digest()[:16])
    b[6] = (b[6] & 0x0F) | 0x40     # version 4
    b[8] = (b[8] & 0x3F) | 0x80     # RFC 4122 variant
    h = b.hex()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def _agent_uri(name: str) -> str:
    return "urn:action-mirror:agent:" + urllib.parse.quote(str(name), safe="")


def to_aat(ledger_path: str, *, session_id: str | None = None,
           agent_version: str = "0.0.0", trust_level: str = "L0",
           principal: dict | None = None, default_outcome: str = "success") -> list[dict]:
    """Convert a verified action-mirror ledger into AAT records (draft -11).

    Raises ValueError if the native chain does not verify, or an argument is not an
    allowed -11 value. See the module docstring for every default and its meaning.
    """
    chain = verify_chain(ledger_path)
    if any(f.level == "FAIL" for f in chain):
        raise ValueError("native chain does not verify — refusing to export "
                         f"(re-chaining would hide tampering): {chain[0].msg}")
    if trust_level not in TRUST_LEVELS:
        raise ValueError(f"trust_level must be one of {TRUST_LEVELS}")
    if default_outcome not in OUTCOMES:
        raise ValueError(f"default_outcome must be one of {OUTCOMES}")
    if not _SEMVER.match(agent_version):
        raise ValueError("agent_version must be SemVer (e.g. 1.2.3)")
    if principal is not None:
        if principal.get("principal_type") not in PRINCIPAL_TYPES:
            raise ValueError(f"principal_type must be one of {PRINCIPAL_TYPES}")
        if not principal.get("principal_id"):
            raise ValueError("principal_id is required")

    entries = _load_entries(ledger_path)
    if not entries:
        return []
    if session_id is None:
        session_id = _uuid4_from("session", str(entries[0].get("seal")))
    elif not _UUID4.match(session_id):
        raise ValueError("session_id must be a lowercase UUIDv4")

    out: list[dict] = []
    prev: dict | None = None
    for e in entries:
        agent_id = _agent_uri(e.get("agent") or e.get("_type", "unknown"))
        if e.get("_type") == "peer_witness":
            action_type = "decision"
            detail = {"decision_type": "action_mirror.peer_witness",
                      "peer": e.get("peer"), "peer_entries": e.get("peer_entries"),
                      "peer_head_seal": e.get("peer_head_seal")}
        else:
            action_type = "tool_call"
            detail = {"tool_name": str(e.get("action")),
                      "parameters_hash": hashlib.sha256(jcs(e.get("payload", {}))).hexdigest()}
            if "target" in e:
                detail["target"] = e["target"]
        detail["export_profile"] = {"draft": DRAFT, "draft_date": DRAFT_DATE,
                                    "exporter": "action-mirror"}
        detail["action_mirror"] = e          # native entry, incl. seal / prev_seal / sig

        payload = e.get("payload")
        exit_code = payload.get("exit") if isinstance(payload, dict) else None
        if isinstance(exit_code, int) and not isinstance(exit_code, bool):
            outcome = "success" if exit_code == 0 else "failure"
        else:
            outcome = default_outcome

        rec = {
            "record_id": _uuid4_from("record", str(e.get("seal"))),
            "timestamp": e.get("ts"),
            "agent_id": agent_id,
            "principal": dict(principal) if principal is not None else
                         {"principal_type": "agent", "principal_id": agent_id,
                          "authority_ref": None},
            "agent_version": agent_version,
            "session_id": session_id,
            "action_type": action_type,
            "action_detail": detail,
            "outcome": outcome,
            "trust_level": trust_level,
            "parent_record_id": prev["record_id"] if prev else None,
            "prev_hash": record_hash(prev) if prev else None,
            "record_phase": "post_execution",
        }
        rec["principal"].setdefault("authority_ref", None)
        if "content_hash" in e and _HEX64.match(str(e["content_hash"])):
            rec["output_hash"] = e["content_hash"]
        out.append(rec)
        prev = rec
    return out


def export_aat(ledger_path: str, out_path: str, **kw) -> int:
    """Write the AAT export as JSON Lines (UTF-8, no BOM, LF; draft §10.1).
    Returns the number of records written."""
    recs = to_aat(ledger_path, **kw)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(recs)


def _instant(ts: str) -> datetime:
    """RFC 3339 → aware datetime (fraction cut to µs; Python 3.10 has no 'Z')."""
    m = re.match(r"^(.*T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$", ts)
    frac = (m.group(2) or "")[:6].ljust(6, "0")
    tz = "+00:00" if m.group(3) == "Z" else m.group(3)
    return datetime.fromisoformat(f"{m.group(1)}.{frac}{tz}")


# ─────────────────────────────────────────────────────────────
# Structural verification of an AAT file (subset of -11)
# ─────────────────────────────────────────────────────────────
def verify_aat(path: str) -> list[Finding]:
    """Check an AAT JSON Lines file against the parts of -11 this module implements:
    REQUIRED fields and enums (§3.1), genesis nulls, §6.1 prev_hash chain,
    parent_record_id linkage, one session_id, non-decreasing timestamps, unique
    record_id. Signatures, trust-level rules, batching and §8 session records are
    NOT checked. Passing means "structurally consistent", not "true"."""
    recs: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                return [Finding("📜 aat", "FAIL", f"Line {i} is not valid JSON.")]
    if not recs:
        return [Finding("📜 aat", "WARN", "No records.")]
    seen: set[str] = set()
    for i, r in enumerate(recs, 1):
        missing = [k for k in REQUIRED if k not in r]
        if missing:
            return [Finding("📜 aat", "FAIL", f"Record {i}: missing REQUIRED {missing}.")]
        bad = []
        if not _UUID4.match(str(r["record_id"])):
            bad.append("record_id")
        if not _UUID4.match(str(r["session_id"])):
            bad.append("session_id")
        if not isinstance(r["timestamp"], str) or not _RFC3339.match(r["timestamp"]):
            bad.append("timestamp")
        if r["action_type"] not in ACTION_TYPES:
            bad.append("action_type")
        if r["outcome"] not in OUTCOMES:
            bad.append("outcome")
        if r["trust_level"] not in TRUST_LEVELS:
            bad.append("trust_level")
        if r["record_phase"] not in RECORD_PHASES:
            bad.append("record_phase")
        if not isinstance(r["action_detail"], dict):
            bad.append("action_detail")
        p = r["principal"]
        if not isinstance(p, dict) or p.get("principal_type") not in PRINCIPAL_TYPES \
                or not p.get("principal_id") or "authority_ref" not in p:
            bad.append("principal")
        if not isinstance(r["agent_version"], str) or not _SEMVER.match(r["agent_version"]):
            bad.append("agent_version")
        if bad:
            return [Finding("📜 aat", "FAIL", f"Record {i}: invalid {bad}.")]
        if r["record_id"] in seen:
            return [Finding("📜 aat", "FAIL", f"Record {i}: duplicate record_id.")]
        seen.add(r["record_id"])
        if i == 1:
            if r["prev_hash"] is not None or r["parent_record_id"] is not None:
                return [Finding("📜 aat", "FAIL",
                                "Record 1: genesis must have prev_hash and parent_record_id null.")]
            continue
        prev = recs[i - 2]
        if r["prev_hash"] != record_hash(prev):
            return [Finding("📜 aat", "FAIL",
                            f"Record {i}: prev_hash broken — record {i-1} modified, "
                            "or a record was inserted/deleted/reordered.")]
        if r["parent_record_id"] != prev["record_id"]:
            return [Finding("📜 aat", "FAIL", f"Record {i}: parent_record_id does not link.")]
        if r["session_id"] != recs[0]["session_id"]:
            return [Finding("📜 aat", "FAIL", f"Record {i}: session_id changes mid-file.")]
        if _instant(r["timestamp"]) < _instant(prev["timestamp"]):
            return [Finding("📜 aat", "FAIL", f"Record {i}: timestamp decreases.")]
    return [Finding("📜 aat", "OK",
                    f"{len(recs)} records structurally consistent with {DRAFT} "
                    "(fields, enums, prev_hash chain). Signatures not checked; "
                    "the last record can be truncated without detection.")]
