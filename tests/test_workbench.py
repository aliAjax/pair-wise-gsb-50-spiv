import tempfile
import unittest
from pathlib import Path

from app import build_service
from src.domain import Actor, Conflict, ValidationError


CREATE_DATA = {'taxpayer': 'Star Ltd', 'tax_period': '2025-Q4', 'declared_tax': 500000.0, 'assessed_tax': 760000.0, 'penalty_rate': 0.2, 'evidence_count': 4, 'days_late': 90, 'appeal_deadline_day': 60}
PROPOSE_DATA = {'proposal': '补税并处罚', 'defense_deadline_day': 10, 'hearing_deadline_day': 15}
INSPECTOR = Actor('inspector-1', 'inspector')
REVIEWER = Actor('reviewer-1', 'reviewer')
REP = Actor('rep-1', 'taxpayer_rep')


class WorkbenchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / 'test.db')
        self.service = build_service(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def _proposed(self, reference='TAX-26001'):
        record = self.service.create(INSPECTOR, reference, CREATE_DATA)
        record = self.service.act(INSPECTOR, record['id'], record['version'], 'investigate', {'plan': '核对账簿'})
        return self.service.act(INSPECTOR, record['id'], record['version'], 'propose', PROPOSE_DATA)

    def test_propose_writes_deadlines(self):
        record = self._proposed()
        self.assertEqual(record['payload']['defense_deadline_day'], 10)
        self.assertEqual(record['payload']['hearing_deadline_day'], 15)
        self.assertEqual(record['payload']['proposed_amount'], 323700.0)
        wb = self.service.workbench(REVIEWER, record['id'])
        self.assertTrue(wb['procedure_complete'])
        self.assertTrue(wb['review_ready'])

    def test_propose_without_deadline_rejected(self):
        record = self.service.create(INSPECTOR, 'TAX-26001', CREATE_DATA)
        record = self.service.act(INSPECTOR, record['id'], record['version'], 'investigate', {'plan': '核对账簿'})
        with self.assertRaises(ValidationError):
            self.service.act(INSPECTOR, record['id'], record['version'], 'propose', {'proposal': '补税并处罚'})

    def test_defend_accepted_and_overdue_recorded(self):
        record = self._proposed()
        record = self.service.act(REP, record['id'], record['version'], 'defend', {'defense_day': 5, 'statement': '已按期申报'})
        self.assertTrue(record['payload']['defenses'][0]['accepted'])
        record = self.service.act(REP, record['id'], record['version'], 'defend', {'defense_day': 11, 'statement': '逾期补充说明'})
        entry = record['payload']['defenses'][1]
        self.assertFalse(entry['accepted'])
        self.assertIn('不予受理', entry['note'])
        self.assertEqual(record['state'], 'proposed')
        wb = self.service.workbench(REVIEWER, record['id'])
        self.assertEqual(len(wb['defenses']), 2)

    def test_overdue_hearing_request_only_recorded(self):
        record = self._proposed()
        record = self.service.act(REP, record['id'], record['version'], 'request_hearing', {'request_day': 20, 'reason': '要求听证'})
        entry = record['payload']['hearing_requests'][0]
        self.assertFalse(entry['accepted'])
        self.assertIn('不予受理', entry['note'])
        self.assertEqual(record['payload']['hearing_status'], 'none')
        record = self.service.act(REVIEWER, record['id'], record['version'], 'review', {'outcome': 'accepted', 'review_note': '证据充分'})
        self.assertEqual(record['state'], 'reviewed')

    def test_open_hearing_blocks_review_until_concluded(self):
        record = self._proposed()
        record = self.service.act(REP, record['id'], record['version'], 'request_hearing', {'request_day': 3, 'reason': '对核定有异议'})
        self.assertEqual(record['payload']['hearing_status'], 'open')
        with self.assertRaises(Conflict):
            self.service.act(REVIEWER, record['id'], record['version'], 'review', {'outcome': 'accepted', 'review_note': '证据充分'})
        wb = self.service.workbench(REVIEWER, record['id'])
        self.assertFalse(wb['procedure_complete'])
        self.assertFalse(wb['review_ready'])
        record = self.service.act(INSPECTOR, record['id'], record['version'], 'conclude_hearing', {'conclusion': '听证维持原核定'})
        self.assertEqual(record['payload']['hearing_status'], 'concluded')
        with self.assertRaises(ValidationError):
            self.service.act(REVIEWER, record['id'], record['version'], 'review', {'outcome': 'accepted', 'review_note': '证据充分'})
        record = self.service.act(REVIEWER, record['id'], record['version'], 'review', {'outcome': 'accepted', 'review_note': '证据充分', 'decision_basis': '听证结论维持原核定，复核无误'})
        self.assertEqual(record['state'], 'reviewed')
        self.assertEqual(record['payload']['decision_basis'], '听证结论维持原核定，复核无误')

    def test_new_evidence_changes_amount_and_requires_basis(self):
        record = self._proposed()
        record = self.service.act(REP, record['id'], record['version'], 'submit_evidence', {'evidence_day': 4, 'items': ['成本发票', '银行回单'], 'assessed_tax': 600000.0})
        payload = record['payload']
        self.assertEqual(payload['evidence_count'], 6)
        self.assertTrue(payload['amount_changed'])
        self.assertEqual(payload['total_due'], 124500.0)
        self.assertEqual(payload['amount_revisions'][0]['source'], 'evidence')
        wb = self.service.workbench(REVIEWER, record['id'])
        self.assertTrue(wb['decision_basis_required'])
        with self.assertRaises(ValidationError):
            self.service.act(REVIEWER, record['id'], record['version'], 'review', {'outcome': 'accepted', 'review_note': '证据充分'})
        record = self.service.act(REVIEWER, record['id'], record['version'], 'review', {'outcome': 'accepted', 'review_note': '重新核对', 'decision_basis': '依据新证据重新核定税额'})
        self.assertEqual(record['state'], 'reviewed')
        self.assertEqual(record['payload']['decision_basis'], '依据新证据重新核定税额')

    def test_overdue_evidence_not_accepted(self):
        record = self._proposed()
        record = self.service.act(REP, record['id'], record['version'], 'submit_evidence', {'evidence_day': 12, 'items': ['迟到材料'], 'assessed_tax': 100.0})
        payload = record['payload']
        self.assertFalse(payload['evidence_submissions'][0]['accepted'])
        self.assertEqual(payload['evidence_count'], 4)
        self.assertEqual(payload['total_due'], 323700.0)
        self.assertFalse(payload['amount_changed'])

    def test_hearing_conclusion_can_change_amount(self):
        record = self._proposed()
        record = self.service.act(REP, record['id'], record['version'], 'request_hearing', {'request_day': 3, 'reason': '有异议'})
        record = self.service.act(INSPECTOR, record['id'], record['version'], 'conclude_hearing', {'conclusion': '听证后调整核定', 'assessed_tax': 600000.0})
        self.assertEqual(record['payload']['total_due'], 124500.0)
        wb = self.service.workbench(REVIEWER, record['id'])
        self.assertTrue(wb['procedure_complete'])
        self.assertTrue(wb['decision_basis_required'])

    def test_conclude_without_open_hearing_conflicts(self):
        record = self._proposed()
        with self.assertRaises(Conflict):
            self.service.act(INSPECTOR, record['id'], record['version'], 'conclude_hearing', {'conclusion': '无听证'})

    def test_restart_keeps_workbench_visible(self):
        record = self._proposed()
        record = self.service.act(REP, record['id'], record['version'], 'defend', {'defense_day': 2, 'statement': '情况说明'})
        reopened = build_service(self.db)
        loaded = reopened.get_record(REVIEWER, record['id'])
        self.assertEqual(loaded['payload']['defenses'][0]['statement'], '情况说明')
        wb = reopened.workbench(REVIEWER, record['id'])
        self.assertEqual(wb['defense_deadline_day'], 10)
        self.assertEqual(wb['hearing_deadline_day'], 15)
        timeline = reopened.timeline(REVIEWER, record['id'])
        self.assertEqual([event['action'] for event in timeline], ['created', 'investigate', 'propose', 'defend'])


if __name__ == '__main__':
    unittest.main()
