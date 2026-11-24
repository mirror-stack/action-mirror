"""Concurrent appenders must not fork the chain.

record() reads the head seal and then writes a line pointing at it. Before the ledger
lock, two processes could read the same head and both append after it, and
verify_chain() reported the honest second line as a deletion/insertion (reproduced
2026-11-24 with 4 processes x 50 record() calls). Real processes, not threads: the race
is between cron jobs / sibling agents sharing one ledger.
"""
import json
import os
import subprocess
import sys
import textwrap

from actmirror import am

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WRITER = textwrap.dedent("""
    import sys
    from actmirror import am
    path, wid, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
    for j in range(n):
        am.record(path, agent=f"a{wid}", action="test", target=f"t{j}")
""")


def test_concurrent_appenders_keep_one_chain(tmp_path):
    path = str(tmp_path / "actions.jsonl")
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    procs = [subprocess.Popen([sys.executable, "-c", WRITER, path, str(w), "30"],
                              env=env, stderr=subprocess.PIPE, text=True)
             for w in range(4)]
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err

    assert [f.level for f in am.verify_chain(path)] == ["OK"]
    with open(path, encoding="utf-8") as f:
        lines = [json.loads(l) for l in f if l.strip()]
    assert len(lines) == 4 * 30
    assert len({e["prev_seal"] for e in lines}) == len(lines)


def test_lock_is_released_after_a_failed_append(tmp_path):
    path = str(tmp_path / "actions.jsonl")

    class Unserializable:
        pass

    try:
        am.record(path, agent="x", action="y", payload={"bad": Unserializable()})
    except TypeError:
        pass
    with am._ledger_lock(path, timeout=1.0):   # would time out if the lock leaked
        pass
    am.record(path, agent="x", action="ok")
    assert [f.level for f in am.verify_chain(path)] == ["OK"]
