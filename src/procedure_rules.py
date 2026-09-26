"""陈述申辩、补交证据与听证程序的判定规则，全部为纯函数。"""
from typing import Any, Dict, List, Optional, Tuple


KIND_STATEMENT = "statement"
KIND_EVIDENCE = "evidence"
KIND_HEARING_REQUEST = "hearing_request"
KIND_HEARING_CONCLUSION = "hearing_conclusion"
KINDS = (KIND_STATEMENT, KIND_EVIDENCE, KIND_HEARING_REQUEST, KIND_HEARING_CONCLUSION)

STATUS_ACCEPTED = "accepted"
STATUS_LATE = "late_filed"
STATUS_CONCLUDED = "concluded"

HEARING_NONE = "none"
HEARING_PENDING = "pending"
HEARING_CONCLUDED = "concluded"

SUBMIT_ROLES = {
    KIND_STATEMENT: {"taxpayer_rep"},
    KIND_EVIDENCE: {"taxpayer_rep"},
    KIND_HEARING_REQUEST: {"taxpayer_rep"},
    KIND_HEARING_CONCLUSION: {"reviewer"},
}

DEADLINE_LABELS = {
    KIND_STATEMENT: "申辩截止日",
    KIND_EVIDENCE: "申辩截止日",
    KIND_HEARING_REQUEST: "听证申请期限",
}


def role_can_submit(role: str, kind: str) -> bool:
    return role == "admin" or role in SUBMIT_ROLES.get(kind, set())


def deadline_for(kind: str, payload: Dict[str, Any]) -> Optional[int]:
    if kind in (KIND_STATEMENT, KIND_EVIDENCE):
        value = payload.get("defense_deadline_day")
    elif kind == KIND_HEARING_REQUEST:
        value = payload.get("hearing_request_deadline_day")
    else:
        return None
    return int(value) if value is not None else None


def evaluate_submission(kind: str, submitted_day: int, payload: Dict[str, Any]) -> Tuple[str, str]:
    deadline = deadline_for(kind, payload)
    if deadline is not None and int(submitted_day) > deadline:
        note = "超过%s（第%s日），不予受理，仅留存记录" % (DEADLINE_LABELS.get(kind, "办理期限"), deadline)
        return STATUS_LATE, note
    return STATUS_ACCEPTED, ""


def hearing_status(procedures: List[Dict[str, Any]]) -> Dict[str, Any]:
    request = None
    conclusion = None
    for row in procedures:
        if row["kind"] == KIND_HEARING_REQUEST and row["status"] == STATUS_ACCEPTED:
            request = row
            conclusion = None
        elif row["kind"] == KIND_HEARING_CONCLUSION:
            conclusion = row
    if request is None:
        status = HEARING_NONE
    elif conclusion is None:
        status = HEARING_PENDING
    else:
        status = HEARING_CONCLUDED
    return {"status": status, "request": request, "conclusion": conclusion}


def blocking_reasons(record: Dict[str, Any], procedures: List[Dict[str, Any]]) -> List[str]:
    reasons = []
    if hearing_status(procedures)["status"] == HEARING_PENDING:
        reasons.append("听证未办结，原决定不能确认")
    if record["payload"].get("needs_recheck"):
        reasons.append("新证据已改变金额，需复核人员重新核对")
    return reasons


def procedure_complete(record: Dict[str, Any], procedures: List[Dict[str, Any]]) -> bool:
    return not blocking_reasons(record, procedures)


def submission_summary(kind: str, status: str, amounts_changed: bool = False) -> str:
    if status == STATUS_LATE:
        return {
            KIND_STATEMENT: "陈述申辩逾期，仅留存记录",
            KIND_EVIDENCE: "补交证据逾期，仅留存记录",
            KIND_HEARING_REQUEST: "听证申请逾期，不予受理，仅留存记录",
        }.get(kind, "逾期提交，仅留存记录")
    if kind == KIND_STATEMENT:
        return "陈述申辩已受理"
    if kind == KIND_EVIDENCE:
        return "新证据已受理，金额重新核算" if amounts_changed else "新证据已受理"
    if kind == KIND_HEARING_REQUEST:
        return "听证申请已受理"
    if kind == KIND_HEARING_CONCLUSION:
        return "听证已办结"
    return "已受理"
