import copy
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from history_store import HistoryStore
from pipeline import import_snapshot, export_all, writer_lock
from monitor import LiveProvider, allowed_job_page
from private_applications import find_record, read_tracker, upsert
from sanitize_public_snapshot import sanitize


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = HistoryStore(self.root / 'data/job-search.db')
        self.snapshot = json.loads((ROOT / 'sources/artifact-2026-09-30.json').read_text())

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_import_counts_provenance_and_repeat(self):
        self.assertTrue(import_snapshot(self.store, self.snapshot))
        self.assertFalse(import_snapshot(self.store, self.snapshot))
        summary = export_all(self.store, self.root)
        self.assertEqual((summary['companies_total'], summary['shortlist_total'], summary['backlog_total']), (153, 78, 75))
        self.assertEqual((summary['companies_france_explicit'], summary['companies_remote_paris'], summary['companies_other']), (151, 1, 1))
        self.assertEqual(summary['vacancy_records'], 38)
        self.assertEqual(summary['private_application_history'], 'not_published')
        self.assertFalse((self.root / 'data/applications.json').exists())
        self.assertFalse((self.root / 'data/applications-log.csv').exists())
        before = (self.root / 'dashboard/index.html').read_bytes()
        export_all(self.store, self.root)
        self.assertEqual(before, (self.root / 'dashboard/index.html').read_bytes())

    def test_public_snapshot_rejects_application_history(self):
        broken = copy.deepcopy(self.snapshot)
        broken['applications'] = [{'company': 'Secret', 'application_status': 'applied'}]
        with self.assertRaisesRegex(ValueError, 'must not contain application history'):
            import_snapshot(self.store, broken)
        self.assertEqual(self.store.export_companies(), [])

    def test_invalid_snapshot_transaction_preserves_state(self):
        broken = copy.deepcopy(self.snapshot)
        broken['jobs'][-1]['url'] = 'file:///etc/passwd'
        with self.assertRaises(ValueError):
            import_snapshot(self.store, broken)
        self.assertEqual(self.store.export_companies(), [])
        self.assertEqual(self.store.export_jobs(), [])

    def test_newer_live_status_survives_snapshot(self):
        import_snapshot(self.store, self.snapshot)
        job = dict(self.store.export_jobs()[0], status='CLOSED', evidence={'checked_at':'2099-10-01T00:00:00+00:00'})
        self.store.record_run([job], {'observations':[{'url':job['url'], 'status':'CLOSED'}]}, 'live', datetime(2099,10,1,tzinfo=timezone.utc))
        changed = copy.deepcopy(self.snapshot)
        changed['snapshot_at'] = '2026-10-01T00:00:00Z'
        import_snapshot(self.store, changed)
        self.assertEqual(self.store.export_jobs()[0]['status'], 'CLOSED')

    def test_html_escapes_untrusted_public_content(self):
        bad = copy.deepcopy(self.snapshot)
        bad['companies'][0]['company'] = '<script>alert(1)</script>'
        bad['jobs'][0]['company'] = '<script>alert(1)</script>'
        import_snapshot(self.store, bad)
        export_all(self.store, self.root)
        page = (self.root / 'dashboard/index.html').read_text()
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertNotIn('Журнал откликов', page)

    def test_lock_blocks_concurrent_writer(self):
        with writer_lock(self.root):
            with self.assertRaises(FileExistsError):
                with writer_lock(self.root):
                    self.fail('second writer entered')
        self.assertFalse((self.root / '.monitor.lock').exists())

    def test_analytics_is_byte_for_byte_read_only(self):
        import_snapshot(self.store, self.snapshot)
        self.store.close()
        db = self.root / 'data/job-search.db'
        before = db.read_bytes()
        subprocess.run([sys.executable, str(ROOT / 'scripts/job-analytics.py'), '--db', str(db)],
                       check=True, capture_output=True, text=True)
        self.assertEqual(before, db.read_bytes())
        self.store = HistoryStore(db)

    def test_private_tracker_missing_is_unknown_and_upsert_deduplicates(self):
        path = self.root / 'private/applications.json'
        self.assertIsNone(read_tracker(path))
        url = 'https://example.com/jobs/1?utm_source=test'
        upsert(path, 'Example', 'Role', url, 'applied', '2026-10-02', None)
        upsert(path, 'Example', 'Role', 'https://example.com/jobs/1', 'interview', '2026-10-03', 'Confirmed')
        tracker = read_tracker(path)
        self.assertEqual(len(tracker['records']), 1)
        self.assertEqual(find_record(tracker, url=url)['status'], 'interview')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_sanitizer_removes_private_fields_recursively(self):
        source = {'snapshot_at':'2026-10-02T00:00:00Z',
                  'companies':[{'company':'A','contact_status':'found','notes':'sensitive-note',
                                'unlinked_roles':[{'title':'Role','status':'LIVE','notes':'sensitive-note'}]}],
                  'jobs':[{'company':'A','title':'Role','url':'https://example.com/jobs/1','status':'LIVE',
                           'application_status':'rejected','applied_date':'2026-01-01','notes':'sensitive-note'}],
                  'applications':[{'company':'A','application_status':'rejected'}]}
        public = sanitize(source)
        self.assertEqual(public['applications'], [])
        text = json.dumps(public)
        for secret in ('contact_status', 'application_status', 'applied_date', 'sensitive-note'):
            self.assertNotIn(secret, text)

    def test_no_key_still_allows_known_url_monitoring(self):
        with patch.dict('os.environ', {}, clear=True):
            provider = LiveProvider({}, 'auto')
            self.assertEqual(provider.search()['discovery_status'], 'missing_api_key')
            self.assertEqual(provider.search()['query_count'], 0)

    def test_employer_scope_is_exact_and_opt_in(self):
        cfg = {'check_reviewed_employer_sites':True}
        self.assertTrue(allowed_job_page('https://job.lumapps.com/jobs/123', cfg))
        for url in ('https://job.lumapps.com.evil.test/jobs/123', 'http://job.lumapps.com/jobs/123',
                    'https://127.0.0.1/jobs/123', 'https://job.lumapps.com/account', 'file:///etc/passwd'):
            self.assertFalse(allowed_job_page(url, cfg))
        self.assertFalse(allowed_job_page('https://job.lumapps.com/jobs/123', {}))


if __name__ == '__main__':
    unittest.main()
