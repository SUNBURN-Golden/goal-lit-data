# 빈칸 탐색기 · 확장 경로 (PR7)

필수 필드: **title, author, pub_year, publisher, isbn** (한국어 원서 기준, 번역판은 세지 않음). 채움 판단은 `bot/gaps.py: filled_fields` — 출처가 있는 주장(source_claim)만 센다. 빈칸을 AI 로 채우는 경로는 없다.

## 빈칸 상태 (결정 규칙)
| 상태 | 뜻 | 판단 근거 |
|---|---|---|
| open | 역검색 대기 | 작품별 역검색 질의(`gap_crossref`, `gap_openalex`)가 아직 queued |
| conflict | 출처 간 값 충돌 | 같은 필드에 서로 다른 값이 둘 이상. 값은 모두 남기고 검수 대기, 덮어쓰지 않음 |
| not_extracted | 규칙 추출기가 놓쳤을 수 있음 | 근거 문서 중 본문 텍스트가 있는 HTML/PDF/이미지가 있는데 필드가 없음 |
| not_in_source | 읽을 수 있는 출처에 없음 | 역검색이 끝났는데 없음. detail 에 확인한 출처와 **접근 못 한 출처(NLK·Google Books 키 없음)** 를 함께 적는다 |
| inaccessible | 확인 불가 | 역검색 질의가 모두 failed/skipped (robots 불허·접근 불가·키 없음) |
| resolved | 채워짐 | `resolved_by` = 채운 source_id, `gap_history` 에 전이 기록 |

`not_in_source` 는 "세상 어디에도 없다"가 아니라 "우리가 읽을 수 있는 출처에는 없다"이다.

## 역검색
- Crossref 도서 검색(`type:book|monograph|edited-book`, 제목+저자) → 한글 제목 도서만 작품 후보. 판본 연도·출판사는 ISBN 이 있으면 **판본 수준** 사실로 저장해 초판 연도와 섞여 거짓 충돌이 생기지 않게 한다.
- 결과를 기존 작품에 붙이는 조건은 정규화한 제목 + 저자 일치 또는 ISBN 일치뿐이다(유사도 합치기 없음). 일치하지 않으면 작품으로 올리지 않는다.
- OpenAlex 제목 검색은 비평·참고문헌 문서(라벨)를 모은다.

## 확장
작품 → 판본(`ol_editions`) → **저자 → 다른 작품**(`ol_author_works`, 출처가 준 Open Library 저자 키가 이미 있는 저자만; 이름은 외부 식별자표로 찾음) → 인용 작품·비평(PR6 `oa_cited`/`oa_citing`) → 논쟁(비평을 인용하는 논문 사슬).

## 커버리지
`out_coverage(work, field, filled, gap_state)` 를 `gaps` 단계가 매번 다시 만든다. 요약: 필드별 채움, 전체 필수 필드 완비 작품 수, 빈칸 상태 분포, `resolved_rate`(해결/열린 빈칸), `classified_rate`(open 이 아닌 상태 비율).
