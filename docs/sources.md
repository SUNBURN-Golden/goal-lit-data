# 소스 레지스트리 (`sources/registry.json`)

소스별로 **무엇을 수집할 수 있는지 / 어떤 권한으로 쓰는지 / 얼마나 자주 갱신하는지 / 얼마나 보관하는지 / 지원하지 않는 범위**를 기록한다. 지원하지 않는 것은 숨기지 않고 `unsupported`로 남긴다.

공통 한도(기존 규칙 유지): 하루 후보 500 · 본문 200 · 도메인 동시 1 · 요청 간격 5초 · 링크 깊이 2 · 자동 게시 off · 정직한 User-Agent.

**하지 않는 것:** 사람 위장(UA·행동 흉내), 계정 교체·회전, 프록시·IP 우회, robots.txt/AI 차단 우회, 로그인·결제·비공개 영역 수집, 크롤 트랩(달력·무한 페이지네이션)

| 키 | 지원 | 수집 방법 | 수집 가능 필드 | 이용 권한 | 갱신 | 보관 | 미지원 범위 |
|---|---|---|---|---|---|---|---|
| `openalex_api` | supported | 공식 공개 API (키 불필요, 정직한 UA) | 제목, 초록(비공개 수집함에만, 지식그래프엔 위치·해시만), 발행연도, DOI, 저자명, OpenAlex ID | metadata_only | 168h | 365일 | 논문 전문, 인용 네트워크 전수, 초록 문장의 복사·게시 |
| `doi_resolver` | limited | DOI는 식별자·링크로만 사용. 랜딩 페이지 본문은 도메인별 robots 감사 통과 시에만 | DOI 문자열, 리다이렉트 대상 URL | link_only | 720h | 365일 | 출판사 랜딩/전문 페이지 본문 일괄 수집 |
| `nlk_catalog` | limited | 공개 서지 페이지/OpenAPI. OpenAPI는 인증키 필요 — 키 미구성 | ISBN, 출판사, 발행연도, 쪽수, 저자 표기 | metadata_only | 720h | 730일 | 인증키가 필요한 OpenAPI 대량 조회(키 없음), 로그인 영역 |
| `booker_library` | supported | 공개 페이지 (robots 감사 ok), 필요 시 승인된 JS 렌더링 | 수상·후보 이력, 연도, 번역자, 출판사 | metadata_only | 720h | 730일 | 에세이·인터뷰 전문 |
| `press_robots_ok` | supported | RSS·사이트맵·공개 기사 페이지, live robots.txt 재확인(우리 UA + GPTBot/ClaudeBot/CCBot/anthropic-ai/Google-Extended 중 하나라도 불허면 중단) | 제목, 발행일, 매체, URL, 기사 내 근거 위치, 편집자 요약(우리 문장) | quote_le12 | 24h | 365일 | 기사 전문 저장·게시, 12자 초과 인용, 유료 구독 영역, 댓글 |
| `press_ai_blocked` | unsupported | 수집 안 함. 과거 편집자가 확인한 '링크만' 인용(bare citation)만 유지 | — | link_only | — | 365일 | 신규 수집 전부(본문·메타 자동 조회 포함). 차단 우회 시도 없음 |
| `kci_journal` | limited | 승인 도메인의 공개 초록 페이지, robots 허용 경로만. www.kci.go.kr·dbpia는 정책상 제외 | 논문 제목, 초록(비공개 수집함에만), 학술지·권호, 저자명 | metadata_only | 720h | 365일 | www.kci.go.kr, dbpia.co.kr, 논문 전문 |
| `platform_public_cover` | limited | 승인 도메인 + robots 허용 공개 표지·작품 소개 1페이지만 | 작품명, 연재처, 연재 상태, 공개 소개 문구 메타 | metadata_only | 168h | 365일 | /webtoon/detail·/viewer/ 본문, 로그인·결제 영역, 댓글·평점 |
| `naver_blog_booking` | unsupported | 수집 안 함 (robots가 AI 수집 차단) | — | link_only | — | 0일 | 전부 |
| `other_blocked_policy` | unsupported | 수집 안 함 | — | link_only | — | 0일 | 전부 |
| `instagram_graph_api` | unsupported | Meta 공식 Graph API. 앱 등록·앱 검수·접근 토큰 필요. 현재 자격 미구성이라 비활성 | (활성화 시) 연결한 우리 비즈니스 계정의 미디어 캡션·퍼마링크·게시시각, (활성화 시) 공개 비즈니스/크리에이터 계정 business_discovery 메타, (활성화 시) 공개 게시물 oEmbed | metadata_only | 24h | 90일 | 현재 전부(자격 없음). 활성화에는 '계정 연결'이 필요하므로 준태님 승인 후에만. 개인(비전문가) 계정·비공개 계정·DM·스토리·팔로워 목록은 공식 API로도 범위 밖 |
| `instagram_public_pages` | unsupported | 수집 안 함. instagram.com/robots.txt가 '명시적 서면 허가 없는 자동 수집 금지'를 선언하고 AI 크롤러를 차단하며, 정책 차단 호스트로 등록됨. 로그인 벽·행동 위장·계정/프록시 회전으로 우회하지 않는다 | — | link_only | — | 0일 | 자동 수집 전부. 이 항목은 미지원으로 기록만 한다 |
| `manual_clip_extension` | limited | 사람이 보고 있는 페이지에서 '이 자료 담기'를 눌렀을 때만 동작. 인스타 게시물도 사람이 직접 열어 본 것을 담는 경우에 한해 가능(자동 순회·예약 없음) | 페이지 제목, URL, 선택 텍스트(PII 필터 통과 후), 게시 시각 메타 | quote_le12 | — | 180일 | 무인 수집, 로그인 정보·입력창·댓글·DM, 페이지 전체 덤프 |
| `internal_site_records` | supported | 로컬 파일 읽기 | 작품 서지 단서, 미확인 목록, 에세이 근거 묶음 메타 | own_work | 24h | 무기한(자체 기록) | 내부 기록만으로는 '확인된 사실'이 되지 않음(외부 출처 필요) |
| `unregistered` | unsupported | robots 감사를 거쳐 등록되기 전까지 수집 안 함 | — | link_only | — | 365일 | 등록 전 전부 |

## 인스타그램 취급 (명시)
- **자동 수집(공개 웹 페이지)**: 미지원. `instagram.com/robots.txt`가 서면 허가 없는 자동 수집을 금지하고 AI 크롤러를 차단한다. 로그인 벽·행동 위장·계정/프록시 회전으로 우회하지 않는다.
- **공식 API(Graph API / oEmbed)**: 코드 경로·레지스트리 항목은 있으나 **자격(앱·토큰) 미구성이라 비활성**. 켜려면 계정 연결이 필요하므로 준태님 승인 후에만(비용·첫 외부 메일·삭제·계정 연결은 한 장에 묶어서 질문).
- **수동 담기 확장**: 사람이 직접 열어본 게시물을 '담기'로 가져오는 것만 가능(무인·예약 없음). 개인정보는 저장 전 필터.
- 인스타 정보는 '확인할 후보를 찾는 단서'로만 쓰며, 그 자체로 `confirmed_fact`가 되지 않는다.

## 이용 권한 값 (`usage_basis`)
`metadata_only` 메타데이터만 · `link_only` 링크만 · `quote_le12` 12자 이하 인용 · `fulltext_forbidden` 전문 금지 · `own_work` 우리 글.
보관 만료일은 `수집 시각 + retention_days` 로 `retention_until`에 기록한다.

