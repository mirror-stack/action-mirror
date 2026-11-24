# 🪪 Action Mirror (행동거울)

<p align="center">
  <img src="docs/action_mirror_og.png" alt="Action Mirror" width="500">
</p>

**에이전트 행동 원장 + 상호증인 네트워크.**
거울 패밀리 3호 — 같은 DNA, 새 도메인:

| 도구 | 감사 대상 | 질문 |
|---|---|---|
| 🪞 [measure-mirror](https://github.com/mirror-stack/measure-mirror) | AI 평가 주장 | **주장**이 정직한가? |
| 🔎 [provenance-mirror](https://github.com/mirror-stack/provenance-mirror) | 콘텐츠 진위 | **출처**가 증명되나? |
| 🪪 **action-mirror** (현재 위치) | 에이전트 행동 | **누가 뭘 했나, 증명 가능하게?** |
| 👁 [mirror-witness](https://github.com/mirror-stack/mirror-witness) | 운영자 간 증인 게시판 | 또 **누가 증인** 섰나? |

> **원장 포맷**: 행동 원장은 패밀리 규범 명세
> **[MIRROR-SPEC v1.1](https://github.com/mirror-stack/measure-mirror/blob/main/docs/SPEC.md)**
> ([한국어 참고 번역](https://github.com/mirror-stack/measure-mirror/blob/main/docs/SPEC_KO.md),
> 2026-07-02 비준)을 따르며, 이 패키지는 그 참조구현입니다. 참고: `am`은 genesis 마커를
> `"GENESIS"`(대문자)로 쓰는데, 명세는 대소문자 무관 비교(§5.1)라 둘 다 유효합니다.

넷을 합치면 = 🪞🔎🪪 [미러스택](https://github.com/mirror-stack/measure-mirror/tree/main/stack).

💬 **[Discussions](https://github.com/orgs/mirror-stack/discussions)** — 질문 · 아이디어 · 독립 재현 공유 환영.

> 훈련 불요 · 결정론적 · 외부 의존성 없음 (Python 3.10+ stdlib만).

**[📖 완전 가이드 →](docs/GUIDE_KO.md)** · [English README](README.md)

---

## 왜

AI 에이전트가 점점 진짜 일을 합니다 — 파일 쓰기, eval 실행, 코드 커밋, 티켓 처리.
뭔가 잘못되거나 누군가 그렇게 *주장*할 때, 질문은 이렇게 됩니다:
**어느 에이전트가 뭘 했고, 그걸 증명할 수 있나?**

행동거울은 하나의 체인해시 원장 위 두 메커니즘으로 답합니다:

### A. 행동 원장 (Action Provenance)

모든 행동을 추가전용 체인에 봉인. 내용은 SHA-256 해시만 기록(프라이버시 + 용량).

```python
from actmirror import am

# 제비가 eval 실행하고 산출물 해시 봉인
am.record("jebi.jsonl", agent="jebi", action="eval_run",
          target="exp1_result.txt", content=result_bytes,
          payload={"script": "eval.py", "exit": 0})

# 나중에: "제비가 정말 이 결과를 만들었나?"
am.attest("jebi.jsonl", agent="jebi", target="exp1_result.txt",
          content=result_bytes)
# → ATTESTED (해시 검증)  ·  조작된 사본 → CONTENT-MISMATCH
```

| 판정 | 의미 |
|---|---|
| ✅ `ATTESTED` | 봉인 기록 일치 (내용 주면 해시 검증) |
| 🔴 `CONTENT-MISMATCH` | 행동은 기록됐으나, 산출물이 **이후 수정됨** |
| ⚪ `NOT-FOUND` | 봉인 기록 없음 — 정직한 주석: *기록 부재 ≠ 부재 증명* |

### B. 상호증인 — 롤백 킬러

체인 해시 단독으로는 두 공격을 **못** 잡습니다: 꼬리 절단(짧은 체인도 유효)과
통째 교체(새 체인도 내부적으론 유효). 이건 측정거울의 문서화된 구멍이고, 거기선
외부 앵커링으로 풉니다.

행동거울은 **외부 서비스 없이 로컬에서** 풉니다: 에이전트들이 주기적으로 서로의
원장 head(엔트리 수 + head seal)를 자기 원장에 핀(고정)합니다.

```python
am.family_round({"seara": "seara.jsonl", "jebi": "jebi.jsonl",
                 "sonnet": "sonnet.jsonl"})   # 전원이 전원을 핀

am.family_verify(ledgers)
# 제비가 자기 원장을 깨끗한 역사로 몰래 교체:
#   jebi.jsonl 체인 검사 : OK   ← 속음 (새 체인이 유효)
#   seara→jebi 증인       : 🔴 ROLLBACK (3 엔트리 증인했는데 지금 1)
#   sonnet→jebi 증인      : 🔴 ROLLBACK
```

**한 에이전트의 역사를 지우려면, 공격자는 가족 전원의 원장을 동시에 일관되게
재작성해야 합니다.** 에이전트가 늘수록 자물쇠가 늘어납니다.

---

## 설치 & 범용 사용

```bash
pip install -e ~/action_mirror_poc --user   # 어디서든 `am`
export AM_LEDGER=~/mirror_ledgers/<agent>.jsonl
am record --agent <agent> --action train_run --target run.log --content-file run.log
```

기록은 선의가 아니라 **경계**에서 일어나야 합니다 — 본인 것을 고르세요:

```bash
# 훈련 스크립트 — 마지막 줄
am record --agent neoul-trainer --action train_run \
   --target results/pretrain.log --content-file results/pretrain.log

# git — .git/hooks/post-commit
am record --agent $(git config user.name) --action commit \
   --target "$(git rev-parse HEAD)"

# Claude Code — PostToolUse hook (아래 통합 섹션 참조)
```

## CLI

```bash
am --ledger jebi.jsonl record --agent jebi --action eval_run \
   --target exp1.txt --content-file exp1.txt --payload '{"exit":0}'
am --ledger jebi.jsonl history --agent jebi
am --ledger jebi.jsonl attest --agent jebi --target exp1.txt --content-file exp1.txt
am --ledger jebi.jsonl verify                              # 내 체인
am --ledger seara.jsonl witness jebi.jsonl --name jebi     # 동료 head 핀
am --ledger seara.jsonl verify-peer jebi.jsonl --name jebi # 동료 검사
am cross seara.jsonl jebi.jsonl --names seara jebi         # 상호 핀
am --ledger jebi.jsonl export-aat --out jebi.aat.jsonl    # IETF AAT 초안 -11
```

데모 (3-에이전트 가족, 공격 포함): `PYTHONPATH=. python examples/demo_family.py`

## Python API

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
| `verify_signatures` | [Finding] | 선택적 서명-신원 레이어 검증 (`trusted_keys`를 주면 누가 서명했는지 확인) |
| `load_trusted_keys` | dict | `<agent> <pubkey_hex>` 신뢰 키 파일 읽기 |
| `to_aat` / `export_aat` / `verify_aat` | list / int / [Finding] | IETF AAT 초안 -11 내보내기 (아래 참조) |

각 함수의 시그니처·판정은 **[완전 가이드](docs/GUIDE_KO.md)** 참조.

---

## IETF Agent Audit Trail 형식으로 내보내기 (초안)

`am export-aat`는 원장을
**[draft-sharif-agent-audit-trail-11](https://datatracker.ietf.org/doc/html/draft-sharif-agent-audit-trail-11)**
(2026-11-21)의 레코드 형식(JSON Lines)으로 씁니다. 이 문서는 개인 제출 인터넷 초안이고
IETF 표준이 아닙니다. 판마다 바뀌어 왔고 앞으로도 바뀔 수 있습니다. 내보내기는 **-11판만**
기준으로 하며, 각 레코드의 `action_detail.export_profile.draft`에 판 번호를 기록합니다.

```bash
am --ledger jebi.jsonl export-aat --out jebi.aat.jsonl \
   --principal-type human --principal-id urn:user:operator --agent-version 1.0.0
am verify-aat jebi.aat.jsonl     # 필드, 열거값, §6.1 prev_hash 체인 확인
```

들어가는 것: §3.1 필수 필드, `prev_hash = hex(SHA-256(JCS(직전 레코드)))`(RFC 8785),
첫 레코드의 `null`, 그리고 `action_detail.action_mirror` 아래 원래 action-mirror 엔트리
(원장과 대조용). 원장 체인이 `am verify`에서 실패하면 내보내기를 **거부**합니다.
변조된 내용 위에 멀쩡해 보이는 AAT 체인을 새로 만들게 되기 때문입니다.

하지 않는 것:
- **새 증거를 만들지 않습니다.** 원장에서 파생된 것이라, 원장을 가진 사람은 똑같이 다시 만들 수 있습니다.
- action-mirror가 기록하지 않는 필드는 **명시된 기본값**으로 채웁니다: `principal`(에이전트 자신, 자기 주장),
  `agent_version` `0.0.0`, `trust_level` `L0`, `record_phase` `post_execution`, `outcome`은
  `payload.exit`이 정수일 때만 계산하고 아니면 `success`. 여기서 `success`는 "기록됨"이지 "성공이 검증됨"이 아닙니다.
- `record_id`/`session_id`는 UUIDv4 형식이지만 봉인값에서 파생합니다(재현 가능). 무작위가 아닙니다.
- 원래 행동은 `tool_call`(`tool_name` = 행동 문자열), 증인 엔트리는 `decision`이 됩니다. 다른 AAT 유형은 추론하지 않습니다.
- 선택 기능인 Ed25519 `sig`는 원장 봉인값에 대한 서명이라 AAT `signature`로 **내보내지 않습니다**.
  `verify-aat`는 서명, 신뢰 수준 규칙, §8 세션 레코드를 검사하지 않고, 마지막 레코드 삭제는 탐지하지 못합니다.
- JCS 구현은 `tests/test_aat.py`에서 RFC 8785의 숫자·키 정렬 규칙으로 시험했고, 외부 적합성 테스트 모음으로는 시험하지 않았습니다.

---

## 서명 신원 (선택) — *누구*를 *무엇*만큼 단단하게

기본적으로 `agent` 필드는 **자기선언 라벨**입니다: 체인은 *엔트리*가 안 바뀌었음을 증명할 뿐,
명시된 에이전트가 *실제 행위자*임은 증명 못 합니다. 선택적 `[signing]` extra가 Ed25519 키쌍으로
*누구*를 검증 가능한 신원으로 올립니다:

```bash
pip install action-mirror[signing]

am keygen --out jebi.key                                   # 에이전트당 키쌍 하나
am record --agent jebi --action eval --target exp1 --sign jebi.key
am verify-sig                                              # 모든 서명 엔트리 검증
```

서명 엔트리는 `pubkey`(체인됨 — 주장한 키를 체인 안 깨고 못 바꿈)와 seal에 대한 `sig`를 답니다.
`verify_signatures`(CLI `verify-sig`)는 *내용에서 seal을 재계산*해 서명을 검증하므로 **내용 위조**(서명
후 본문 변경)를 잡습니다.

**사칭은 신뢰 키가 있을 때만 잡습니다.** 신뢰 키가 없으면 각 엔트리가 *그 엔트리에 적힌* 키로 서명됐는지만
볼 수 있고, 원장을 다시 쓰는 사람은 새 키로 다시 서명하면 됩니다. `--trusted-keys`(한 줄에
`<agent> <pubkey_hex>`, 모든 에이전트는 `*`)를 주면, 그 에이전트에게 신뢰되지 않은 키로 서명한 엔트리와
신뢰 키가 있는 에이전트의 무서명 엔트리가 FAIL입니다. 신뢰 키 파일은 원장을 쓰는 쪽이 고칠 수 없는 곳에
두세요.

**정직한 범위 — 안 팔기.** 서명은 *이 키 보유자가 서명했다*(귀속·사칭저항·부인불가)를 증명합니다.
**독립은 증명 못 합니다**: 한 운영자가 키를 여럿 발급(Sybil)할 수 있고, 자기 키를 든 에이전트는
다른 키를 쓸 수도 있어 — 가장 강한 형태는 *하네스*가 신원을 attest해야 합니다. 키는 *동일 신원*을
증명할 뿐, 서로 다른 키를 *독립*으로 만들지 못합니다. 무서명 엔트리도 완전 유효(코어는 무의존 유지);
서명은 강제가 아닌 선택 레이어입니다.

---

## 정직한 위협 모델 (신뢰 전 필독)

- 이건 **가족 내 변조 증거(tamper-evidence)**이지 호스트 보안이 아닙니다. 원장이
  서로 다른 신뢰 도메인(다른 프로세스·사용자·머신)에 있을 때 보장이 성립합니다.
  모든 원장을 가진 단일 머신의 root 공격자는 전부 일관되게 재작성할 수 있고 —
  로컬 전용 도구로는 막을 수 없습니다.
- 타임스탬프는 로컬 시계: **봉인 순서**는 신뢰 가능, 절대 시각은 증명 아님.
- `NOT-FOUND`는 결백이 아님: 그냥 기록 안 한 에이전트는 흔적을 안 남깁니다. 기록은
  경계(hook/미들웨어)에서 강제해야지 에이전트 선의에 못 맡깁니다.
- 증인 핀은 핀된 엔트리까지 역사를 보호; 마지막 라운드 이후 행동은 다음 라운드까지
  무방비. 자주 핀하세요.

오늘 견고한 것: 체인봉인 행동 기록, 내용해시 입증, 위치고정 상호증인 +
절단/교체 탐지 — Linux·Windows CI에서 테스트, zero-dep.

---

## 로드맵 (값을 증명하면)

1. Claude Code hook 패키지 — 도구 경계에서 자동 기록
2. `family_round` 데몬 / relay_watch 통합 (NACC 가족)
3. 크로스-거울 cascade: 행동 기록을 measure-mirror 주장의 `depends_on` 대상으로
   (미입증 행동 위에 선 주장 → WARN)
4. MCP 서버 (`am_attest`, `am_family_verify`)

거울 규율 아래 제작:
**일어난 일을 기록하고, 증명 가능한 것을 증명하고, 나머지는 "모른다"고.**

---

## 라이선스

[Apache 2.0](LICENSE)
