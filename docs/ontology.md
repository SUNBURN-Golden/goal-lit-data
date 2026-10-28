# 꼴릿 문학 지식그래프 온톨로지 (v1)

## 1. 개체 (SQLite 테이블, `schema/migrations/001_ontology.sql`)
| 개체 | 테이블 | 설명 |
|---|---|---|
| Work | `work` | 작품. 고유 ID `wk_*`, 사이트 slug, 대표 제목, 종류. 시간에 따라 변하는 속성은 여기 두지 않고 `bib_fact`로. |
| Edition | `edition` | 판본(출판사·번역자·언어·연도·ISBN). ISBN 없으면 `identity_status='provisional'`. |
| Author | `author` | 작가. 고유 ID `au_*`. |
| Theme | `theme` | 주제 태그. 출처가 쓴 표현을 그대로 `label`로, 정규화는 `entity_alias`. |
| Source | `source` | 출처 문서. URL·정규화 URL·매체·접근 수준·이용 권한·보관기간. |
| EvidencePassage | `evidence_passage` | 근거 **위치**(문단/쪽/섹션)만 저장. 인용 조각은 12자 이하(CHECK), 원문은 저장하지 않고 해시만. |
| 서지 사실 | `bib_fact` | 작품·판본에 대한 사실 주장(predicate/value). |
| 작가 발언 | `author_statement` | 작가가 한 말(발언 시점·맥락·1차/2차). |
| 해석 | `interpretation` | 비평·해석(작성자·입장). **확정 사실 상태를 가질 수 없다**(CHECK). |
| 관계 | `work_author`, `work_theme`, `interpretation_relation` | 각각 출처·근거를 갖는 주장 행. |
| AI 추론 | `ai_inference` | AI가 제안한 관계. 항상 `ai_inference`, 사실 테이블과 분리. 승격은 사람이 근거를 붙여 새 행을 만드는 것으로만. |

## 2. 모든 "주장 행"의 공통 필드
| 필드 | 의미 |
|---|---|
| `id` + `entity_alias` | 고유 ID와 별칭(표기 변형·번역명). 별칭이 같다고 같은 대상으로 합치지 않는다 — 동일 대상 식별 단계가 규칙으로 판정. |
| `source_id`, `source_url`, `evidence_id` | 출처와 근거 위치. 근거 위치가 없으면 `confirmed_fact` 불가. |
| `collected_at` | 우리가 수집한 시각. |
| `valid_from`, `valid_to`, `valid_from_precision` | **실제로 적용되는 기간**. NULL은 "모름"이지 "항상/제한 없음"이 아니다. |
| `recorded_at`, `recorded_to`, `superseded_by` | 우리가 이 행을 믿은 기간(정정 이력). 정정 시 이전 행은 닫고(`recorded_to`) 새 행이 `superseded_by`로 이어진다. |
| `epistemic_status` | `confirmed_fact` 확인된 사실 / `source_claim` 출처의 주장 / `ai_inference` AI 추론 / `unverified` 미확인 |
| `usage_basis`, `retention_until` | 수집·이용 권한(메타데이터만 / 링크만 / 12자 인용 / 전문 금지)과 보관 만료일. 출처 레지스트리에서 상속. |
| `legacy_inbox_id` | 기존 수집함(`goallit_inbox.db`) 항목 추적용. 원문은 그쪽(리포 밖)에만 있다. |

## 3. 불변 조건 (DB 트리거·CHECK + 검증 규칙으로 강제)
1. 주장 행의 값(`value`, `summary` 등)은 UPDATE 금지 — 바꾸려면 새 행 + 이전 행 닫기. DELETE 금지.
2. `change_log`는 append-only(UPDATE/DELETE 트리거로 차단).
3. `interpretation.epistemic_status`는 `confirmed_fact` 불가. 해석의 "누가 말했는가"(귀속)만 `attribution_verified`로 별도 확인.
4. `confirmed_fact` 조건: 외부 출처 URL + 근거 위치 + collected_at + 열린 충돌 없음 (검증 단계가 판정, DB 트리거가 재확인).
5. `evidence_passage.quote_snippet` ≤ 12자. 15자 이상 겹침 검사는 검증 단계가 `snippet_hash`/`passage_hash`로 수행.
6. AI 추론 → 사실/해석 테이블로의 자동 승격 없음. 관계(`opposes`, `work_theme`, `work_author`) 확정은 출처 명시 또는 편집자 승인만.
7. 충돌은 `conflict` + `review_queue`로 남기고 양쪽 보존. 충돌이 열려 있는 동안 `publish_pending` 승격 차단(기존 규칙 유지).
8. `AUTO_PUBLISH_TO_SITE = False` 유지. 이 리포의 어떤 단계도 사이트를 게시하지 않는다.

## 4. 서지 사실 vs AI 해석 분리
- `bib_fact`, `author_statement`, `work_author`: *출처가 말한 것*을 기록. 확정 가능.
- `interpretation`, `work_theme`, `interpretation_relation`: *누군가의 해석*. 확정 사실이 될 수 없음.
- `ai_inference`: *우리 AI의 제안*. 별도 테이블, 별도 결과 칸(`out_unverified_and_ai`).

## 5. 상태 추적 대상 (실제로 변하는 것)
- `source_snapshot` (출처 문서의 콘텐츠 해시·HTTP 상태·마지막 확인 시각) → 변경 감지 → `change_event` → 의존 주장 재검증 큐.
- `bib_fact`/`edition` 정정 이력(`superseded_by`, `change_log`).
- 그 외(작품-주제 연결 등)는 상태 추적이 아니라 근거 그래프로 취급한다.

## 6. 개인정보·원문 경계
- 저장 전 PII·프롬프트 인젝션 필터(기존 `pii_filter`)를 통과한 것만 수집함에 들어간다.
- 원문(본문·초록 전체)은 리포 밖 비공개 수집함에만. 지식그래프 DB에는 요약(우리 문장)·위치·해시만. 논문 초록 원문은 요약으로 복사하지 않고 `summary_withheld_reason`을 남긴다.
