import tempfile
import unittest
from pathlib import Path

from app import build_services
from src.domain import Actor, Conflict, PermissionDenied, ValidationError


CREATE_DATA = {'taxpayer': 'Star Ltd', 'tax_period': '2025-Q4', 'declared_tax': 500000.0, 'assessed_tax': 760000.0, 'penalty_rate': 0.2, 'evidence_count': 4, 'days_late': 90, 'appeal_deadline_day': 60}
PROPOSE_DATA = {'proposal': '补税并处罚', 'defense_deadline_day': 15, 'hearing_request_deadline_day': 5}
REVIEW_DATA = {'outcome': 'accepted', 'review_note': '证据充分', 'decision_basis': '核对听证笔录与补充证据后维持'}

INSPECTOR = Actor('inspector-1', 'inspector')
REVIEWER = Actor('reviewer-1', 'reviewer')
REP = Actor('rep-1', 'taxpayer_rep')


class ProcedureDeskTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / 'test.db')
        self.service, self.desk = build_services(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def proposed_record(self, reference='TAX-26001'):
        record = self.service.create(INSPECTOR, reference, CREATE_DATA)
        record = self.service.act(INSPECTOR, record['id'], record['version'], 'investigate', {'plan': '核对账簿'})
        return self.service.act(INSPECTOR, record['id'], record['version'], 'propose', PROPOSE_DATA)

    def test_propose_writes_deadlines(self):
        record = self.proposed_record()
        self.assertEqual(record['payload']['defense_deadline_day'], 15)
        self.assertEqual(record['payload']['hearing_request_deadline_day'], 5)
        self.assertFalse(record['payload']['needs_recheck'])

    def test_statement_evidence_recheck_and_review(self):
        record = self.proposed_record()
        result = self.desk.submit(REP, record['id'], 'statement', {'submitted_day': 3, 'content': '情况说明'})
        self.assertEqual(result['procedure']['status'], 'accepted')

        result = self.desk.submit(REP, record['id'], 'evidence', {'submitted_day': 5, 'content': '新发票', 'assessed_tax': 700000.0})
        self.assertEqual(result['procedure']['status'], 'accepted')
        self.assertTrue(result['procedure']['details']['amounts_changed'])
        record = result['record']
        self.assertEqual(record['payload']['total_due'], 249000.0)
        self.assertTrue(record['payload']['needs_recheck'])

        with self.assertRaises(Conflict):
            self.service.act(REVIEWER, record['id'], record['version'], 'review', REVIEW_DATA)

        record = self.service.act(REVIEWER, record['id'], record['version'], 'recheck', {'recheck_note': '已核对新证据'})
        self.assertFalse(record['payload']['needs_recheck'])
        self.assertEqual(record['payload']['rechecked_total_due'], 249000.0)

        record = self.service.act(REVIEWER, record['id'], record['version'], 'review', REVIEW_DATA)
        self.assertEqual(record['state'], 'reviewed')
        self.assertEqual(record['payload']['decision_basis'], REVIEW_DATA['decision_basis'])

    def test_hearing_blocks_review_until_concluded(self):
        record = self.proposed_record()
        self.desk.submit(REP, record['id'], 'hearing_request', {'submitted_day': 4, 'content': '要求听证'})
        view = self.desk.desk(REVIEWER, record['id'])
        self.assertEqual(view['hearing']['status'], 'pending')
        self.assertFalse(view['procedure_complete'])
        self.assertIn('听证未办结，原决定不能确认', view['blocking_reasons'])

        with self.assertRaises(Conflict):
            self.service.act(REVIEWER, record['id'], record['version'], 'review', REVIEW_DATA)

        self.desk.submit(REVIEWER, record['id'], 'hearing_conclusion', {'submitted_day': 8, 'content': '听证结束，维持建议'})
        view = self.desk.desk(REVIEWER, record['id'])
        self.assertEqual(view['hearing']['status'], 'concluded')
        self.assertTrue(view['procedure_complete'])

        record = self.service.act(REVIEWER, record['id'], record['version'], 'review', REVIEW_DATA)
        self.assertEqual(record['state'], 'reviewed')

    def test_late_filings_recorded_but_not_accepted(self):
        record = self.proposed_record()
        result = self.desk.submit(REP, record['id'], 'statement', {'submitted_day': 20, 'content': '逾期陈述'})
        self.assertEqual(result['procedure']['status'], 'late_filed')
        self.assertIn('不予受理', result['procedure']['note'])

        result = self.desk.submit(REP, record['id'], 'evidence', {'submitted_day': 16, 'content': '逾期证据', 'assessed_tax': 100.0})
        self.assertEqual(result['procedure']['status'], 'late_filed')
        self.assertEqual(result['record']['payload']['total_due'], 323700.0)
        self.assertFalse(result['record']['payload'].get('needs_recheck', False))

        result = self.desk.submit(REP, record['id'], 'hearing_request', {'submitted_day': 6, 'content': '逾期听证'})
        self.assertEqual(result['procedure']['status'], 'late_filed')
        view = self.desk.desk(REVIEWER, record['id'])
        self.assertEqual(view['hearing']['status'], 'none')
        self.assertTrue(view['procedure_complete'])
        self.assertEqual(view['summary']['late_filed'], 3)

    def test_restart_keeps_procedures_visible(self):
        record = self.proposed_record()
        self.desk.submit(REP, record['id'], 'statement', {'submitted_day': 2, 'content': '陈述材料'})
        self.desk.submit(REP, record['id'], 'hearing_request', {'submitted_day': 3, 'content': '要求听证'})

        service2, desk2 = build_services(self.db_path)
        view = desk2.desk(REVIEWER, record['id'])
        self.assertEqual(len(view['procedures']), 2)
        self.assertEqual(view['hearing']['status'], 'pending')
        self.assertFalse(view['procedure_complete'])

    def test_permissions_and_guards(self):
        record = self.proposed_record()
        with self.assertRaises(PermissionDenied):
            self.desk.submit(INSPECTOR, record['id'], 'statement', {'submitted_day': 1, 'content': '越权'})
        with self.assertRaises(PermissionDenied):
            self.desk.submit(REP, record['id'], 'hearing_conclusion', {'submitted_day': 1, 'content': '越权'})
        with self.assertRaises(Conflict):
            self.desk.submit(REVIEWER, record['id'], 'hearing_conclusion', {'submitted_day': 1, 'content': '没有听证'})

        other = dict(CREATE_DATA)
        other['tax_period'] = '2025-Q3'
        fresh = self.service.create(INSPECTOR, 'TAX-26002', other)
        with self.assertRaises(Conflict):
            self.desk.submit(REP, fresh['id'], 'statement', {'submitted_day': 1, 'content': '建议未发出'})

    def test_propose_and_review_require_new_fields(self):
        record = self.service.create(INSPECTOR, 'TAX-26003', CREATE_DATA)
        record = self.service.act(INSPECTOR, record['id'], record['version'], 'investigate', {'plan': '核对账簿'})
        with self.assertRaises(ValidationError):
            self.service.act(INSPECTOR, record['id'], record['version'], 'propose', {'proposal': '缺期限'})
        record = self.service.act(INSPECTOR, record['id'], record['version'], 'propose', PROPOSE_DATA)
        with self.assertRaises(ValidationError):
            self.service.act(REVIEWER, record['id'], record['version'], 'review', {'outcome': 'accepted', 'review_note': '缺依据'})

    def test_recheck_without_changes_rejected(self):
        record = self.proposed_record()
        with self.assertRaises(Conflict):
            self.service.act(REVIEWER, record['id'], record['version'], 'recheck', {'recheck_note': '无变动'})


if __name__ == '__main__':
    unittest.main()
