# 운영 (PR8)

## 프로세스 (pm2)
데몬은 **리포 체크아웃(`/workspace/gl-data/run`, 배포용 워크트리)** 에서 돌고, DB·원문·락은 리포 밖(`GLKG_DATA_DIR`, 기본 `/workspace/lit-hub/collector/data`)에 둔다. 비밀값은 설정에 없다.

```
GLKG_REPO=/workspace/gl-data/run pm2 start ops/ecosystem.glkg.config.js     # 13개 단계 데몬
python3 -m glkg status                                                      # 단계별 마지막 상태·재시도·dead·오늘 사용량
python3 -m glkg pause <단계> / resume <단계>                                 # 단계 일시 정지(재시작해도 유지)
python3 ops/proof_metrics.py [--save f | --diff f]                          # 증명 지표(읽기 전용)
```
| 데몬 | 주기 | 역할 | 네트워크 |
|---|---|---|---|
| glkg-seek | 15분 | 질의 조합 생성 | 없음 |
| glkg-harvest | 1분 | 수집 실행·스케줄(예산 클래스 3종) | 있음 |
| glkg-assess | 5분 | 새 도메인 평가 | 있음(discovery 예산) |
| glkg-gaps | 10분 | 빈칸 탐색·커버리지 | 없음(질의만 만듦) |
| glkg-parse | 10분 | 새 추출기 버전으로 재파싱 | 없음 |
| glkg-fetch | 30분 | 기존 수집기 증분 실행(`goallit-auto` 대체). 하루 40건 몫 | 있음 |
| glkg-detect/extract/resolve/verify/history/recompute/quality | 15~60분 | 기존 PR4 파이프라인 | 없음 |

`goallit-auto` 는 삭제했고(같은 일을 `glkg-fetch` 가 함) `goallit-worker` 는 그대로다. `watchdog.sh`(5분 cron)는 `goallit-worker` + `glkg-*` 13개를 감시한다(원본은 `watchdog.sh.bak-pre-glkg`). 다른 pm2 앱(agamajung-*)은 건드리지 않는다.

## 예산 (한 곳에서 강제)
하루 요청 200건 = 능동 봇 backfill 120 + discovery 60 + refresh 20, **기존 수집기와 합쳐서** 200건을 넘지 않는다(수집함 DB `daily_counters.fetched_count` 를 읽기 전용으로 합산). 기존 수집기(`glkg-fetch`)는 그중 40건까지만 쓴다(`config.LEGACY_FETCH_SHARE`) — 레거시가 예산을 다 먹어 봇이 굶는 일을 막기 위해서다. 후보 생성 500/일. robots.txt 요청도 센다. 하루가 지나면(KST 0시) 자동으로 새 예산이 열린다.

**verify 단계 예외 (준태님 11/5 지시):** 온톨로지 검증(`glkg-verify`)은 일일 rows 예산을 두지 않는다. `VerifyStage.budget_metric = None`, `STAGE_DAILY_CAPS["verify"] = {}`. 과거 `daily_usage` 의 `verify/rows` 행(예: 50000/50000)은 남아 있어도 차단하지 않는다 — verify 는 더 이상 그 지표를 소모/조회하지 않으므로 DB 마이그레이션이나 사용량 초기화가 필요 없다. fetch·assess·discovery·bot·extract 등 **다른 단계의 한도와 robots/도메인 차단 규칙은 그대로**다. 판정 규칙(`rules.evaluate`)도 완화하지 않았다. 장기 실행 프로세스는 코드를 다시 import 하지 않으므로, 승인된 코드를 `/workspace/gl-data/run` 에 반영한 뒤 `glkg-verify` 프로세스를 갱신해야 적용된다.

## 동시 사용 (DB 잠금)
데몬 13개가 한 SQLite(WAL)를 쓴다. 요청 간격 대기·네트워크 호출 **직전에 커밋**해 쓰기 잠금을 놓는다(`DomainClock.before_wait`). 저장·파싱·작업 상태·후속 질의는 요청 뒤 한 트랜잭션으로 커밋되므로 중간에 죽으면 그 작업만 롤백된다. (이 수정 전에는 첫 기동에서 `database is locked` 가 났다 — 테스트 `test_db_write_lock_is_not_held_during_network`.)

## 실패 처리
- robots.txt **읽기 실패**(타임아웃·SSL·5xx)는 차단이 아니라 일시 장애: 수집은 하지 않고 60/300/1800초 백오프 후 최대 3회 재시도, 평가기도 3회까지 `new` 유지, robots 실패 캐시는 10분. robots **불허**·403·로그인·접근불가는 영구 제외. (실제 실행에서 Open Library SSL 오류가 영구 제외로 굳은 것을 고친 것이다.)
- 429 는 1시간 쉼. 3회 실패한 작업은 `failed`/`dead` 로 남아 `status` 에 보인다.

## 이어받기·재실행
- 죽은 뒤: 단계 락·리스는 만료 후 회수되고, 커서는 배치마다 커밋되어 마지막 커밋 지점부터 이어간다. 한 작업은 한 트랜잭션이라 반쯤 저장된 상태가 없다.
- 재실행: 문서 `(canonical_url, content_hash)`, 작품·판본·사실의 안정 ID 로 중복이 생기지 않는다.

## 데이터 수정 기록 (1회성, 파생 상태만)
robots 읽기 실패 수정 후, 잘못 `skipped` 로 굳은 `ol_subject` 질의 10건을 `queued` 로, `blocked` 로 굳은 후보 도메인 3건을 `new` 로 되돌렸다(주장·원문 테이블은 건드리지 않음).
