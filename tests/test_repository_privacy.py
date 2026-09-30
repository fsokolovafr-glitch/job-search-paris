import json
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RepositoryPrivacyTests(unittest.TestCase):
    def test_public_outputs_contain_no_application_history_fields(self):
        forbidden = ('application_status', 'applied_date', 'interview_date',
                     'submission_error', 'contact_status', 'cv_variant')
        for folder in ('data', 'dashboard', 'reports', 'sources'):
            for path in (ROOT / folder).rglob('*'):
                if not path.is_file() or path.suffix == '.db':
                    continue
                text = path.read_text(encoding='utf-8')
                for marker in forbidden:
                    self.assertNotIn(marker, text, '{} leaked in {}'.format(marker, path))

    def test_public_sqlite_has_no_application_table_or_private_fields(self):
        db = sqlite3.connect(ROOT / 'data/job-search.db')
        try:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn('applications', tables)
            payloads = '\n'.join(row[0] for table in ('companies', 'jobs')
                                 for row in db.execute('SELECT payload FROM ' + table))
            for marker in ('application_status', 'applied_date', 'contact_status', 'cv_variant'):
                self.assertNotIn(marker, payloads)
        finally:
            db.close()

    def test_workflow_cannot_accept_or_upload_private_application_data(self):
        workflow = (ROOT / '.github/workflows/monitor-jobs.yml').read_text(encoding='utf-8')
        for marker in ('update_application', 'application_status', 'applied_date', 'APPLICATION_NOTE'):
            self.assertNotIn(marker, workflow)
        self.assertNotIn('private/', workflow)

    def test_public_snapshot_declares_empty_applications(self):
        snapshot = json.loads((ROOT / 'sources/artifact-2026-09-30.json').read_text(encoding='utf-8'))
        self.assertEqual(snapshot['applications'], [])


if __name__ == '__main__':
    unittest.main()
