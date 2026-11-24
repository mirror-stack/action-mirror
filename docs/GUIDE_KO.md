# 🪪 Action Mirror — 완전 가이드

> **대상 독자**: 에이전트(또는 스크립트, 사람) 가족을 운영하며 사후에 *누가 뭘
> 했는지* 증명하고, 외부 서비스 없이 원장 변조를 탐지해야 하는 누구나.
>
> **관련**: [README_KO](../README_KO.md) · [CHANGELOG](../CHANGELOG.md)
> **English**: [GUIDE.md](GUIDE.md)

---

## 철학: 기록하고, 증명하고, 모름을 인정한다

행동거울은 나쁜 행동을 막지 않습니다 — 행동을 **증명 가능**하게 만듭니다. 하나의
체인해시 원장 위 두 층:

- **행동 원장** — 일어난 일을 봉인 (`record`, `attest`)
- **상호증인** — 에이전트들이 서로를 핀해서 아무도 혼자 역사를 못 고침

정직성 기본값이 중요합니다: `NOT-FOUND`는 "일어나지 않았다"가 *아니라* "이 원장이
봉인 안 했다"일 뿐. 기록 부재 ≠ 부재 증명.

---

## A. 행동 원장 (Action Provenance)

### `record(ledger, *, agent, action, target, payload, content)`

행동 하나를 봉인. `agent`/`action`/`target`은 자유 문자열 — 에이전트, 훈련 런, CI
단계, 사람, 빌드 산출물 전부 가능. `content`(bytes/str)는 16진 SHA-256만 저장 —
바이트 자체는 절대 저장 안 함.

```python
from actmirror import am
am.record("jebi.jsonl", agent="jebi", action="eval_run",
          target="exp1_result.txt", content=result_bytes,
          payload={"script": "eval.py", "exit": 0})
```

### `history(ledger, *, agent, action, target)`

봉인된 행동을 선택적 필터로 조회. 일치 엔트리 반환.

```python
am.history("jebi.jsonl", agent="jebi")             # 제비의 모든 행동
am.history("jebi.jsonl", target="exp1_result.txt") # 그 파일을 건드린 전원
```

### `attest(ledger, *, agent, action, target, content)`

보상: "X가 Y를 Z에 했나?" 증명 — 그리고 이후 변조된 산출물 적발.

| 판정 | 조건 |
|---|---|
| `ATTESTED` | 일치 기록 발견 (`content` 주면 해시 검증) |
| `CONTENT-MISMATCH` | 행동 기록됨, 그러나 제시된 내용 해시가 다름 — 기록 후 산출물 수정됨 |
| `NOT-FOUND` | 일치 기록 없음 (정직: ≠ 사건이 없었다는 증명) |

```python
am.attest("jebi.jsonl", agent="jebi", target="exp1_result.txt",
          content=result_bytes)
# ATTESTED                         ← 바이트가 봉인 해시와 일치
am.attest("jebi.jsonl", agent="jebi", target="exp1_result.txt",
          content=doctored_bytes)
# CONTENT-MISMATCH                 ← 기록 후 누가 수정함
```

### `verify_chain(ledger) → [Finding]`

완전 무결성 검사: 모든 seal 재계산, 모든 `prev_seal` 링크 확인. 내용 수정, 엔트리
삭제, 삽입, 재배열 적발.

```python
am.report("chain", am.verify_chain("jebi.jsonl"))
# ✅ [⛓ chain] Chain intact — 12 entries verified.
# 🔴 [⛓ chain] Entry 5: prev_seal broken — deletion/insertion/reorder detected.
```

---

## B. 상호증인 네트워크

체인 해시는 단독으로 두 공격을 못 잡습니다: **꼬리 절단**(짧은 체인도 내부적으로
유효)과 **통째 교체**(새로 만든 체인도 내부적으로 유효). 이건 측정거울의 문서화된
구멍입니다. 행동거울은 이를 로컬에서 닫습니다.

### `witness_peer(my_ledger, peer_ledger, *, peer_name)`

동료 head 스냅샷을 내 원장에 핀: "시각 T에 동료는 N 엔트리, 엔트리 N의 seal은 X".
이후 동료의 추가는 OK; 엔트리 ≤ N의 절단·재작성은 영구 탐지됨.

### `verify_peer(my_ledger, peer_ledger, *, peer_name) → Finding`

내가 가진 모든 증인 기록과 동료를 대조.

| 레벨 | 조건 |
|---|---|
| `OK` | 핀된 head 전부 위치 유지 (추가전용 준수) |
| `FAIL` | 핀된 head 불일치 — `TRUNCATED` 또는 `REWRITTEN` |
| `WARN` | 이 동료 증인 기록 없음 — 검증 불가 |

```python
am.witness_peer("seara.jsonl", "jebi.jsonl", peer_name="jebi")  # 지금 핀
# ... 제비가 계속 추가 — 여전히 OK ...
# ... 제비가 원장을 처음부터 재작성 ...
am.verify_peer("seara.jsonl", "jebi.jsonl", peer_name="jebi")
# 🔴 Peer 'jebi' ledger ROLLBACK detected: REWRITTEN: entry 3 seal ≠ head ...
```

### `cross_witness` / `family_round` / `family_verify`

```python
ledgers = {"seara": "seara.jsonl", "jebi": "jebi.jsonl", "sonnet": "sonnet.jsonl"}
am.family_round(ledgers)      # 전원이 전원을 핀 (n·(n-1) 기록)
am.family_verify(ledgers)     # observer→peer 쌍마다 Finding 하나
```

**보장**: 한 에이전트의 역사를 지우려면 공격자는 가족 전원 원장을 동시에 일관되게
재작성해야 함. 에이전트가 늘수록 독립 자물쇠가 늘어남.

---

## 범용 사용

```bash
pip install -e ~/action_mirror_poc --user      # 어디서든 `am`
export AM_LEDGER=~/mirror_ledgers/<agent>.jsonl
```

기록은 선의가 아니라 **경계**에서:

```bash
# 훈련 스크립트 — 마지막 줄
am record --agent neoul-trainer --action train_run \
   --target results/pretrain.log --content-file results/pretrain.log

# git post-commit hook (.git/hooks/post-commit)
am record --agent "$(git config user.name)" --action commit \
   --target "$(git rev-parse HEAD)"
```

### Claude Code 통합

`PostToolUse` hook이 임의의 Claude Code 에이전트를 자기기록형으로:

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

cron/loop가 `family_round()`를 돌리면 가족 전체에 상시 상호 변조증거. 둘 다
기본값 아닌 *의도적* 배선으로.

---

## 정직한 위협 모델

- **가족 내** 변조증거이지 호스트 보안 아님. 원장이 서로 다른 신뢰 도메인에 있을
  때 성립. 모든 원장을 가진 단일 머신 root 공격자는 전부 재작성 가능 — 로컬 전용은
  못 막음.
- 로컬 시계: 봉인 **순서**는 신뢰, 절대 시각은 증명 아님.
- `NOT-FOUND`는 결백 아님 — 기록은 경계에서 강제해야 함.
- 증인 핀은 마지막 라운드까지 역사 보호; 자주 핀.

---

## 퀵 레퍼런스

| 함수 | 반환 | 용도 |
|---|---|---|
| `record` | dict | 행동 하나 봉인 |
| `history` | list | 봉인 행동 조회 |
| `attest` | dict | "X가 Y 했나?" 증명 (ATTESTED / CONTENT-MISMATCH / NOT-FOUND) |
| `verify_chain` | [Finding] | 내 원장 체인 무결성 |
| `witness_peer` | dict | 동료 head를 내 원장에 핀 |
| `verify_peer` | Finding | 내 증인 기록과 동료 대조 |
| `cross_witness` | tuple | 두 원장 상호 핀 |
| `family_round` | list | 전원이 전원을 핀 |
| `family_verify` | [Finding] | 가족 전체 검증 |
| `verify_signatures` | [Finding] | 선택적 서명-신원 레이어 검증 |
| `load_trusted_keys` | dict | 신뢰 키 파일 읽기 |

---

### `verify_signatures(ledger) → [Finding]`

선택적 Ed25519 서명-신원 레이어(`[signing]` extra)를 검증한다. `record(..., sign_key=...)`로 만든
엔트리는 체인된 `pubkey`와 (재계산한) seal에 대한 `sig`를 단다. `verify_signatures`는 각 서명을
확인해 위조(서명 후 본문 변경)를 잡는다. 사칭은 `trusted_keys`(`load_trusted_keys(path)`로 읽은
`에이전트 -> 공개키` 목록)를 줄 때만 잡는다: 그 에이전트에게 신뢰되지 않은 키의 서명과, 신뢰 키가 있는
에이전트의 무서명 엔트리가 FAIL. 신뢰 키 없이는 "엔트리 안의 키로 서명됨"까지만 확인한다. `pubkey` 없는 엔트리는 무서명이라
OK 통과(`누구`는 자기선언). **귀속만 — 독립 아님**: 한 운영자가 키를 여럿 들 수 있다.

```python
am.report("identity", am.verify_signatures(
    "jebi.jsonl", trusted_keys=am.load_trusted_keys("trusted_keys.txt")))
```

---

*측정거울의 자매로, 하나의 규율 아래 제작:*
*일어난 일을 기록하고, 증명 가능한 것을 증명하고, 나머진 "모른다"고.*
