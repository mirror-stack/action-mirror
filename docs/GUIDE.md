# 🪪 Action Mirror — Guide

> **Audience**: anyone running a family of agents (or scripts, or people) who
> needs to prove *who did what* after the fact, and detect ledger tampering
> without an external service.
>
> **Companion**: [README](../README.md) · [CHANGELOG](../CHANGELOG.md)
> **한국어**: [GUIDE_KO.md](GUIDE_KO.md)

---

## Philosophy: record, prove, admit unknown

Action Mirror does not prevent bad behaviour — it makes behaviour **provable**.
Two layers on one chain-hashed ledger:

- **Action provenance** — what happened, sealed (`record`, `attest`)
- **Mutual witness** — agents pin each other so no one can rewrite history alone

The honesty default matters: `NOT-FOUND` is *not* "it never happened", only
"this ledger never sealed it". Absence of record ≠ proof of absence.

---

## A. Action provenance

### `record(ledger, *, agent, action, target, payload, content)`

Seal one action. `agent`/`action`/`target` are free strings — works for agents,
training runs, CI steps, humans, build artifacts. `content` (bytes/str) is
stored as a 16-hex SHA-256 only — never the bytes themselves.

```python
from actmirror import am
am.record("jebi.jsonl", agent="jebi", action="eval_run",
          target="exp1_result.txt", content=result_bytes,
          payload={"script": "eval.py", "exit": 0})
```

### `history(ledger, *, agent, action, target)`

Query sealed actions with optional filters. Returns the matching entries.

```python
am.history("jebi.jsonl", agent="jebi")             # all of jebi's actions
am.history("jebi.jsonl", target="exp1_result.txt") # everyone who touched it
```

### `attest(ledger, *, agent, action, target, content)`

The payoff: prove "did X do Y to Z?" — and catch artifacts altered afterwards.

| Verdict | When |
|---|---|
| `ATTESTED` | matching record(s) found (content hash verified when `content` given) |
| `CONTENT-MISMATCH` | action recorded, but the presented content's hash differs — the artifact was modified after the recorded action |
| `NOT-FOUND` | no matching record (honest: ≠ proof the event never happened) |

```python
am.attest("jebi.jsonl", agent="jebi", target="exp1_result.txt",
          content=result_bytes)
# ATTESTED                         ← bytes match the sealed hash
am.attest("jebi.jsonl", agent="jebi", target="exp1_result.txt",
          content=doctored_bytes)
# CONTENT-MISMATCH                 ← someone edited it after recording
```

### `verify_chain(ledger) → [Finding]`

Full integrity check: recompute every seal, confirm every `prev_seal` link.
Catches content modification, entry deletion, insertion, and reorder.

```python
am.report("chain", am.verify_chain("jebi.jsonl"))
# ✅ [⛓ chain] Chain intact — 12 entries verified.
# 🔴 [⛓ chain] Entry 5: prev_seal broken — deletion/insertion/reorder detected.
```

---

## B. Mutual witness network

A chain hash cannot detect two attacks on its own:
**tail truncation** (a shorter chain is still internally valid) and
**complete replacement** (a freshly built chain is internally valid). This is
measure-mirror's documented gap. Action Mirror closes it locally.

### `witness_peer(my_ledger, peer_ledger, *, peer_name)`

Pin a snapshot of the peer's head into my ledger: "at time T, peer had N
entries and entry N's seal was X". Later appends by the peer are fine;
truncating or rewriting entry ≤ N becomes permanently detectable.

### `verify_peer(my_ledger, peer_ledger, *, peer_name) → Finding`

Check the peer against every witness record I hold.

| Level | When |
|---|---|
| `OK` | every pinned head still present at its position (append-only respected) |
| `FAIL` | a pinned head no longer matches — `TRUNCATED` or `REWRITTEN` |
| `WARN` | I hold no witness records for this peer — nothing to verify |

```python
am.witness_peer("seara.jsonl", "jebi.jsonl", peer_name="jebi")  # pin now
# ... jebi keeps appending — still OK ...
# ... jebi rewrites its ledger from scratch ...
am.verify_peer("seara.jsonl", "jebi.jsonl", peer_name="jebi")
# 🔴 Peer 'jebi' ledger ROLLBACK detected: REWRITTEN: entry 3 seal ≠ head ...
```

### `cross_witness` / `family_round` / `family_verify`

```python
ledgers = {"seara": "seara.jsonl", "jebi": "jebi.jsonl", "sonnet": "sonnet.jsonl"}
am.family_round(ledgers)      # every agent pins every other (n·(n-1) records)
am.family_verify(ledgers)     # one Finding per observer→peer pair
```

**The guarantee**: to erase one agent's history, the attacker must rewrite
every family ledger simultaneously and consistently. Each added agent is
another independent lock.

---

## Universal use

```bash
pip install -e ~/action_mirror_poc --user      # `am` everywhere
export AM_LEDGER=~/mirror_ledgers/<agent>.jsonl
```

Recording must happen at **boundaries**, not by goodwill:

```bash
# training script — last line
am record --agent neoul-trainer --action train_run \
   --target results/pretrain.log --content-file results/pretrain.log

# git post-commit hook (.git/hooks/post-commit)
am record --agent "$(git config user.name)" --action commit \
   --target "$(git rev-parse HEAD)"
```

### Claude Code integration

A `PostToolUse` hook turns any Claude Code agent self-recording:

```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Write|Edit|Bash",
      "hooks": [{
        "type": "command",
        "command": "am --ledger ~/mirror_ledgers/$USER.jsonl record --agent $USER --action tool_use --target \"$CLAUDE_TOOL_NAME\""
      }]
    }]
  }
}
```

A cron/loop running `family_round()` then gives the whole family continuous
mutual tamper-evidence. Wire both up deliberately, not by default.

---

## Honest threat model

- Tamper-evidence **within a family**, not host security. Holds when ledgers
  live in separate trust domains. A root attacker on one machine holding all
  ledgers can rewrite everything — nothing local-only stops that.
- Local clock: sealed **order** is trustworthy, wall-clock time is not proof.
- `NOT-FOUND` is not innocence — recording must be enforced at the boundary.
- Witness pins protect history up to the last round; pin frequently.

---

## Quick reference

| Function | Returns | Purpose |
|---|---|---|
| `record` | dict | seal one action |
| `history` | list | query sealed actions |
| `attest` | dict | prove "did X do Y?" (ATTESTED / CONTENT-MISMATCH / NOT-FOUND) |
| `verify_chain` | [Finding] | chain integrity of my ledger |
| `witness_peer` | dict | pin a peer's head into my ledger |
| `verify_peer` | Finding | check a peer against my witness records |
| `cross_witness` | tuple | mutual pin between two ledgers |
| `family_round` | list | every agent pins every other |
| `family_verify` | [Finding] | verify the whole family |
| `verify_signatures` | [Finding] | verify the optional signed-identity layer |
| `load_trusted_keys` | dict | read a trusted-keys file |

---

### `verify_signatures(ledger) → [Finding]`

Verifies the optional Ed25519 signed-identity layer (`[signing]` extra). Entries created with
`record(..., sign_key=...)` carry a chained `pubkey` and a `sig` over the (recomputed) seal.
`verify_signatures` confirms each signature, catching tampering (body changed after signing).
It catches impersonation only with `trusted_keys` (agent -> pubkeys, read with
`load_trusted_keys(path)` from `<agent> <pubkey_hex>` lines): a signature by a key not trusted for
the entry's agent FAILs, and so does an unsigned entry from an agent that has a trusted key.
Without trusted keys it checks only that the key written in the entry signed it. Entries with no `pubkey` are unsigned and pass as OK (the
`who` is self-asserted). **Attribution only — not independence**: one operator can hold many keys.

```python
am.report("identity", am.verify_signatures(
    "jebi.jsonl", trusted_keys=am.load_trusted_keys("trusted_keys.txt")))
```

---

*Built as a sister to measure-mirror, under one discipline:*
*record what happened, prove what you can, say "unknown" about the rest.*
