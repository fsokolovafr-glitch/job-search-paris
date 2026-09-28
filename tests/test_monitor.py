import copy
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import monitor as m


class Tests(unittest.TestCase):
    def setUp(self):
        self.cfg=m.read_json(m.ROOT/'data/config.json')
        self.f=m.read_json(m.ROOT/'tests/fixtures/demo.json')
        self.now=datetime(2026,9,28,10,tzinfo=timezone.utc)
        self.url=self.f['search']['hits'][0]['url']
        self.page=self.f['pages'][self.url]

    def run_fixture(self,jobs=None,companies=None):
        self.provider=m.MockProvider(self.f)
        return m.monitor(self.f['jobs'] if jobs is None else jobs,
                         self.f['companies'] if companies is None else companies,
                         self.cfg,self.provider,self.now)

    def test_new_closed_unknown(self):
        jobs,r=self.run_fixture()
        self.assertEqual((r['new_jobs_found'],r['jobs_closed'],r['live_jobs']),(1,1,1))
        self.assertEqual(jobs[1]['status'],'LIVE')
        self.assertEqual(jobs[1]['last_check_status'],'UNKNOWN')
        self.assertTrue(r['new'][0]['priority'])

    def test_generic_search_title_is_read(self):
        _,r=self.run_fixture()
        self.assertIn(self.url,self.provider.fetched)
        self.assertEqual(r['new_jobs_found'],1)

    def test_external_search_and_saved_urls_never_fetched(self):
        self.f['jobs'].append({'company':'LumApps','title':'Field Marketing Manager',
                              'url':'https://job.lumapps.com/jobs/123','status':'LIVE','applied':False})
        _,r=self.run_fixture()
        self.assertTrue(all(m.platform_for(u) for u in self.provider.fetched))
        self.assertEqual(len(r['excluded']),2)

    def test_allowed_domains_and_paths(self):
        good=['https://fr.linkedin.com/jobs/view/123','https://fr.indeed.com/viewjob?jk=1',
              'https://www.indeed.fr/viewjob?jk=1',self.url]
        bad=['https://linkedin.com.evil.test/jobs/view/123','https://evil-linkedin.com/jobs/view/123',
             'https://www.linkedin.com/in/person','http://www.linkedin.com/jobs/view/123',
             'https://user:pass@fr.indeed.com/viewjob?jk=1','https://fr.indeed.com:8080/viewjob?jk=1',
             'https://job.lumapps.com/jobs/123','https://www.linkedin.com/authwall','file:///etc/passwd']
        for u in good: self.assertIsNotNone(m.platform_for(u),u)
        for u in bad: self.assertIsNone(m.platform_for(u),u)

    def test_only_one_global_search_for_44_companies(self):
        companies=[{'company':'Company '+str(n)} for n in range(44)]
        _,r=self.run_fixture(companies=companies)
        self.assertEqual(self.provider.search_calls,1)
        self.assertEqual(r['query_count'],18)

    def test_empty_company_list_still_discovers_jobs(self):
        _,r=self.run_fixture(companies=[])
        self.assertEqual(r['new_jobs_found'],1)
        self.assertFalse(r['new'][0]['priority'])

    def test_repeat_no_duplicate_events(self):
        jobs,_=self.run_fixture()
        again,r=self.run_fixture(jobs=jobs)
        self.assertEqual((r['new_jobs_found'],r['jobs_closed']),(0,0))
        self.assertEqual(len(again),3)

    def test_tracking_duplicate(self):
        self.f['search']['hits'].append({'url':self.url+'?utm_source=test'})
        _,r=self.run_fixture()
        self.assertEqual(r['new_jobs_found'],1)
        self.assertEqual(self.provider.fetched.count(self.url),1)

    def test_request_preserves_trailing_slash(self):
        self.f['search']['hits'][0]['url']=self.url+'/'
        self.f['pages'][self.url+'/']=self.page
        _,r=self.run_fixture()
        self.assertIn(self.url+'/',self.provider.fetched)
        self.assertEqual(r['new_jobs_found'],1)
        self.assertNotEqual(m.canonical(self.url),m.canonical(self.url+'/'))

    def test_international_and_early_stage_not_internships(self):
        for t in ['International Field Marketing Manager','Field Marketing Manager — early-stage startup']:
            self.assertTrue(m.title_matches(t,self.cfg),t)
        for t in ['Field Marketing Intern','Stage Field Marketing','Field Marketing en alternance']:
            self.assertFalse(m.title_matches(t,self.cfg),t)

    def test_timestamp_expiration(self):
        page=dict(self.page,body=self.page['body'].replace('2099-12-31','2026-09-28T08:00:00Z'))
        self.assertEqual(m.classify(page,self.cfg,self.now)[0],'CLOSED')
        page=dict(self.page,body=self.page['body'].replace('2099-12-31','2026-09-28T13:00:00+02:00'))
        self.assertEqual(m.classify(page,self.cfg,self.now)[0],'LIVE')

    def test_date_only_and_naive_expiration(self):
        page=dict(self.page,body=self.page['body'].replace('2099-12-31','2026-09-28'))
        self.assertEqual(m.classify(page,self.cfg,self.now)[0],'LIVE')
        page=dict(self.page,body=self.page['body'].replace('2099-12-31','2026-09-28T09:00:00'))
        self.assertEqual(m.classify(page,self.cfg,self.now)[0],'UNKNOWN')

    def test_wrong_country_unknown(self):
        page=dict(self.page,body=self.page['body'].replace('FR','US'))
        self.assertEqual(m.classify(page,self.cfg,self.now)[0],'UNKNOWN')

    def test_ambiguous_or_generic_page_unknown(self):
        for body in ['<h1>Careers</h1>',self.page['body']*2]:
            self.assertEqual(m.classify({'status':200,'body':body},self.cfg,self.now)[0],'UNKNOWN')

    def test_applied_and_rejected_roles_skipped(self):
        self.f['jobs'][0]['applied']=True
        self.f['jobs'][1]['application_status']='rejected'
        _,r=self.run_fixture()
        self.assertEqual(r['pages_checked'],1)

    def test_rejected_employer_is_not_added(self):
        self.f['companies'][0]['contact_status']='rejected'
        _,r=self.run_fixture()
        self.assertEqual(r['new_jobs_found'],0)
        self.assertEqual(r['pages_checked'],1)

    def test_budget_respected(self):
        self.cfg['max_pages_per_run']=1
        _,r=self.run_fixture()
        self.assertEqual(len(self.provider.fetched),1)
        self.assertTrue(any(x['reason']=='page_budget_exceeded' for x in r['unknown']))

    def test_reopened(self):
        self.f['jobs'].append({'company':'Demo Enterprise','title':'Demand Generation Manager','url':self.url,'status':'CLOSED','applied':False})
        _,r=self.run_fixture()
        self.assertEqual(len(r['reopened']),1)
        self.assertEqual(r['new_jobs_found'],0)

    def live(self):
        with patch.dict('os.environ',{'BRAVE_SEARCH_API_KEY':'MOCK_ONLY'}):
            return m.LiveProvider(self.cfg)

    def test_live_provider_rejects_external_before_request(self):
        p=self.live()
        with patch.object(p.opener,'open') as request:
            self.assertEqual(p.fetch('https://employer.example/jobs/1')['status'],0)
            request.assert_not_called()

    def test_external_redirect_not_followed(self):
        p=self.live()
        error=HTTPError(self.url,302,'Redirect',{'Location':'https://employer.example/jobs/1'},None)
        with patch.object(p.opener,'open',side_effect=error) as request:
            self.assertEqual(p.fetch(self.url)['error'],'blocked_outside_allowed_job_pages')
            self.assertEqual(request.call_count,1)

    def test_native_redirect_handler_disabled(self):
        self.assertIsNone(m.NoRedirect().redirect_request(None,None,302,'',{},'https://employer.example'))

    def test_internal_redirect_follows_original_url(self):
        p=self.live()
        target=self.url+'/'
        error=HTTPError(self.url,302,'Redirect',{'Location':target},None)
        response=io.BytesIO(self.page['body'].encode())
        response.status=200
        with patch.object(p.opener,'open',side_effect=[error,response]) as request:
            self.assertEqual(p.fetch(self.url)['status'],200)
            self.assertEqual(request.call_args_list[1].args[0].full_url,target)

    def test_partial_search_retains_first_success(self):
        p=self.live()
        first=io.BytesIO(json.dumps({'web':{'results':[{'url':self.url}]}}).encode())
        count=[0]
        def server(*args,**kwargs):
            count[0]+=1
            if count[0]==1: return first
            raise HTTPError('https://api.search.brave.com',429,'Rate limited',{},None)
        with patch.object(p.opener,'open',side_effect=server),patch('monitor.time.sleep'):
            r=p.search()
        self.assertEqual(len(r['hits']),1)
        self.assertEqual(r['query_count'],18)
        self.assertEqual(len(r['errors']),17)
        self.assertTrue(all('site:' in q['query'] for q in r['queries']))

    def test_blocked_page_never_closed(self):
        for status in [0,403,429,500]:
            self.assertEqual(m.classify({'status':status},self.cfg,self.now)[0],'UNKNOWN')

    def test_save_and_validate(self):
        jobs,r=self.run_fixture()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            m.save_run(root,jobs,r,'mock',{'check_history':[]},self.f['companies'])
            history=m.read_json(root/'data/job-tracking.json')['check_history']
            self.assertIn('MOCK',(root/history[0]['report_file']).read_text())
            m.validate(m.read_json(root/'data/jobs.json'))
            self.assertFalse((root/'data/companies-shortlist.json').exists())

    def test_missing_key_and_invalid_job(self):
        with patch.dict('os.environ',{},clear=True):
            with self.assertRaises(ValueError): m.LiveProvider(self.cfg)
        with self.assertRaises(ValueError): m.validate(self.f['jobs']*2)


if __name__=='__main__': unittest.main()
