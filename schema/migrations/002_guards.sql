-- 자동 생성: tools/gen_schema.py
-- 불변 조건: 주장 행 값 UPDATE 금지, DELETE 금지, change_log append-only, confirmed_fact 승격 조건 재확인

CREATE TRIGGER trg_edition_immutable BEFORE UPDATE ON edition
WHEN NEW.work_id IS NOT OLD.work_id OR NEW.isbn IS NOT OLD.isbn OR NEW.label IS NOT OLD.label OR NEW.source_id IS NOT OLD.source_id OR NEW.source_url IS NOT OLD.source_url OR NEW.evidence_id IS NOT OLD.evidence_id OR NEW.collected_at IS NOT OLD.collected_at OR NEW.valid_from IS NOT OLD.valid_from OR NEW.valid_from_precision IS NOT OLD.valid_from_precision OR NEW.recorded_at IS NOT OLD.recorded_at OR NEW.usage_basis IS NOT OLD.usage_basis OR NEW.legacy_inbox_id IS NOT OLD.legacy_inbox_id
BEGIN SELECT RAISE(ABORT, 'edition: 주장 값은 덮어쓸 수 없다 — 이전 행을 닫고 새 행을 추가할 것'); END;
CREATE TRIGGER trg_edition_nodelete BEFORE DELETE ON edition
BEGIN SELECT RAISE(ABORT, 'edition: 삭제 금지 (valid_to/recorded_to로 닫을 것)'); END;

CREATE TRIGGER trg_edition_confirm_ins BEFORE INSERT ON edition
WHEN NEW.epistemic_status = 'confirmed_fact' AND NOT EXISTS (SELECT 1 FROM source s WHERE s.source_id = NEW.source_id AND s.is_external = 1)
BEGIN SELECT RAISE(ABORT, 'edition: confirmed_fact는 외부 출처가 필요하다'); END;
CREATE TRIGGER trg_edition_confirm_upd BEFORE UPDATE OF epistemic_status ON edition
WHEN NEW.epistemic_status = 'confirmed_fact' AND (NEW.evidence_id IS NULL OR NOT EXISTS (SELECT 1 FROM source s WHERE s.source_id = NEW.source_id AND s.is_external = 1))
BEGIN SELECT RAISE(ABORT, 'edition: confirmed_fact는 외부 출처+근거 위치가 필요하다'); END;

CREATE TRIGGER trg_edition_status_log AFTER UPDATE OF epistemic_status ON edition
WHEN NEW.epistemic_status IS NOT OLD.epistemic_status
BEGIN INSERT INTO change_log(table_name,row_id,op,old_json,new_json,reason,at)
 VALUES('edition', NEW.edition_id, 'status_change', json_object('epistemic_status',OLD.epistemic_status), json_object('epistemic_status',NEW.epistemic_status), 'epistemic_status 변경', strftime('%Y-%m-%dT%H:%M:%S+00:00','now')); END;

CREATE TRIGGER trg_bib_fact_immutable BEFORE UPDATE ON bib_fact
WHEN NEW.subject_type IS NOT OLD.subject_type OR NEW.subject_id IS NOT OLD.subject_id OR NEW.predicate IS NOT OLD.predicate OR NEW.value_text IS NOT OLD.value_text OR NEW.source_id IS NOT OLD.source_id OR NEW.source_url IS NOT OLD.source_url OR NEW.evidence_id IS NOT OLD.evidence_id OR NEW.collected_at IS NOT OLD.collected_at OR NEW.valid_from IS NOT OLD.valid_from OR NEW.valid_from_precision IS NOT OLD.valid_from_precision OR NEW.recorded_at IS NOT OLD.recorded_at OR NEW.usage_basis IS NOT OLD.usage_basis OR NEW.legacy_inbox_id IS NOT OLD.legacy_inbox_id
BEGIN SELECT RAISE(ABORT, 'bib_fact: 주장 값은 덮어쓸 수 없다 — 이전 행을 닫고 새 행을 추가할 것'); END;
CREATE TRIGGER trg_bib_fact_nodelete BEFORE DELETE ON bib_fact
BEGIN SELECT RAISE(ABORT, 'bib_fact: 삭제 금지 (valid_to/recorded_to로 닫을 것)'); END;

CREATE TRIGGER trg_bib_fact_confirm_ins BEFORE INSERT ON bib_fact
WHEN NEW.epistemic_status = 'confirmed_fact' AND NOT EXISTS (SELECT 1 FROM source s WHERE s.source_id = NEW.source_id AND s.is_external = 1)
BEGIN SELECT RAISE(ABORT, 'bib_fact: confirmed_fact는 외부 출처가 필요하다'); END;
CREATE TRIGGER trg_bib_fact_confirm_upd BEFORE UPDATE OF epistemic_status ON bib_fact
WHEN NEW.epistemic_status = 'confirmed_fact' AND (NEW.evidence_id IS NULL OR NOT EXISTS (SELECT 1 FROM source s WHERE s.source_id = NEW.source_id AND s.is_external = 1))
BEGIN SELECT RAISE(ABORT, 'bib_fact: confirmed_fact는 외부 출처+근거 위치가 필요하다'); END;

CREATE TRIGGER trg_bib_fact_status_log AFTER UPDATE OF epistemic_status ON bib_fact
WHEN NEW.epistemic_status IS NOT OLD.epistemic_status
BEGIN INSERT INTO change_log(table_name,row_id,op,old_json,new_json,reason,at)
 VALUES('bib_fact', NEW.fact_id, 'status_change', json_object('epistemic_status',OLD.epistemic_status), json_object('epistemic_status',NEW.epistemic_status), 'epistemic_status 변경', strftime('%Y-%m-%dT%H:%M:%S+00:00','now')); END;

CREATE TRIGGER trg_author_statement_immutable BEFORE UPDATE ON author_statement
WHEN NEW.author_id IS NOT OLD.author_id OR NEW.author_name_raw IS NOT OLD.author_name_raw OR NEW.work_id IS NOT OLD.work_id OR NEW.summary IS NOT OLD.summary OR NEW.statement_date IS NOT OLD.statement_date OR NEW.statement_medium IS NOT OLD.statement_medium OR NEW.source_tier IS NOT OLD.source_tier OR NEW.source_id IS NOT OLD.source_id OR NEW.source_url IS NOT OLD.source_url OR NEW.evidence_id IS NOT OLD.evidence_id OR NEW.collected_at IS NOT OLD.collected_at OR NEW.valid_from IS NOT OLD.valid_from OR NEW.valid_from_precision IS NOT OLD.valid_from_precision OR NEW.recorded_at IS NOT OLD.recorded_at OR NEW.usage_basis IS NOT OLD.usage_basis OR NEW.legacy_inbox_id IS NOT OLD.legacy_inbox_id
BEGIN SELECT RAISE(ABORT, 'author_statement: 주장 값은 덮어쓸 수 없다 — 이전 행을 닫고 새 행을 추가할 것'); END;
CREATE TRIGGER trg_author_statement_nodelete BEFORE DELETE ON author_statement
BEGIN SELECT RAISE(ABORT, 'author_statement: 삭제 금지 (valid_to/recorded_to로 닫을 것)'); END;

CREATE TRIGGER trg_author_statement_confirm_ins BEFORE INSERT ON author_statement
WHEN NEW.epistemic_status = 'confirmed_fact' AND NOT EXISTS (SELECT 1 FROM source s WHERE s.source_id = NEW.source_id AND s.is_external = 1)
BEGIN SELECT RAISE(ABORT, 'author_statement: confirmed_fact는 외부 출처가 필요하다'); END;
CREATE TRIGGER trg_author_statement_confirm_upd BEFORE UPDATE OF epistemic_status ON author_statement
WHEN NEW.epistemic_status = 'confirmed_fact' AND (NEW.evidence_id IS NULL OR NOT EXISTS (SELECT 1 FROM source s WHERE s.source_id = NEW.source_id AND s.is_external = 1))
BEGIN SELECT RAISE(ABORT, 'author_statement: confirmed_fact는 외부 출처+근거 위치가 필요하다'); END;

CREATE TRIGGER trg_author_statement_status_log AFTER UPDATE OF epistemic_status ON author_statement
WHEN NEW.epistemic_status IS NOT OLD.epistemic_status
BEGIN INSERT INTO change_log(table_name,row_id,op,old_json,new_json,reason,at)
 VALUES('author_statement', NEW.statement_id, 'status_change', json_object('epistemic_status',OLD.epistemic_status), json_object('epistemic_status',NEW.epistemic_status), 'epistemic_status 변경', strftime('%Y-%m-%dT%H:%M:%S+00:00','now')); END;

CREATE TRIGGER trg_interpretation_immutable BEFORE UPDATE ON interpretation
WHEN NEW.work_id IS NOT OLD.work_id OR NEW.theme_id IS NOT OLD.theme_id OR NEW.kind IS NOT OLD.kind OR NEW.attributed_to_name IS NOT OLD.attributed_to_name OR NEW.stance IS NOT OLD.stance OR NEW.summary IS NOT OLD.summary OR NEW.source_id IS NOT OLD.source_id OR NEW.source_url IS NOT OLD.source_url OR NEW.evidence_id IS NOT OLD.evidence_id OR NEW.collected_at IS NOT OLD.collected_at OR NEW.valid_from IS NOT OLD.valid_from OR NEW.valid_from_precision IS NOT OLD.valid_from_precision OR NEW.recorded_at IS NOT OLD.recorded_at OR NEW.usage_basis IS NOT OLD.usage_basis OR NEW.legacy_inbox_id IS NOT OLD.legacy_inbox_id
BEGIN SELECT RAISE(ABORT, 'interpretation: 주장 값은 덮어쓸 수 없다 — 이전 행을 닫고 새 행을 추가할 것'); END;
CREATE TRIGGER trg_interpretation_nodelete BEFORE DELETE ON interpretation
BEGIN SELECT RAISE(ABORT, 'interpretation: 삭제 금지 (valid_to/recorded_to로 닫을 것)'); END;

CREATE TRIGGER trg_interpretation_status_log AFTER UPDATE OF epistemic_status ON interpretation
WHEN NEW.epistemic_status IS NOT OLD.epistemic_status
BEGIN INSERT INTO change_log(table_name,row_id,op,old_json,new_json,reason,at)
 VALUES('interpretation', NEW.interp_id, 'status_change', json_object('epistemic_status',OLD.epistemic_status), json_object('epistemic_status',NEW.epistemic_status), 'epistemic_status 변경', strftime('%Y-%m-%dT%H:%M:%S+00:00','now')); END;

CREATE TRIGGER trg_work_author_immutable BEFORE UPDATE ON work_author
WHEN NEW.work_id IS NOT OLD.work_id OR NEW.author_id IS NOT OLD.author_id OR NEW.role IS NOT OLD.role OR NEW.source_id IS NOT OLD.source_id OR NEW.source_url IS NOT OLD.source_url OR NEW.evidence_id IS NOT OLD.evidence_id OR NEW.collected_at IS NOT OLD.collected_at OR NEW.valid_from IS NOT OLD.valid_from OR NEW.valid_from_precision IS NOT OLD.valid_from_precision OR NEW.recorded_at IS NOT OLD.recorded_at OR NEW.usage_basis IS NOT OLD.usage_basis OR NEW.legacy_inbox_id IS NOT OLD.legacy_inbox_id
BEGIN SELECT RAISE(ABORT, 'work_author: 주장 값은 덮어쓸 수 없다 — 이전 행을 닫고 새 행을 추가할 것'); END;
CREATE TRIGGER trg_work_author_nodelete BEFORE DELETE ON work_author
BEGIN SELECT RAISE(ABORT, 'work_author: 삭제 금지 (valid_to/recorded_to로 닫을 것)'); END;

CREATE TRIGGER trg_work_author_confirm_ins BEFORE INSERT ON work_author
WHEN NEW.epistemic_status = 'confirmed_fact' AND NOT EXISTS (SELECT 1 FROM source s WHERE s.source_id = NEW.source_id AND s.is_external = 1)
BEGIN SELECT RAISE(ABORT, 'work_author: confirmed_fact는 외부 출처가 필요하다'); END;
CREATE TRIGGER trg_work_author_confirm_upd BEFORE UPDATE OF epistemic_status ON work_author
WHEN NEW.epistemic_status = 'confirmed_fact' AND (NEW.evidence_id IS NULL OR NOT EXISTS (SELECT 1 FROM source s WHERE s.source_id = NEW.source_id AND s.is_external = 1))
BEGIN SELECT RAISE(ABORT, 'work_author: confirmed_fact는 외부 출처+근거 위치가 필요하다'); END;

CREATE TRIGGER trg_work_author_status_log AFTER UPDATE OF epistemic_status ON work_author
WHEN NEW.epistemic_status IS NOT OLD.epistemic_status
BEGIN INSERT INTO change_log(table_name,row_id,op,old_json,new_json,reason,at)
 VALUES('work_author', NEW.wa_id, 'status_change', json_object('epistemic_status',OLD.epistemic_status), json_object('epistemic_status',NEW.epistemic_status), 'epistemic_status 변경', strftime('%Y-%m-%dT%H:%M:%S+00:00','now')); END;

CREATE TRIGGER trg_work_theme_immutable BEFORE UPDATE ON work_theme
WHEN NEW.work_id IS NOT OLD.work_id OR NEW.theme_id IS NOT OLD.theme_id OR NEW.source_id IS NOT OLD.source_id OR NEW.source_url IS NOT OLD.source_url OR NEW.evidence_id IS NOT OLD.evidence_id OR NEW.collected_at IS NOT OLD.collected_at OR NEW.valid_from IS NOT OLD.valid_from OR NEW.valid_from_precision IS NOT OLD.valid_from_precision OR NEW.recorded_at IS NOT OLD.recorded_at OR NEW.usage_basis IS NOT OLD.usage_basis OR NEW.legacy_inbox_id IS NOT OLD.legacy_inbox_id
BEGIN SELECT RAISE(ABORT, 'work_theme: 주장 값은 덮어쓸 수 없다 — 이전 행을 닫고 새 행을 추가할 것'); END;
CREATE TRIGGER trg_work_theme_nodelete BEFORE DELETE ON work_theme
BEGIN SELECT RAISE(ABORT, 'work_theme: 삭제 금지 (valid_to/recorded_to로 닫을 것)'); END;

CREATE TRIGGER trg_work_theme_status_log AFTER UPDATE OF epistemic_status ON work_theme
WHEN NEW.epistemic_status IS NOT OLD.epistemic_status
BEGIN INSERT INTO change_log(table_name,row_id,op,old_json,new_json,reason,at)
 VALUES('work_theme', NEW.wt_id, 'status_change', json_object('epistemic_status',OLD.epistemic_status), json_object('epistemic_status',NEW.epistemic_status), 'epistemic_status 변경', strftime('%Y-%m-%dT%H:%M:%S+00:00','now')); END;

CREATE TRIGGER trg_interpretation_relation_immutable BEFORE UPDATE ON interpretation_relation
WHEN NEW.a_interp_id IS NOT OLD.a_interp_id OR NEW.b_interp_id IS NOT OLD.b_interp_id OR NEW.relation IS NOT OLD.relation OR NEW.source_id IS NOT OLD.source_id OR NEW.source_url IS NOT OLD.source_url OR NEW.evidence_id IS NOT OLD.evidence_id OR NEW.collected_at IS NOT OLD.collected_at OR NEW.valid_from IS NOT OLD.valid_from OR NEW.valid_from_precision IS NOT OLD.valid_from_precision OR NEW.recorded_at IS NOT OLD.recorded_at OR NEW.usage_basis IS NOT OLD.usage_basis OR NEW.legacy_inbox_id IS NOT OLD.legacy_inbox_id
BEGIN SELECT RAISE(ABORT, 'interpretation_relation: 주장 값은 덮어쓸 수 없다 — 이전 행을 닫고 새 행을 추가할 것'); END;
CREATE TRIGGER trg_interpretation_relation_nodelete BEFORE DELETE ON interpretation_relation
BEGIN SELECT RAISE(ABORT, 'interpretation_relation: 삭제 금지 (valid_to/recorded_to로 닫을 것)'); END;

CREATE TRIGGER trg_interpretation_relation_status_log AFTER UPDATE OF epistemic_status ON interpretation_relation
WHEN NEW.epistemic_status IS NOT OLD.epistemic_status
BEGIN INSERT INTO change_log(table_name,row_id,op,old_json,new_json,reason,at)
 VALUES('interpretation_relation', NEW.rel_id, 'status_change', json_object('epistemic_status',OLD.epistemic_status), json_object('epistemic_status',NEW.epistemic_status), 'epistemic_status 변경', strftime('%Y-%m-%dT%H:%M:%S+00:00','now')); END;

CREATE TRIGGER trg_evidence_immutable BEFORE UPDATE ON evidence_passage
BEGIN SELECT RAISE(ABORT, 'evidence_passage: 수정 금지 — 새 근거 행을 추가할 것'); END;
CREATE TRIGGER trg_evidence_nodelete BEFORE DELETE ON evidence_passage
BEGIN SELECT RAISE(ABORT, 'evidence_passage: 삭제 금지'); END;
CREATE TRIGGER trg_changelog_noupdate BEFORE UPDATE ON change_log
BEGIN SELECT RAISE(ABORT, 'change_log는 append-only'); END;
CREATE TRIGGER trg_changelog_nodelete BEFORE DELETE ON change_log
BEGIN SELECT RAISE(ABORT, 'change_log는 append-only'); END;
CREATE TRIGGER trg_snapshot_noupdate BEFORE UPDATE ON source_snapshot
BEGIN SELECT RAISE(ABORT, 'source_snapshot은 append-only'); END;
CREATE TRIGGER trg_snapshot_nodelete BEFORE DELETE ON source_snapshot
BEGIN SELECT RAISE(ABORT, 'source_snapshot은 append-only'); END;
CREATE TRIGGER trg_conflict_nodelete BEFORE DELETE ON conflict
BEGIN SELECT RAISE(ABORT, 'conflict 삭제 금지 (status로 닫을 것)'); END;
CREATE TRIGGER trg_aiinf_nodelete BEFORE DELETE ON ai_inference
BEGIN SELECT RAISE(ABORT, 'ai_inference 삭제 금지'); END;
