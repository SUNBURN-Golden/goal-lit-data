-- 외부 식별자 매핑(Open Library work/author 키, OpenAlex ID, DOI 등). '같은 개체'라는 주장은 출처가 준 식별자 키로만 만든다(유사도 합치기 없음).
CREATE TABLE external_id (
    entity_type TEXT NOT NULL CHECK (entity_type IN ('work','author','edition')),
    entity_id TEXT NOT NULL,
    scheme TEXT NOT NULL,
    value TEXT NOT NULL,
    source_id TEXT REFERENCES source(source_id),
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (scheme, value)
);
CREATE INDEX idx_external_entity ON external_id(entity_type, entity_id);
