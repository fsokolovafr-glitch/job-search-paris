import sys
import unittest
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from history_store import HistoryStore, job_key

class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = HistoryStore(Path(self.tmp.name) / 'state.db')
        self.now = datetime(2026, 9, 29, tzinfo=timezone.utc)
        self.job = dict(company='Société Générale', title='Demand Generation Manager', url='https://linkedin.com/jobs/view/1', status='LIVE', applied=False, posting={'title':'Demand Generation Manager'})
    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()
    def record(self, job, day=0, observed='LIVE'):
        self.store.record_run([job], {'observations': [] if observed is None else [{'url':job['url'], 'status':observed}]}, 'mock', self.now + timedelta(days=day))
    def test_keys(self):
        self.assertEqual(job_key('Société Générale',' Senior\u00a0 Manager ',self.job['url']),job_key('SOCIETE GENERALE','senior manager',self.job['url']+'?utm_source=x'))
        self.assertNotEqual(job_key('AB','C',self.job['url']),job_key('A','BC',self.job['url']))
    def test_bootstrap_once_and_preserve_application(self):
        self.store.bootstrap([dict(self.job, applied=True, application_status='rejected')], [], {'check_history':[]})
        self.store.bootstrap([self.job], [], {'check_history':[]})
        self.assertTrue(self.store.export_jobs()[0]['applied'])
        self.assertEqual(self.store.export_jobs()[0]['application_status'],'rejected')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM job_events').fetchone()[0],0)
    def test_skipped_unknown_and_unchanged_do_not_emit_update(self):
        self.record(self.job)
        first=self.store.db.execute('SELECT last_seen FROM jobs').fetchone()[0]
        self.record(self.job,1,None)
        self.record(self.job,2,'UNKNOWN')
        self.assertEqual(self.store.db.execute('SELECT last_seen FROM jobs').fetchone()[0],first)
        self.record(self.job,3)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM job_events').fetchone()[0],1)
    def test_title_change_keeps_identity(self):
        self.record(self.job)
        self.record(dict(self.job,title='Senior Demand Generation Manager',posting={'title':'Senior Demand Generation Manager'}),1)
        self.assertEqual(len(self.store.export_jobs()),1)
        self.assertEqual(self.store.db.execute('SELECT event_type FROM job_events ORDER BY id DESC').fetchone()[0],'JOB_UPDATED')
    def test_window_reopens_momentum_scores_and_future(self):
        self.record(self.job)
        self.record(dict(self.job,status='CLOSED'),1,'CLOSED')
        self.record(self.job,2)
        self.record(dict(self.job,status='CLOSED'),3,'CLOSED')
        self.record(self.job,4)
        self.record(dict(self.job,company='Other',url='https://linkedin.com/jobs/view/2'),100)
        s=self.store.hiring_intelligence(30,self.now+timedelta(days=5))
        self.assertEqual((s['new_jobs'],s['closed_jobs'],s['reopened_jobs'],s['net_growth']),(1,2,2,-1))
        self.assertIn(('Société Générale',1),s['momentum'])
        self.assertEqual(s['reopened_roles'][0][2],2)
        self.assertIn(('Société Générale',4),s['activity_scores'])
    def test_closed_employer_remains_in_score(self):
        self.record(self.job)
        self.record(dict(self.job,status='CLOSED'),1,'CLOSED')
        s=self.store.hiring_intelligence(30,self.now+timedelta(days=2))
        self.assertEqual(s['top_hiring_companies'],[])
        self.assertEqual(s['activity_scores'],[('Société Générale',1)])
    def test_failed_run_rolls_back(self):
        with self.assertRaises(KeyError):
            self.store.record_run([self.job,{}],{},'mock',self.now)
        self.assertEqual(self.store.export_jobs(),[])
