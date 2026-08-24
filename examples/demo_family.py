"""
🪪 Demo: NACC-style agent family — action provenance + mutual witness.

Scenario:
  seara  (relay)    issues ticket 0034
  jebi   (verifier) runs an eval and seals the result artifact's hash
  sonnet (reviewer) reviews and seals approval
  → one witness round (everyone pins everyone)
  → attest: "did jebi really produce result 0.72?" (and catch a doctored copy)
  → jebi's ledger gets secretly REPLACED with a clean-looking history
     · chain check alone:   fooled (the new chain is internally valid)
     · family witnesses:    both seara and sonnet catch the rollback

Run:  PYTHONPATH=. python examples/demo_family.py
"""
import os
import tempfile

from actmirror import am

# This script prints the same emoji the CLI does, but it does not go through `_cli()`,
# so it did not inherit the console fallback that lives there — and on a cp1252 console
# it died with UnicodeEncodeError. An example is the FIRST code a new user runs; failing
# here is worse than failing in a test. Private import on purpose: this is the package's
# own example, and the helper is not part of the public API.
am._printable_streams()

D = tempfile.mkdtemp(prefix="am_demo_")
ledgers = {n: os.path.join(D, f"{n}.jsonl") for n in ["seara", "jebi", "sonnet"]}

# ── 1. A day of family work, every action sealed ─────────────
am.record(ledgers["seara"], agent="seara", action="ticket",
          target="tickets/0034", payload={"to": "jebi", "task": "eval exp1"})

result_bytes = b"exp1: acc=0.72 n=500 seed=42"
am.record(ledgers["jebi"], agent="jebi", action="eval_run",
          target="exp1_result.txt", content=result_bytes,
          payload={"script": "eval.py", "exit": 0})

am.record(ledgers["sonnet"], agent="sonnet", action="review",
          target="exp1_result.txt", payload={"verdict": "approve"})

# ── 2. One mutual-witness round ──────────────────────────────
wits = am.family_round(ledgers)
print(f"👁 Witness round: {len(wits)} cross-pins sealed (3 agents × 2 peers)")

# ── 3. Attestation: who did what, provably ───────────────────
print("\n── attest: did jebi produce exp1_result.txt with these bytes? ──")
res = am.attest(ledgers["jebi"], agent="jebi", target="exp1_result.txt",
                content=result_bytes)
print(f"  ✅ {res['verdict']}: {res['note']}")

print("\n── attest: someone presents a DOCTORED copy (0.72 → 0.99) ──")
res = am.attest(ledgers["jebi"], agent="jebi", target="exp1_result.txt",
                content=b"exp1: acc=0.99 n=500 seed=42")
print(f"  🔴 {res['verdict']}: {res['note']}")

# ── 4. The attack: jebi's ledger is silently REPLACED ────────
print("\n── attack: jebi's ledger replaced with a clean-looking history ──")
os.remove(ledgers["jebi"])
am.record(ledgers["jebi"], agent="jebi", action="eval_run",
          target="exp1_result.txt", content=b"exp1: acc=0.99 n=500 seed=42")

chain = am.verify_chain(ledgers["jebi"])[0]
print(f"  chain check alone:  {chain.level} — \"{chain.msg}\"  ← fooled!")

am.report("family verify (after attack)", am.family_verify(ledgers))

print(f"\n(ledgers: {D})")
