# Changelog

All notable changes to Action Mirror are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.3.0] — 2026-08-14

### Fixed
- **Verdicts now reach the exit code.** Every CLI path exited 0, so
  `am verify && …` proceeded on a tampered ledger — the 🔴 FAIL was print-only.
  Found live, not hypothetically: a commit-binding tool trusted the exit code
  and its own tamper demo (a byte-flipped ledger) came back green.
  Now: `verify` / `verify-peer` / `verify-sig` exit 1 on FAIL (0 on OK/WARN);
  `attest` exits 0 only on ATTESTED (1 on CONTENT-MISMATCH and NOT-FOUND).
  Commands with no verdict (`record`, `history`, `witness`, `cross`, `keygen`)
  keep exiting 0. Scripts that already parsed the output are unaffected;
  scripts that trusted the exit code were getting a constant — any change is
  strictly more information.

### Added
- `tests/test_cli_exit_codes.py` — subprocess-level checks that each verdict
  maps to the documented exit code, including the exact tampered-ledger case
  the defect shipped.

---

## [0.2.0] — 2026-07-17

### Security
- **Seal/hash width: 16-hex (64-bit) truncation → full 64-hex SHA-256**
  (chain seals, `content_hash`, `peer_anchor`) — closes the dishonest-sealer
  birthday-collision gap (~2^32). Legacy 16-hex values keep verifying via
  prefix match (`_hash_matches`); signatures over legacy truncated seals
  still verify. Mixed chains supported; no migration needed.

### Added
- `tests/test_adversarial.py` — 14 attack-scenario tests: mid-chain deletion,
  forged insertion, tail truncation, whole-ledger replacement, content edits,
  re-sealing, legacy-chain attacks, content-hash forgery — including honest
  documentation of what only the witness layer can catch.

## [0.1.0] — 2026-06-12

First proof-of-concept. Tamper-evidence for agent behaviour within a family of
agents whose ledgers live in separate trust domains (see README threat model).

### Added — action provenance (`actmirror.am`)
- **`record(ledger, *, agent, action, target, payload, content)`** — seal one
  agent action into an append-only chain-hashed ledger. Content is stored as a
  SHA-256 hash only (privacy + size).
- **`history(ledger, *, agent, action, target)`** — query sealed actions.
- **`attest(ledger, *, agent, action, target, content)`** — answer
  "did agent X do Y to Z?":
  - `ATTESTED` — matching sealed record(s) found (content hash verified if given)
  - `CONTENT-MISMATCH` — recorded, but the artifact was modified afterwards
  - `NOT-FOUND` — no sealed record (honest: absence of record ≠ proof of absence)
- **`verify_chain(ledger)`** — full chain verification (recompute every seal,
  every link). Catches modification, deletion, insertion, reorder.

### Added — mutual witness network (the rollback killer)
- **`witness_peer(my_ledger, peer_ledger, *, peer_name)`** — pin a peer ledger's
  head (entry_count + head_seal) into my ledger, position-anchored.
- **`verify_peer(my_ledger, peer_ledger, *, peer_name)`** — check a peer against
  every witness record I hold. Catches truncation and complete replacement —
  the two attacks a chain hash alone cannot detect.
- **`cross_witness` / `family_round` / `family_verify`** — mutual / whole-family
  witness rounds. To erase one agent's history, an attacker must rewrite every
  family member's ledger simultaneously.

### Added — tooling
- **CLI `am`** (`pip install -e .`): `record`, `history`, `attest`, `verify`,
  `witness`, `verify-peer`, `cross`. Ledger default from `$AM_LEDGER`.
- **`examples/demo_family.py`** — 3-agent NACC-style family, attack included.
- 21 tests, all passing. Zero dependencies.

### Design
- **Record what happened, prove what you can, say "unknown" about the rest.**
- Same DNA as measure-mirror: zero-training, deterministic, sealed ledger, honest.
- **Honest threat model**: tamper-evidence within a family, not host security.
  A root-level attacker on one machine holding all ledgers can rewrite
  everything; nothing local-only stops that. Recording must be enforced at the
  boundary (hooks), not trusted to agent goodwill.
