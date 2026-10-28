# 수집 봇 저장 계층 (마이그레이션 006)

| 계층 | 표 | 내용 | 보관 위치 |
|---|---|---|---|
| raw | `raw_document`, `fetch_log` | 가져온 응답의 해시·시각·방식(api/feed/html/browser/pdf/image)·상태. 본문 사본은 권리상 가능할 때만(`body_stored`) | DB는 리포 밖, 본문은 `data/kg_raw/` (0600, 보관기한 후 삭제 대상) |
| extraction | `extraction` | 필드·값·근거 위치·방법(structured/pattern/ocr)·**추출기 버전**. 버전이 오르면 새 행이 쌓이고 이전 결과는 남는다 | DB |
| label | `doc_label` | 서지/인터뷰/서평/비평/해석/참고문헌 + 출처 성격(홍보·독립·미상) | DB |
| service | `work`, `bib_fact`, `work_author`, `edition`, `out_*` | `source_claim` 으로만 들어온 주장. 확정은 verify 규칙만 | DB |

## 수집 규칙 (executor)
- 순서: API/피드 → HTML → (JS로만 그려지는 빈 페이지에 한해) 브라우저 렌더 → PDF·이미지. 브라우저는 차단을 우회하는 용도로 쓰지 않는다(401/403·로그인·robots 차단은 그대로 실패).
- 실패로 기록: 로그인 페이지, 빈 본문, 401/403(`failed_inaccessible`), 429(`failed_rate_limit`), robots 차단·조회 실패(`failed_robots`), 미등록·미지원 도메인(`failed_policy`), 미지원 형식. 실패는 문서로 저장하지 않는다.
- 같은 정규화 URL + 같은 내용 해시는 `unchanged` 로 기록만 하고 새 문서를 만들지 않는다. 내용이 바뀌면 새 문서(버전)로 쌓인다.
- 도메인 동시 1 · 간격 5초(파일 락, 프로세스가 달라도 유지) · robots 확인(우리 UA + AI 크롤러 5종) · robots.txt 요청도 예산에 포함.
- PDF: 페이지별 `pdftotext`, 텍스트가 없는 페이지(최대 3쪽)와 이미지는 `tesseract kor+eng` OCR. 저장하는 것은 ISBN·DOI 같은 **식별자 패턴과 페이지 텍스트 해시·글자수**뿐 — 문장은 저장하지 않는다.
- 예산: 하루 요청 200건을 backfill 120 / discovery 60 / refresh 20 으로 나눠 센다(기존 수집기 사용량도 합산). 넘겨 쓰지 않는다.
