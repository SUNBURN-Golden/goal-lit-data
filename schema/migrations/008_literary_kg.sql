-- 문학 지식그래프 확장: 주제(theme) 개체의 출처·상태·권리, 근거 의무 트리거, 대립 해석 규칙, 정정 이력, 통합 항목 뷰
ALTER TABLE theme ADD COLUMN epistemic_status TEXT NOT NULL DEFAULT 'unverified' CHECK (epistemic_status IN ('confirmed_fact','source_claim','ai_inference','unverified'));
ALTER TABLE theme ADD COLUMN source_id TEXT REFERENCES source(source_id);
ALTER TABLE theme ADD COLUMN evidence_id TEXT REFERENCES evidence_passage(evidence_id);
ALTER TABLE theme ADD COLUMN lexicon_key TEXT NOT NULL DEFAULT '';
ALTER TABLE theme ADD COLUMN valid_from TEXT;
ALTER TABLE theme ADD COLUMN usage_basis TEXT NOT NULL DEFAULT 'own_work';
ALTER TABLE theme ADD COLUMN retention_until TEXT;
ALTER TABLE source_registry ADD COLUMN rights_note TEXT NOT NULL DEFAULT '';

-- 주제 연결: 출처 주장(source_claim)은 출처와 근거 위치가 반드시 있어야 한다
CREATE TRIGGER trg_work_theme_claim_needs_evidence BEFORE INSERT ON work_theme
WHEN NEW.epistemic_status='source_claim' AND (NEW.source_id IS NULL OR NEW.evidence_id IS NULL)
BEGIN SELECT RAISE(ABORT,'work_theme source_claim requires source_id and evidence_id'); END;
CREATE TRIGGER trg_work_theme_claim_needs_evidence_u BEFORE UPDATE ON work_theme
WHEN NEW.epistemic_status='source_claim' AND (NEW.source_id IS NULL OR NEW.evidence_id IS NULL)
BEGIN SELECT RAISE(ABORT,'work_theme source_claim requires source_id and evidence_id'); END;
-- AI 주제 제안은 work_theme 에 직접 들어갈 수 없다(ai_inference 표에만)
CREATE TRIGGER trg_work_theme_no_ai BEFORE INSERT ON work_theme WHEN NEW.epistemic_status='ai_inference'
BEGIN SELECT RAISE(ABORT,'AI theme proposals go to ai_inference only'); END;
-- AI 주제 제안은 승격 금지
CREATE TRIGGER trg_ai_theme_never_promoted BEFORE UPDATE ON ai_inference
WHEN NEW.review_state='promoted' AND (NEW.proposed_relation LIKE 'work_theme%' OR NEW.subject_table='work_theme' OR NEW.subject_table='theme')
BEGIN SELECT RAISE(ABORT,'AI-proposed themes are never promoted'); END;

-- 비평·해석: 출처 주장이면 근거 위치 필수
CREATE TRIGGER trg_interp_claim_needs_evidence BEFORE INSERT ON interpretation
WHEN NEW.epistemic_status='source_claim' AND NEW.evidence_id IS NULL
BEGIN SELECT RAISE(ABORT,'interpretation source_claim requires evidence_id'); END;

-- 대립 해석: 양쪽 모두 남아 있고, 양쪽 모두 주체(작성자)와 근거가 있어야 한다
CREATE TRIGGER trg_opposes_both_sides BEFORE INSERT ON interpretation_relation
WHEN NEW.relation='opposes' AND (
  (SELECT COUNT(*) FROM interpretation i WHERE i.interp_id IN (NEW.a_interp_id, NEW.b_interp_id)
     AND i.evidence_id IS NOT NULL AND i.attributed_to_name<>'' AND i.recorded_to IS NULL) < 2)
BEGIN SELECT RAISE(ABORT,'opposes needs both interpretations live with author and evidence'); END;
CREATE UNIQUE INDEX uq_interp_relation_pair ON interpretation_relation(a_interp_id, b_interp_id, relation);

-- 정정 이력: 상태·근거·종료가 바뀌면 자동 기록
CREATE TRIGGER trg_hist_work_theme AFTER UPDATE ON work_theme
WHEN OLD.epistemic_status IS NOT NEW.epistemic_status OR OLD.evidence_id IS NOT NEW.evidence_id OR OLD.recorded_to IS NOT NEW.recorded_to
BEGIN INSERT INTO change_log(table_name,row_id,op,old_json,new_json,reason,at)
  VALUES('work_theme',NEW.wt_id,'correct',json_object('status',OLD.epistemic_status,'evidence',OLD.evidence_id,'recorded_to',OLD.recorded_to),
         json_object('status',NEW.epistemic_status,'evidence',NEW.evidence_id,'recorded_to',NEW.recorded_to),'trigger',strftime('%Y-%m-%dT%H:%M:%S+09:00','now','+9 hours')); END;
CREATE TRIGGER trg_hist_interp_rel AFTER UPDATE ON interpretation_relation
WHEN OLD.epistemic_status IS NOT NEW.epistemic_status OR OLD.recorded_to IS NOT NEW.recorded_to
BEGIN INSERT INTO change_log(table_name,row_id,op,old_json,new_json,reason,at)
  VALUES('interpretation_relation',NEW.rel_id,'correct',json_object('status',OLD.epistemic_status,'recorded_to',OLD.recorded_to),
         json_object('status',NEW.epistemic_status,'recorded_to',NEW.recorded_to),'trigger',strftime('%Y-%m-%dT%H:%M:%S+09:00','now','+9 hours')); END;
CREATE TRIGGER trg_hist_theme AFTER UPDATE ON theme
WHEN OLD.epistemic_status IS NOT NEW.epistemic_status OR OLD.label IS NOT NEW.label
BEGIN INSERT INTO change_log(table_name,row_id,op,old_json,new_json,reason,at)
  VALUES('theme',NEW.theme_id,'correct',json_object('status',OLD.epistemic_status,'label',OLD.label),
         json_object('status',NEW.epistemic_status,'label',NEW.label),'trigger',strftime('%Y-%m-%dT%H:%M:%S+09:00','now','+9 hours')); END;

-- 통합 항목 뷰: 모든 그래프 항목의 ID·별칭·출처·위치·수집/유효 시각·상태(4단계)·충돌·정정 횟수·권리·보관기간
CREATE VIEW v_kg_item AS
WITH items AS (
  SELECT 'theme' AS item_type, t.theme_id AS item_id, t.label AS label, t.source_id, t.evidence_id, t.created_at AS collected_at, t.valid_from,
         t.epistemic_status, t.usage_basis, t.retention_until FROM theme t
  UNION ALL SELECT 'work_theme', wt.wt_id, wt.work_id||'→'||wt.theme_id, wt.source_id, wt.evidence_id, wt.collected_at, wt.valid_from, wt.epistemic_status, wt.usage_basis, wt.retention_until FROM work_theme wt WHERE wt.recorded_to IS NULL
  UNION ALL SELECT 'interpretation', i.interp_id, i.kind||':'||substr(i.summary,1,40), i.source_id, i.evidence_id, i.collected_at, i.valid_from, i.epistemic_status, i.usage_basis, i.retention_until FROM interpretation i WHERE i.recorded_to IS NULL
  UNION ALL SELECT 'interpretation_relation', r.rel_id, r.relation||':'||r.a_interp_id||'↔'||r.b_interp_id, r.source_id, r.evidence_id, r.collected_at, r.valid_from, r.epistemic_status, r.usage_basis, r.retention_until FROM interpretation_relation r WHERE r.recorded_to IS NULL
  UNION ALL SELECT 'ai_inference', a.inference_id, a.proposed_relation, NULL, NULL, a.collected_at, NULL, a.epistemic_status, 'own_work', NULL FROM ai_inference a
)
SELECT it.item_type, it.item_id, it.label,
  (SELECT group_concat(al.alias,' | ') FROM entity_alias al WHERE al.entity_id=it.item_id) AS aliases,
  it.source_id, s.url AS source_url, ev.locator AS location, ev.quote_snippet,
  it.collected_at, it.valid_from,
  CASE it.epistemic_status WHEN 'confirmed_fact' THEN '확정' WHEN 'source_claim' THEN '출처 주장' WHEN 'ai_inference' THEN 'AI 추론' ELSE '미확인' END AS status_ko,
  it.epistemic_status,
  (SELECT COUNT(*) FROM conflict c WHERE (c.a_id=it.item_id OR c.b_id=it.item_id)) AS conflict_count,
  (SELECT COUNT(*) FROM change_log l WHERE l.row_id=it.item_id AND l.op IN ('correct','supersede','status_change','close')) AS correction_count,
  it.usage_basis AS rights, it.retention_until
FROM items it LEFT JOIN source s ON s.source_id=it.source_id LEFT JOIN evidence_passage ev ON ev.evidence_id=it.evidence_id;
