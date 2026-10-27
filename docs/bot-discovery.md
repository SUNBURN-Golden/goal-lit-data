# 능동 수집 봇: 탐색 · 평가 · 실행 · 스케줄 (PR6)

네 개의 데몬 단계가 같은 DB 대기열을 공유한다. 각 단계는 단계별 락·체크포인트·제한 재시도를 쓰고, 서로 직접 호출하지 않는다.

| 단계 | 하는 일 | 네트워크 | 코드 |
|---|---|---|---|
| `seek` (소스 탐색기) | 장르×연도·장르×연도×키워드 질의 조합, 작품마다 비평 질의(OpenAlex·Crossref 제목 검색), 갱신 주기 지난 작업을 refresh 로 재큐 | 없음(DB 행만 추가) | `stages/seek.py`, `bot/queries.py` |
| `harvest` (수집 실행기·스케줄러) | 예산 클래스(refresh/backfill/discovery)를 번갈아 작업 1건씩: 가져오기→추출→라벨→승격→작업 상태→후속 질의를 한 트랜잭션으로 | 있음 | `stages/harvest.py`, `bot/jobs.py`, `bot/followups.py`, `bot/collect.py` |
| `assess` (출처 평가기) | 새 도메인 시험 수집 1건 → 5점수 → 등록/후순위/차단 | 있음(discovery 예산) | `stages/assess.py`, `bot/evaluator.py` |
| `parse` | 저장된 원문을 새 추출기 버전으로 다시 파싱 | 없음 | `stages/parse.py` |

## 확장 경로
작품 → 판본(ISBN) → 원제·번역자 → 저자 → 인용 작품·비평 → 논쟁.
- 작품이 승격되면 `ol_editions`(backfill, 우선순위 60) 질의가 생긴다. 저자 페이지·논쟁 확장은 PR7(갭 탐색기)에서 이어진다.
- OpenAlex 논문이 작품에 연결되면 `cites`(참고문헌)와 `cites:<id>`(인용 논문) 질의가 생긴다 → 참고문헌/비평 라벨 문서가 쌓인다.
- 본문에서 언급된 작품명(`mention_title`)은 아직 모르는 제목일 때만 새 검색을 만든다.
- 논문의 landing page·OA PDF 도메인, HTML 의 외부 링크 도메인은 `candidate_source(new, in_seed=0)` 로 올라가 평가기를 거친다.
- 등록된 도메인: 시험 페이지를 다시 파싱하고, robots 의 `Sitemap:` 선언을 큐에 넣는다. sitemap/RSS 는 같은 호스트 URL 만, 깊이 ≤ 2, 문서당 30건 이하.
- 같은 도메인의 PDF 첨부·'다음' 페이지는 따라간다(깊이 ≤ 2). 다른 도메인 링크는 후보 출처로만 올린다.

## 예산
하루 요청 200건 = backfill 120 + discovery 60 + refresh 20 (robots.txt 요청도 포함). 후보 생성은 하루 500건(`discovered`). 클래스끼리 빌려 쓰지 않는다. `daily_usage(stage='bot:<class>')` 에 기록되어 재시작해도 유지된다. 이미 있는 질의·URL 은 후보 상한을 쓰지 않는다.

## 작업 상태
- `ok`/`unchanged` → done. 404/410·robots 불허·로그인·접근불가·형식 미지원·빈 본문 → skipped(영구).
- 네트워크·5xx → queued, 60/300/1800초 백오프, 3회 후 failed. 429 → 1시간 쉼.
- 한 작업의 상태 갱신과 후속 질의 생성은 수집·파싱과 같은 SAVEPOINT 에서 커밋된다. 중간에 죽으면 모두 롤백되고 작업은 queued 로 남아 이어서 처리된다(테스트: `test_resume_after_crash_*`).

## 출처 평가 점수 (규칙 기반, AI 추정 없음)
relevance(0.3: 문학 어휘·알려진 작품명) · novelty(0.15: 이미 본 식별자 비율) · extractability(0.25: 제목/저자/날짜/식별자/출판사 5필드) · original(0.15: 정본이 다른 도메인이면 감점, DOI 면 만점) · rights(0.15: 구조화 메타데이터가 있어야 metadata_only, 아니면 link_only).
등록 조건: 총점 ≥ 0.6 and relevance ≥ 0.34 and extractability ≥ 0.4. 등록돼도 사용 범위는 metadata_only/link_only 이며 원문 사본은 metadata_only 일 때만 비공개 저장소에 둔다. robots/AI 차단·로그인·접근불가는 blocked/deprioritized. 일시 실패는 3회까지 재시도.
