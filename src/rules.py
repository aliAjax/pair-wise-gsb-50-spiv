"""税务稽查案件与复议流程领域规则与状态转换。"""
from typing import Any, Dict, Iterable, Tuple

from .domain import Actor, Conflict, ValidationError, boolean, choice, integer, number, optional_text, text, text_list


INITIAL_STATE = "opened"
CREATE_ROLES = {'inspector'}
ACTION_ROLES = {'investigate': {'inspector'}, 'propose': {'inspector'}, 'defend': {'taxpayer_rep'}, 'submit_evidence': {'taxpayer_rep'}, 'request_hearing': {'taxpayer_rep'}, 'conclude_hearing': {'inspector'}, 'review': {'reviewer'}, 'appeal': {'taxpayer_rep'}, 'close': {'reviewer'}}
TRANSITIONS = {'investigate': {'opened': 'investigating'}, 'propose': {'investigating': 'proposed'}, 'defend': {'proposed': 'proposed'}, 'submit_evidence': {'proposed': 'proposed'}, 'request_hearing': {'proposed': 'proposed'}, 'conclude_hearing': {'proposed': 'proposed'}, 'review': {'proposed': 'reviewed'}, 'appeal': {'reviewed': 'appealed'}, 'close': {'reviewed': 'closed', 'appealed': 'closed'}}


def compute_amounts(p: Dict[str, Any]) -> Dict[str, float]:
    difference = max(0.0, float(p["assessed_tax"]) - float(p["declared_tax"]))
    interest = difference * 0.0005 * int(p["days_late"])
    penalty = difference * float(p["penalty_rate"])
    return {'tax_difference': round(difference, 2), 'interest': round(interest, 2), 'penalty': round(penalty, 2), 'total_due': round(difference + interest + penalty, 2), 'refund_due': round(max(0.0, float(p["declared_tax"]) - float(p["assessed_tax"])), 2)}


class DomainRules:
    INITIAL_STATE = INITIAL_STATE

    def known_role(self, role: str) -> bool:
        all_roles = set(CREATE_ROLES)
        for roles in ACTION_ROLES.values():
            all_roles.update(roles)
        return role == "admin" or role in all_roles

    def role_can_create(self, role: str) -> bool:
        return role == "admin" or role in CREATE_ROLES

    def role_can_action(self, role: str, action: str) -> bool:
        return role == "admin" or role in ACTION_ROLES.get(action, set())

    def validate_create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        p = dict(payload)
        text(p, "taxpayer")
        text(p, "tax_period")
        number(p, "declared_tax", 0)
        number(p, "assessed_tax", 0)
        number(p, "penalty_rate", 0, 1)
        integer(p, "evidence_count", 0)
        integer(p, "days_late", 0)
        integer(p, "appeal_deadline_day", 1)
        return p

    def prepare_create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        p = self.validate_create(payload)
        p.update(compute_amounts(p))
        return p

    def check_create_conflicts(self, payload: Dict[str, Any], existing: Iterable[Dict[str, Any]]) -> None:
        for item in existing:
            if item["state"] not in {"closed"} and item["payload"].get("taxpayer") == payload.get("taxpayer") and item["payload"].get("tax_period") == payload.get("tax_period"):
                raise Conflict("同一纳税人同一税期已有未结稽查案件")

    def require_transition(self, record: Dict[str, Any], action: str) -> str:
        allowed = TRANSITIONS.get(action, {}).get(record["state"])
        if allowed is None:
            raise Conflict("当前状态不允许执行%s" % action)
        return allowed

    @staticmethod
    def _apply_new_assessed_tax(p: Dict[str, Any], changes: Dict[str, Any], data: Dict[str, Any], source: str) -> bool:
        if data.get("assessed_tax") is None:
            return False
        new_assessed = number(data, "assessed_tax", 0)
        if new_assessed == float(p["assessed_tax"]):
            return False
        changes["assessed_tax"] = new_assessed
        merged = dict(p)
        merged.update(changes)
        amounts = compute_amounts(merged)
        changes.update(amounts)
        changes["amount_changed"] = True
        revisions = list(p.get("amount_revisions", []))
        revisions.append({"source": source, "assessed_tax": new_assessed, "total_due": amounts["total_due"]})
        changes["amount_revisions"] = revisions
        return True

    def apply_action(self, record: Dict[str, Any], action: str, data: Dict[str, Any]) -> Tuple[str, Dict[str, Any], str]:
        new_state = self.require_transition(record, action)
        data = dict(data or {})
        p = dict(record["payload"])
        changes: Dict[str, Any] = {}
        summary = ""
        if action == "investigate":
            changes["investigation_plan"] = text(data, "plan")
            summary = "进入稽查调查"
        elif action == "propose":
            if int(p["evidence_count"]) <= 0:
                raise ValidationError("没有证据不能提出处理建议")
            changes["proposal"] = text(data, "proposal")
            changes["defense_deadline_day"] = integer(data, "defense_deadline_day", 1)
            changes["hearing_deadline_day"] = integer(data, "hearing_deadline_day", 1)
            changes["proposed_amount"] = float(p["total_due"])
            changes["hearing_status"] = "none"
            changes["amount_changed"] = False
            summary = "已提出补税和处罚建议"
        elif action == "defend":
            day = integer(data, "defense_day", 0)
            deadline = int(p.get("defense_deadline_day", 0))
            accepted = day <= deadline
            entry = {"day": day, "statement": text(data, "statement"), "accepted": accepted}
            if accepted:
                summary = "陈述申辩已受理"
            else:
                entry["note"] = "超过申辩截止日(第%s天)，仅留记录不予受理" % deadline
                summary = "陈述申辩逾期，仅留记录并注明不受理"
            changes["defenses"] = list(p.get("defenses", [])) + [entry]
        elif action == "submit_evidence":
            day = integer(data, "evidence_day", 0)
            items = text_list(data, "items", 1)
            deadline = int(p.get("defense_deadline_day", 0))
            accepted = day <= deadline
            entry = {"day": day, "items": items, "accepted": accepted}
            if accepted:
                changes["evidence_count"] = int(p["evidence_count"]) + len(items)
                if self._apply_new_assessed_tax(p, changes, data, "evidence"):
                    entry["assessed_tax"] = changes["assessed_tax"]
                    summary = "补交证据已受理，税额已重新计算"
                else:
                    summary = "补交证据已受理"
            else:
                entry["note"] = "超过申辩截止日(第%s天)，仅留记录不予受理" % deadline
                summary = "补交证据逾期，仅留记录并注明不受理"
            changes["evidence_submissions"] = list(p.get("evidence_submissions", [])) + [entry]
        elif action == "request_hearing":
            day = integer(data, "request_day", 0)
            reason = text(data, "reason")
            deadline = int(p.get("hearing_deadline_day", 0))
            accepted = day <= deadline
            entry = {"day": day, "reason": reason, "accepted": accepted}
            if accepted:
                if p.get("hearing_status") == "open":
                    raise Conflict("已有受理未办结的听证")
                changes["hearing_status"] = "open"
                summary = "听证申请已受理"
            else:
                entry["note"] = "超过听证申请期限(第%s天)，仅留记录不予受理" % deadline
                summary = "听证申请逾期，仅留记录并注明不受理"
            changes["hearing_requests"] = list(p.get("hearing_requests", [])) + [entry]
        elif action == "conclude_hearing":
            if p.get("hearing_status") != "open":
                raise Conflict("当前没有未办结的听证")
            changes["hearing_status"] = "concluded"
            changes["hearing_conclusion"] = text(data, "conclusion")
            if self._apply_new_assessed_tax(p, changes, data, "hearing"):
                summary = "听证已办结，税额已重新计算"
            else:
                summary = "听证已办结"
        elif action == "review":
            if p.get("hearing_status") == "open":
                raise Conflict("听证未办结，不能确认决定")
            outcome = choice(data, "outcome", ["accepted", "reduced", "remanded"])
            basis = optional_text(data, "decision_basis")
            basis_required = bool(p.get("amount_changed")) or p.get("hearing_status") == "concluded"
            if basis_required and not basis:
                raise ValidationError("听证或新证据已影响原建议，复核人员须重新核对并填写decision_basis")
            changes["review_outcome"] = outcome
            changes["review_note"] = text(data, "review_note")
            if basis:
                changes["decision_basis"] = basis
            if outcome == "reduced":
                changes["total_due"] = round(float(p["total_due"]) * float(data.get("reduction_pct", 0.5)), 2)
            summary = "复核完成"
        elif action == "appeal":
            appeal_day = integer(data, "appeal_day", 0)
            if appeal_day > int(p["appeal_deadline_day"]):
                raise ValidationError("复议申请超过期限")
            changes["appeal_day"] = appeal_day
            changes["appeal_reason"] = text(data, "appeal_reason")
            summary = "复议申请已受理"
        elif action == "close":
            changes["final_decision"] = text(data, "final_decision")
            summary = "案件已结案"
        p.update(changes)
        return new_state, p, summary or ("已执行%s" % action)

    def workbench_status(self, record: Dict[str, Any]) -> Dict[str, Any]:
        p = record["payload"]
        hearing_status = p.get("hearing_status", "none")
        amount_changed = bool(p.get("amount_changed"))
        pending = []
        if hearing_status == "open":
            pending.append("听证未办结")
        return {
            "record_id": record["id"],
            "state": record["state"],
            "version": record["version"],
            "defense_deadline_day": p.get("defense_deadline_day"),
            "hearing_deadline_day": p.get("hearing_deadline_day"),
            "defenses": p.get("defenses", []),
            "evidence_submissions": p.get("evidence_submissions", []),
            "hearing_requests": p.get("hearing_requests", []),
            "hearing_status": hearing_status,
            "hearing_conclusion": p.get("hearing_conclusion", ""),
            "amount_changed": amount_changed,
            "amount_revisions": p.get("amount_revisions", []),
            "current_amounts": {key: p.get(key) for key in ("tax_difference", "interest", "penalty", "total_due", "refund_due")},
            "proposed_amount": p.get("proposed_amount"),
            "procedure_pending": pending,
            "procedure_complete": not pending,
            "review_ready": record["state"] == "proposed" and not pending,
            "review_blockers": list(pending),
            "decision_basis_required": amount_changed or hearing_status == "concluded",
            "decision_basis": p.get("decision_basis", ""),
        }
