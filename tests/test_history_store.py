import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from history_store import HistoryStore, job_key, norm


class HistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 5, tzinfo=timezone.utc)
        self.company = {'company': 'Société Générale', 'aliases': ['Societe Generale'], 'city': 'Paris',
                        'priority': 'High', 'monitoring_status': 'checked'}
        self.job = {'company': 'Societe Generale', 'title': 'Senior  Demand Gen Manager',
                    'url': 'https://www.linkedin.com/jobs/view/123', 'found_date': '2026-10-05',
                    'status': 'LIVE', 'applied': False}

    def test_nfkc_normalization_and_stable_key(self):
        self.assertEqual(norm('Senior\u00a0 Demand Gen Manager'), 'senior demand gen manager')
        self.assertEqual(job_key('Société Générale', 'Senior Demand Gen Manager', self.job['url']),
                         job_key('Société Générale', 'Senior\u00a0 Demand Gen Manager', self.job['url']))

    def test_lifecycle_close_reopen_and_no_delete(self):
        with tempfile.TemporaryDirectory() as d:
            with HistoryStore(Path(d) / 'job-search.db') as store:
                store.record_run([self.job], {'companies': [self.company], 'live_jobs': 1, 'new_jobs_found': 1, 'jobs_closed': 0, 'unknown': []}, 'mock', self.now)
                closed = dict(self.job, status='CLOSED', last_check_status='CLOSED')
                store.record_run([closed], {'companies': [self.company], 'live_jobs': 0, 'new_jobs_found': 0, 'jobs_closed': 1, 'unknown': []}, 'mock', self.now.replace(day=18))
                reopened = dict(self.job, status='LIVE', last_check_status='LIVE')
                store.record_run([reopened], {'companies': [self.company], 'live_jobs': 1, 'new_jobs_found': 0, 'jobs_closed': 0, 'unknown': []}, 'mock', self.now.replace(month=11, day=1))
                events = [r['event_type'] for r in store.db.execute('SELECT event_type FROM job_events ORDER BY id')]
                self.assertEqual(events, ['JOB_DISCOVERED', 'JOB_CLOSED', 'JOB_REOPENED'])
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 1)

    def test_analytics(self):
        with tempfile.TemporaryDirectory() as d:
            with HistoryStore(Path(d) / 'job-search.db') as store:
                store.record_run([self.job], {'companies': [self.company], 'live_jobs': 1, 'new_jobs_found': 1, 'jobs_closed': 0, 'unknown': []}, 'mock', self.now)
                text = store.analytics_text()
                self.assertIn('Companies tracked: 1', text)
                self.assertIn('Active jobs: 1', text)
                self.assertIn('Societe Generale', text)

    def test_hiring_intelligence_trend_rankings_and_score(self):
        with tempfile.TemporaryDirectory() as d:
            with HistoryStore(Path(d) / 'job-search.db') as store:
                base = {'companies': [self.company], 'live_jobs': 1, 'new_jobs_found': 1, 'jobs_closed': 0, 'unknown': []}
                store.record_run([self.job], base, 'mock', self.now)
                closed = dict(self.job, status='CLOSED', last_check_status='CLOSED')
                store.record_run([closed], {'companies': [self.company], 'live_jobs': 0, 'new_jobs_found': 0, 'jobs_closed': 1, 'unknown': []}, 'mock', self.now.replace(day=8))
                store.record_run([dict(self.job, status='LIVE', last_check_status='LIVE')], base, 'mock', self.now.replace(day=9))
                intelligence = store.hiring_intelligence(30, self.now.replace(day=10))
                self.assertEqual(intelligence['net_growth'], 0)
                self.assertEqual(intelligence['reopened_roles'][0][2], 1)
                self.assertEqual(intelligence['top_hiring_companies'][0][1], 1)
                self.assertGreaterEqual(intelligence['activity_scores'][0][1], 3)


if __name__ == '__main__':
    unittest.main()
