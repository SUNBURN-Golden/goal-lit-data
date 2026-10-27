"""봇 테스트용 가짜 전송 계층과 비식별 합성 응답. 실제 네트워크를 쓰지 않는다."""
import json
from typing import Dict

from glkg.bot import http as H


class FakeTransport:
    def __init__(self, routes=None):
        self.routes: Dict[str, object] = routes or {}
        self.calls = []

    def get(self, url, headers=None, timeout=25, max_bytes=0):
        self.calls.append(url)
        for prefix, val in self.routes.items():
            if url.startswith(prefix):
                r = val(url) if callable(val) else val
                if isinstance(r, Exception):
                    raise r
                return H.Response(url=url, status=r[0], headers={"content-type": r[1]}, content=r[2] if isinstance(r[2], bytes) else r[2].encode(), final_url=r[3] if len(r) > 3 else url)
        return H.Response(url=url, status=404, headers={"content-type": "text/plain"}, content=b"nf", final_url=url)


def no_sleep_clock(tmp_path, interval=0.0):
    return H.DomainClock(lock_dir=tmp_path / "locks", interval=interval, sleep=lambda s: None)


ROBOTS_OK = (200, "text/plain", "User-agent: *\nAllow: /\n")
ROBOTS_BLOCK_AI = (200, "text/plain", "User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
ROBOTS_BLOCK_ALL = (200, "text/plain", "User-agent: *\nDisallow: /\n")

OPENALEX = json.dumps({"results": [
    {"id": "https://openalex.org/W1", "display_name": "가상 소설의 서사 구조 연구", "publication_year": 2019, "doi": "https://doi.org/10.1000/gs.1",
     "language": "ko", "authorships": [{"author": {"display_name": "가상연구자"}}], "primary_location": {"source": {"display_name": "가상문학연구"}, "landing_page_url": "https://journal.test/a/1"},
     "referenced_works": ["https://openalex.org/W9", "https://openalex.org/W8"], "keywords": [{"display_name": "서사"}], "cited_by_count": 3,
     "abstract_inverted_index": {"가상": [0]}},
    {"id": "https://openalex.org/W2", "display_name": "다른 논문", "publication_year": 2020, "authorships": [], "referenced_works": []}]})

WIKIDATA = json.dumps({"results": {"bindings": [
    {"work": {"value": "http://www.wikidata.org/entity/Q111"}, "workLabel": {"value": "가상의 바다"}, "authorLabel": {"value": "가상작가"},
     "pub": {"value": "2011-03-01T00:00:00Z"}, "publisherLabel": {"value": "가상출판사"}, "isbn": {"value": "978-89-546-3270-6"}},
    {"work": {"value": "http://www.wikidata.org/entity/Q112"}, "workLabel": {"value": "작가 없는 책"}, "pub": {"value": "2005-01-01T00:00:00Z"}},
    {"work": {"value": "http://www.wikidata.org/entity/Q113"}, "workLabel": {"value": "Q113"}}]}})

BOOK_HTML = """<html><head><title>x</title><meta property="og:site_name" content="가상서점"/>
<script type="application/ld+json">{"@type":"Book","name":"가상의 산","author":{"@type":"Person","name":"산작가"},"publisher":{"name":"산출판"},"isbn":"9788936472559","datePublished":"2014-06-01"}</script>
</head><body><p>%s</p></body></html>""" % ("가상의 산 소개 문단입니다. " * 20)
LOGIN_HTML = "<html><body><form action='/l'><input type='password' name='p'/><input name='u'/></form></body></html>"
EMPTY_HTML = "<html><head></head><body><div id='root'></div><script>app()</script></body></html>"
