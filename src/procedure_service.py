"""程序办理台用例编排：提交、办结与查看，判定和保存分别委托给 procedure_rules 与 ProcedureRepository。"""
from typing import Any, Dict, Optional, Tuple

from .audit import AuditRecorder
from .domain import Actor, Conflict, PermissionDenied, choice, integer, optional_integer, optional_number, text
from .procedure_repository import ProcedureRepository
from .procedure_rules import (
    HEARING_PENDING,
    KIND_EVIDENCE,
    KIND_HEARING_CONCLUSION,
    KIND_HEARING_REQUEST,
    KINDS,
    STATUS_ACCEPTED,
    STATUS_CONCLUDED,
    STATUS_LATE,
    blocking_reasons,
    evaluate_submission,
    hearing_status,
    role_can_submit,
    submission_summary,
)
from .repository import Repository
from .rules import AMOUNT_KEYS, DomainRules, compute_amounts


class ProcedureService:
    def __init__(self, repository: Repository, procedures: ProcedureRepository, rules: DomainRules, audit: AuditRecorder = None) -> None:
        self.repository = repository
        self.procedures = procedures
        self.rules = rules
        self.audit = audit or AuditRecorder(repository)

    def _actor(self, actor: Actor) -> Actor:
        if actor is None or not actor.user_id.strip() or not actor.role.strip():
            raise PermissionDenied("缺少调用身份")
        if not self.rules.known_role(actor.role):
            raise PermissionDenied("角色无权访问该服务")
        return actor

    def submit(self, actor: Actor, record_id: int, kind: str, data: Dict[str, Any]) -> Dict[str, Any]:
        actor = self._actor(actor)
        kind = choice({"kind": kind}, "kind", list(KINDS))
        if not role_can_submit(actor.role, kind):
            raise PermissionDenied("角色无权提交该程序材料")
        record = self.repository.get(record_id)
        if record["state"] != "proposed":
            raise Conflict("补税和处罚建议发出后、复核确认前才能提交程序材料")
        data = dict(data or {})
        submitted_day = integer(data, "submitted_day", 0)
        content = text(data, "content")
        hearing = hearing_status(self.procedures.list_for_record(record_id))
        if kind == KIND_HEARING_CONCLUSION:
            if hearing["status"] != HEARING_PENDING:
                raise Conflict("没有待办结的听证")
            status, note = STATUS_CONCLUDED, ""
        else:
            status, note = evaluate_submission(kind, submitted_day, record["payload"])
            if kind == KIND_HEARING_REQUEST and status == STATUS_ACCEPTED and hearing["status"] == HEARING_PENDING:
                raise Conflict("已有待办结的听证申请")
        details: Dict[str, Any] = {}
        updated = None
        amounts_changed = False
        if kind == KIND_EVIDENCE and status == STATUS_ACCEPTED:
            updated, details = self._apply_evidence(actor, record, data)
            amounts_changed = bool(details.get("amounts_changed"))
        row = self.procedures.add(record_id, kind, status, submitted_day, content, note, details, actor.user_id)
        summary = submission_summary(kind, status, amounts_changed)
        if updated is None:
            self.audit.note(record_id, actor.user_id, kind, {"summary": summary, "status": status, "note": note, "submitted_day": submitted_day})
        return {"procedure": row, "record": updated or self.repository.get(record_id), "summary": summary}

    def _apply_evidence(self, actor: Actor, record: Dict[str, Any], data: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        p = record["payload"]
        assessed = optional_number(data, "assessed_tax", 0)
        days_late = optional_integer(data, "days_late", 0)
        penalty_rate = optional_number(data, "penalty_rate", 0, 1)
        additional = optional_integer(data, "additional_evidence_count", 0) or 0
        merged = {
            "assessed_tax": assessed if assessed is not None else float(p["assessed_tax"]),
            "days_late": days_late if days_late is not None else int(p["days_late"]),
            "penalty_rate": penalty_rate if penalty_rate is not None else float(p["penalty_rate"]),
        }
        before = {key: p[key] for key in AMOUNT_KEYS}
        after = compute_amounts(p["declared_tax"], merged["assessed_tax"], merged["penalty_rate"], merged["days_late"])
        amounts_changed = any(after[key] != before[key] for key in AMOUNT_KEYS)
        evidence_count = int(p["evidence_count"]) + additional
        details: Dict[str, Any] = {"amounts_changed": amounts_changed, "additional_evidence_count": additional}
        if not amounts_changed and evidence_count == int(p["evidence_count"]):
            return None, details
        new_payload = dict(p)
        new_payload.update(after)
        new_payload.update(merged)
        new_payload["evidence_count"] = evidence_count
        if amounts_changed:
            new_payload["needs_recheck"] = True
        details["amounts_before"] = before
        details["amounts_after"] = after
        updated = self.repository.mutate(
            record_id=record["id"],
            expected_version=int(record["version"]),
            state=record["state"],
            payload=new_payload,
            actor_id=actor.user_id,
            action="evidence",
            details={"summary": submission_summary(KIND_EVIDENCE, STATUS_ACCEPTED, amounts_changed), "amounts_before": before, "amounts_after": after, "evidence_count": evidence_count},
        )
        return updated, details

    def desk(self, actor: Actor, record_id: int) -> Dict[str, Any]:
        actor = self._actor(actor)
        record = self.repository.get(record_id)
        procedures = self.procedures.list_for_record(record_id)
        hearing = hearing_status(procedures)
        reasons = blocking_reasons(record, procedures)
        payload = record["payload"]
        return {
            "record": record,
            "deadlines": {
                "defense_deadline_day": payload.get("defense_deadline_day"),
                "hearing_request_deadline_day": payload.get("hearing_request_deadline_day"),
            },
            "procedures": procedures,
            "hearing": hearing,
            "needs_recheck": bool(payload.get("needs_recheck")),
            "blocking_reasons": reasons,
            "procedure_complete": not reasons,
            "summary": {
                "statements": sum(1 for row in procedures if row["kind"] == "statement"),
                "evidences": sum(1 for row in procedures if row["kind"] == "evidence"),
                "hearing_requests": sum(1 for row in procedures if row["kind"] == "hearing_request"),
                "late_filed": sum(1 for row in procedures if row["status"] == STATUS_LATE),
            },
        }
