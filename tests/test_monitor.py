import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from monitor import (ROOT, MockProvider, LiveProvider, canonical, classify,
                     job_postings, monitor, read_json, save_run, validate)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.config = read_json(ROOT / 'data/config.json')
        self.fixture = read_json(ROOT / 'tests/fixtures/demo.json')
        self.company = self.fixture['companies'][0]
        self.now = datetime(2026, 9, 28, 10, tzinfo=timezone.utc)
        self.page = self.fixture['pages']['https://example.com/jobs/new']

    def run_fixture(self, companies=None):
        return monitor(companies or self.fixture['companies'], self.config, MockProvider(self.fixture), self.now)

    def test_new_closed_unknown(self):
        updated, result = self.run_fixture()
        self.assertEqual((result['new_jobs_found'], result['jobs_closed'], result['live_jobs']), (1, 1, 1))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(updated[0]['open_roles'][1]['status'], 'LIVE')
        self.assertEqual(updated[0]['open_roles'][1]['last_check_status'], 'UNKNOWN')
        self.assertEqual(self.company['open_roles'][0]['status'], 'LIVE')

    def test_repeat_no_duplicate_events(self):
        updated, _ = self.run_fixture()
        again, result = self.run_fixture(updated)
        self.assertEqual(result['new_jobs_found'], 0)
        self.assertEqual(result['jobs_closed'], 0)
        self.assertEqual(len(again[0]['open_roles']), 3)

    def test_tracking_parameters_deduplicated(self):
        self.fixture['search']['Demo Enterprise'].append({'title': 'Demand Generation Manager', 'url': 'https://example.com/jobs/new?utm_source=test'})
        _, result = self.run_fixture()
        self.assertEqual(result['new_jobs_found'], 1)
        self.assertNotEqual(canonical('https://example.com/jobs?id=1'), canonical('https://example.com/jobs?id=2'))

    def test_rejected_company_is_skipped(self):
        self.company['contact_status'] = 'rejected'
        _, result = self.run_fixture()
        self.assertEqual(result['companies_checked'], 0)
        self.assertEqual(result['observations'], [])

    def test_applied_and_rejected_roles_are_skipped(self):
        self.company['open_roles'][0]['applied'] = True
        self.company['open_roles'][1]['application_status'] = 'rejected'
        _, result = self.run_fixture()
        self.assertEqual(len(result['observations']), 1)

    def test_200_generic_page_is_unknown(self):
        self.assertEqual(classify({'status': 200, 'body': '<h1>Careers</h1>'}, self.company, self.config, self.now.date())[0], 'UNKNOWN')

    def test_http_errors(self):
        for code in [403, 429, 500, 0]:
            self.assertEqual(classify({'status': code}, self.company, self.config, self.now.date())[0], 'UNKNOWN')
        for code in [404, 410]:
            self.assertEqual(classify({'status': code}, self.company, self.config, self.now.date())[0], 'CLOSED')

    def test_redirect_requires_review(self):
        self.assertEqual(classify(dict(self.page, redirected=True), self.company, self.config, self.now.date())[0], 'UNKNOWN')

    def test_employer_country_role_filters(self):
        for old, new in [('Demo Enterprise', 'Wrong Employer'), ('FR', 'US'), ('Demand Generation Manager', 'Software Engineer')]:
            page = dict(self.page, body=self.page['body'].replace(old, new))
            self.assertEqual(classify(page, self.company, self.config, self.now.date())[0], 'UNKNOWN')

    def test_expected_title_identity(self):
        self.assertEqual(classify(self.page, self.company, self.config, self.now.date(), 'Field Marketing Manager')[0], 'UNKNOWN')

    def test_expired_role(self):
        page = dict(self.page, body=self.page['body'].replace('2099-12-31', '2026-09-01'))
        self.assertEqual(classify(page, self.company, self.config, self.now.date())[0], 'CLOSED')

    def test_search_failure_preserved(self):
        self.fixture['search']['Demo Enterprise'] = {'error': 'search_failed:HTTPError'}
        _, result = self.run_fixture()
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(any(item['title'] == 'WebSearch' for item in result['unknown']))

    def test_found_small_company_does_not_search(self):
        self.company['employee_count'] = 10
        self.company['monitoring_status'] = 'found_not_applied'
        _, result = self.run_fixture()
        self.assertEqual(result['new_jobs_found'], 0)
        self.assertEqual(result['large'], [])

    def test_reopened_role(self):
        self.company['open_roles'].append({'title': 'Demand Generation Manager', 'url': 'https://example.com/jobs/new', 'applied': False, 'status': 'CLOSED'})
        _, result = self.run_fixture()
        self.assertEqual(len(result['reopened']), 1)
        self.assertEqual(result['new_jobs_found'], 0)

    def test_save_history_and_reports(self):
        updated, result = self.run_fixture()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            save_run(path, updated, result, 'mock', {'check_history': []})
            history = read_json(path / 'data/job-tracking.json')
            report = path / history['check_history'][0]['report_file']
            self.assertIn('MOCK', report.read_text())
            self.assertIn('https://example.com/jobs/new', report.read_text())
            self.assertEqual(len(history['check_history']), 1)

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            validate([self.company, copy.deepcopy(self.company)])
        with self.assertRaises(ValueError):
            canonical('file:///etc/passwd')

    def test_missing_key(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaises(ValueError):
                LiveProvider(self.config)

    def test_jsonld_graph_and_malformed(self):
        self.assertEqual(job_postings('<script type="application/ld+json">broken</script>'), [])
        graph = {'@graph': [{'@type': 'JobPosting', 'title': 'Example'}]}
        self.assertEqual(len(job_postings('<script type="application/ld+json">' + json.dumps(graph) + '</script>')), 1)


if __name__ == '__main__':
    unittest.main()
