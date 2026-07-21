# 🪪 Action Mirror

<p align="center">
  <img src="docs/action_mirror_og.png" alt="Action Mirror" width="500">
</p>

**Agent action provenance + mutual witness network.**
Third member of the mirror family — same DNA, new domain:

| Tool | Audits | Question |
|---|---|---|
| 🪞 [measure-mirror](https://github.com/mirror-stack/measure-mirror) | AI evaluation claims | Is the **claim** honest? |
| 🔎 [provenance-mirror](https://github.com/mirror-stack/provenance-mirror) | Content authenticity | Is the **origin** proven? |
| 🪪 **action-mirror** (you are here) | Agent behaviour | **Who did what, provably?** |
| 👁 [mirror-witness](https://github.com/mirror-stack/mirror-witness) | Cross-operator witness board | Who else **witnessed** it? |

> **Ledger format**: action ledgers conform to
> **[MIRROR-SPEC v1.0](https://github.com/mirror-stack/measure-mirror/blob/main/docs/SPEC.md)**
> (the family's normative format, ratified 2026-07-02) — this package is a reference
> implementation. Note: `am` writes the genesis marker as `"GENESIS"`; the spec
> compares it case-insensitively (§5.1), so both `"GENESIS"` and `"genesis"` are valid.

The four together = the 🪞🔎🪪 [Mirror Stack](https://github.com/mirror-stack/measure-mirror/tree/main/stack).

💬 **[Discussions](https://github.com/orgs/mirror-stack/discussions)** — questions · ideas · independent reproductions welcome.

> Zero training · Deterministic · Zero dependencies (Python 3.10+ stdlib only).

**[📖 Full Guide →](docs/GUIDE.md)** · [한국어 README](README_KO.md)

---

## Why

AI agents increasingly do real work — write files, run evals, commit code,
handle tickets. When something goes wrong (or someone claims it did), the
question becomes: **which agent did what, and can you prove it?**

Action Mirror answers with two mechanisms on one chain-hashed ledger:

> **It *records*, it does not *intercept*.** `am.record(...)` is a call the agent (or you)
> makes — Action Mirror does **not** auto-hook, proxy, or intercept an agent's actions, and an
> agent that never calls it leaves no trace (that absence is itself the signal). Automatic
> interception lives at a different layer (a harness hook / wrapper) and is out of scope here.
> The guarantee is not "every action is captured" — it is "what *is* recorded cannot be altered
> or replaced without detection."

### A. Action provenance

Every action is sealed into an append-only chain. Content is recorded as a
SHA-256 hash only (privacy + size).

```python
from actmirror import am

# jebi runs an eval and seals the artifact's hash
am.record("jebi.jsonl", agent="jebi", action="eval_run",
          target="exp1_result.txt", content=result_bytes,
          payload={"script": "eval.py", "exit": 0})

# later: "did jebi really produce THIS result?"
am.attest("jebi.jsonl", agent="jebi", target="exp1_result.txt",
          content=result_bytes)
# → ATTESTED (hash verified)  ·  doctored copy → CONTENT-MISMATCH
```

| Verdict | Meaning |
|---|---|
| ✅ `ATTESTED` | Sealed record matches (content hash verified when given) |
| 🔴 `CONTENT-MISMATCH` | Action recorded, but the artifact was **modified afterwards** |
| ⚪ `NOT-FOUND` | No sealed record — honest note: *absence of record ≠ proof of absence* |

### B. Mutual witness — the rollback killer

A chain hash alone **cannot** detect two attacks:
tail truncation (a shorter chain is still valid) and complete ledger
replacement (a fresh chain is internally valid). This is measure-mirror's
documented gap, solved there by external anchoring.

Action Mirror solves it **locally, with no external service**: agents
periodically pin each other's ledger head (entry count + head seal) into
their own ledgers.

```python
am.family_round({"seara": "seara.jsonl", "jebi": "jebi.jsonl",
                 "sonnet": "sonnet.jsonl"})   # everyone pins everyone

am.family_verify(ledgers)
# jebi secretly replaces its ledger with a clean-looking history:
#   chain check on jebi.jsonl : OK   ← fooled (new chain is valid)
#   seara→jebi witness        : 🔴 ROLLBACK (witnessed 3 entries, now 1)
#   sonnet→jebi witness       : 🔴 ROLLBACK
```

**To erase one agent's history, the attacker must rewrite every family
member's ledger simultaneously and consistently.** Each added agent is
another lock.

---

## Install & universal use

```bash
pip install -e ~/action_mirror_poc --user   # `am` CLI everywhere

# Convention: one ledger per agent under a standard dir
export AM_LEDGER=~/mirror_ledgers/<agent>.jsonl
am record --agent <agent> --action train_run --target run.log --content-file run.log
```

Recording must happen at **boundaries**, not by goodwill — pick yours:

```bash
# training scripts — last line
am record --agent neoul-trainer --action train_run \
   --target results/pretrain.log --content-file results/pretrain.log

# git — .git/hooks/post-commit
am record --agent $(git config user.name) --action commit \
   --target "$(git rev-parse HEAD)"

# Claude Code — PostToolUse hook (see integration section below)
```

## CLI

```bash
am --ledger jebi.jsonl record --agent jebi --action eval_run \
   --target exp1.txt --content-file exp1.txt --payload '{"exit":0}'

am --ledger jebi.jsonl history --agent jebi
am --ledger jebi.jsonl attest --agent jebi --target exp1.txt --content-file exp1.txt
am --ledger jebi.jsonl verify                              # my chain
am --ledger seara.jsonl witness jebi.jsonl --name jebi     # pin peer head
am --ledger seara.jsonl verify-peer jebi.jsonl --name jebi # check peer
am cross seara.jsonl jebi.jsonl --names seara jebi         # mutual pin
```

Demo (3-agent family, attack included): `PYTHONPATH=. python examples/demo_family.py`

## Python API

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

See the **[full Guide](docs/GUIDE.md)** for each function's signature and verdicts.

---

## Signed identity (optional) — making the *who* as hard as the *what*

By default the `agent` field is a **self-asserted label**: the chain proves the *entry* wasn't
altered, not that the named agent is who acted. The optional `[signing]` extra upgrades the *who*
to a verifiable identity with an Ed25519 keypair:

```bash
pip install action-mirror[signing]

am keygen --out jebi.key                                   # one keypair per agent
am record --agent jebi --action eval --target exp1 --sign jebi.key
am verify-sig                                              # checks every signed entry
```

Each signed entry carries a `pubkey` (chained — the claimed key can't be swapped without breaking
the chain) and a `sig` over its seal. `verify_signatures` (CLI `verify-sig`) recomputes the seal
from the content and checks the signature, so it catches **both** impersonation (signed with the
wrong key) **and** tampering (body changed after signing).

**Honest scope — do not oversell.** A signature proves *the holder of this key signed it*
(attribution · impersonation-resistance · non-repudiation). It does **not** prove independence:
one operator can mint many keys (Sybil), and an agent holding its own key can use a different one
— the strongest form needs the *harness* to attest identity. Keys make *same-identity* provable;
they do not make distinct keys *independent*. Unsigned entries remain fully valid (the core stays
zero-dependency); signing is an opt-in layer, not a requirement.

---

## Claude Code integration (the middleware path)

A `PostToolUse` hook turns any Claude Code agent into a self-recording one:

```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Write|Edit|Bash",
      "hooks": [{
        "type": "command",
        "command": "python -m actmirror.am --ledger /data/agents/$USER.jsonl record --agent $USER --action tool_use --target \"$CLAUDE_TOOL_NAME\""
      }]
    }]
  }
}
```

And a cron/loop that runs `family_round()` gives the whole agent family
continuous mutual tamper-evidence. (Both are integration *examples* — wire
them up deliberately, not by default.)

---

## Honest threat model (read before trusting)

- This is **tamper-evidence within a family**, not host security. The
  guarantee holds when ledgers live in separate trust domains (different
  processes, users, machines). A root-level attacker on a single machine
  holding all ledgers can rewrite everything consistently — nothing
  local-only stops that.
- Timestamps are local clock: **sealed order** is trustworthy, wall-clock
  time is not proof.
- `NOT-FOUND` is not innocence: an agent that simply doesn't record an
  action leaves no trace. Recording must be enforced at the boundary
  (hooks/middleware), not trusted to the agent's goodwill.
- Witness pins protect history up to the pinned entry; actions after the
  last round are unprotected until the next round. Pin frequently.

What's solid today: chain-sealed action records, content-hash attestation,
position-pinned mutual witness with truncation/replacement detection —
21 tests, all passing, zero dependencies.

---

## Roadmap (if it earns its keep)

1. Claude Code hook package (`am-hook`) — automatic recording at the tool boundary
2. `family_round` daemon / relay_watch integration (NACC family)
3. Cross-mirror cascade: action records as `depends_on` targets for
   measure-mirror claims (a claim built on unattested actions → WARN)
4. MCP server (`am_attest`, `am_family_verify`) following the family pattern

Built under the mirror discipline:
**record what happened, prove what you can, say "unknown" about the rest.**
