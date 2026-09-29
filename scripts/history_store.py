"""Authoritative SQLite state, lifecycle history and hiring analytics."""
import hashlib
import json
import sqlite3
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY, company_name TEXT UNIQUE NOT NULL, aliases TEXT NOT NULL,
  city TEXT, priority TEXT, monitoring_status TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY, job_key TEXT UNIQUE NOT NULL, company_name TEXT NOT NULL,
  title TEXT NOT NULL, url TEXT NOT NULL, first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL, current_status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_events (
  id INTEGER PRIMARY KEY, job_key TEXT NOT NULL, event_type TEXT NOT NULL,
  event_timestamp TEXT NOT NULL, details TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS monitoring_runs (
  id INTEGER PRIMARY KEY, run_timestamp TEXT NOT NULL, mode TEXT NOT NULL,
  jobs_found INTEGER NOT NULL, jobs_new INTEGER NOT NULL, jobs_closed INTEGER NOT NULL,
  jobs_unknown INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_events_key ON job_events(job_key);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(current_status);
"""


def norm(value):
    value = unicodedata.normalize('NFKC', str(value))
    value = ''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.combining(c))
    return ' '.join(value.casefold().split())


def canonical(url):
    p = urlsplit(str(url).strip())
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('Invalid HTTP URL')
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('gclid', 'fbclid')]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), unicodedata.normalize('NFKC', p.path),
                       urlencode(sorted(query)), ''))


def job_key(company, title, url):
    # JSON tuple prevents ambiguous concatenation; URL paths remain case-sensitive.
    value = json.dumps([norm(company), norm(title), canonical(url)], ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()


class HistoryStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        with self.db:
            for table in ('companies', 'jobs', 'monitoring_runs'):
                columns = {r['name'] for r in self.db.execute('PRAGMA table_info(' + table + ')')}
                if 'payload' not in columns:
                    self.db.execute("ALTER TABLE " + table + " ADD COLUMN payload TEXT NOT NULL DEFAULT '{}'")
            self.db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def initialized(self):
        return self.db.execute("SELECT 1 FROM metadata WHERE key='bootstrap'").fetchone() is not None

    def _event(self, key, kind, at, details):
        self.db.execute('INSERT INTO job_events(job_key,event_type,event_timestamp,details) VALUES(?,?,?,?)',
                        (key, kind, at, json.dumps(details, ensure_ascii=False)))

    def upsert_companies(self, companies, timestamp):
        for item in companies:
            match = next((r for r in self.db.execute('SELECT * FROM companies')
                          if norm(r['company_name']) == norm(item['company'])), None)
            name = match['company_name'] if match else item['company']
            self.db.execute("""INSERT INTO companies(company_name,aliases,city,priority,monitoring_status,created_at,payload)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(company_name) DO UPDATE SET aliases=excluded.aliases,
                city=excluded.city,priority=excluded.priority,monitoring_status=excluded.monitoring_status,payload=excluded.payload""",
                (name, json.dumps(item.get('aliases', [])), item.get('city'), item.get('priority'),
                 item.get('monitoring_status'), timestamp, json.dumps(dict(item, company=name), ensure_ascii=False)))

    def bootstrap(self, jobs, companies, tracker):
        """One-time legacy import; existing SQLite statuses and event keys are preserved."""
        if self.initialized():
            return
        with self.db:
            self.upsert_companies(companies, datetime.now(timezone.utc).isoformat())
            for item in jobs:
                self._save_job(item, {}, item.get('last_checked') or item.get('found_date') or '', imported=True)
            self.db.execute("INSERT INTO metadata VALUES('legacy_tracker',?)", (json.dumps(tracker),))
            self.db.execute("INSERT INTO metadata VALUES('bootstrap','1')")

    def _save_job(self, item, observation, at, imported=False):
        current = next((r for r in self.db.execute('SELECT * FROM jobs')
                        if canonical(r['url']) == canonical(item['url'])), None)
        key = current['job_key'] if current else job_key(item['company'], item['title'], item['url'])
        if current is None:
            status = item.get('status', 'UNKNOWN')
            seen = item.get('last_checked', '') if imported else (at if observation.get('status') == 'LIVE' else '')
            self.db.execute('INSERT INTO jobs(job_key,company_name,title,url,first_seen,last_seen,current_status,payload) VALUES(?,?,?,?,?,?,?,?)',
                (key, item['company'], item['title'], item['url'], item.get('found_date', at), seen, status, json.dumps(item, ensure_ascii=False)))
            if not imported:
                self._event(key, 'JOB_DISCOVERED', at, {'status': status, 'url': item['url']})
            return
        previous = json.loads(current['payload'])
        if imported:
            # Enrich legacy rows without changing their confirmed status/history.
            payload = dict(item, **previous)
            self.db.execute('UPDATE jobs SET payload=? WHERE job_key=?', (json.dumps(payload), key))
            return
        if not observation:
            return
        observed = observation['status']
        status = current['current_status'] if observed == 'UNKNOWN' else observed
        payload = {**previous, **item, 'status': status}
        seen = at if observed == 'LIVE' else current['last_seen']
        self.db.execute('UPDATE jobs SET last_seen=?,current_status=?,title=?,company_name=?,url=?,payload=? WHERE job_key=?',
                        (seen, status, item['title'], item['company'], item['url'], json.dumps(payload, ensure_ascii=False), key))
        old = current['current_status']
        if old != status:
            kind = 'JOB_REOPENED' if old == 'CLOSED' and status == 'LIVE' else 'JOB_CLOSED' if status == 'CLOSED' else 'JOB_STATUS_CHANGED'
            self._event(key, kind, at, {'from': old, 'to': status})
        elif observed == 'LIVE' and previous.get('posting') and previous.get('posting') != item.get('posting'):
            self._event(key, 'JOB_UPDATED', at, {'reason': 'structured_job_content_changed'})

    def record_run(self, jobs, result, mode, timestamp):
        at = timestamp.astimezone(timezone.utc).isoformat()
        observations = {canonical(o['url']): o for o in result.get('observations', [])}
        with self.db:
            self.upsert_companies(result.get('companies', []), at)
            for item in jobs:
                observation = observations.get(canonical(item['url']), {})
                # Compatibility for direct callers supplying explicit observations.
                if 'observations' not in result:
                    observation = {'status': item.get('last_check_status', item.get('status', 'UNKNOWN'))}
                self._save_job(item, observation, at)
                if not any(norm(r['company_name']) == norm(item['company']) for r in self.db.execute('SELECT company_name FROM companies')):
                    self.upsert_companies([{'company': item['company']}], at)
            self.db.execute('INSERT INTO monitoring_runs(run_timestamp,mode,jobs_found,jobs_new,jobs_closed,jobs_unknown,payload) VALUES(?,?,?,?,?,?,?)',
                (at, mode, result.get('live_jobs', 0), result.get('new_jobs_found', 0), result.get('jobs_closed', 0), len(result.get('unknown', [])),
                 json.dumps(dict(result, mode=mode), ensure_ascii=False)))

    def export_jobs(self):
        return [dict(json.loads(r['payload']), company=r['company_name'], title=r['title'], url=r['url'],
                     status=r['current_status'], found_date=r['first_seen'][:10],
                     applied=json.loads(r['payload']).get('applied', False)) for r in self.db.execute('SELECT * FROM jobs ORDER BY id')]

    def export_companies(self):
        return [dict(json.loads(r['payload']), company=r['company_name']) for r in self.db.execute('SELECT * FROM companies ORDER BY id')]

    def export_tracker(self):
        legacy = self.db.execute("SELECT value FROM metadata WHERE key='legacy_tracker'").fetchone()
        result = json.loads(legacy[0]) if legacy else {'check_history': []}
        for row in self.db.execute('SELECT * FROM monitoring_runs ORDER BY id'):
            item = json.loads(row['payload'])
            if item:
                result['check_history'].append(item)
            else:
                result['check_history'].append(dict(row))
        return result

    def hiring_intelligence(self, days=30, now=None):
        if days < 1:
            raise ValueError('days must be positive')
        now = now or datetime.now(timezone.utc)
        since = now - timedelta(days=days)
        jobs = {r['job_key']: r for r in self.db.execute('SELECT * FROM jobs')}
        active, names, historical, repeats, momentum, roles, counts = Counter(), {}, Counter(), Counter(), Counter(), Counter(), Counter()
        for row in jobs.values():
            co = norm(row['company_name']); names.setdefault(co, row['company_name'])
            active[co] += row['current_status'] == 'LIVE'
        for e in self.db.execute('SELECT * FROM job_events'):
            if e['job_key'] not in jobs:
                continue
            t = datetime.fromisoformat(e['event_timestamp'].replace('Z', '+00:00'))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if t > now:
                continue
            job = jobs[e['job_key']]; co = norm(job['company_name']); kind = e['event_type']
            historical[co] += kind == 'JOB_DISCOVERED'
            repeats[co] += kind == 'JOB_REOPENED'
            if t < since:
                continue
            counts[kind] += 1
            if kind in ('JOB_DISCOVERED', 'JOB_REOPENED', 'JOB_CLOSED'):
                momentum[co] += -1 if kind == 'JOB_CLOSED' else 1
            if kind == 'JOB_REOPENED':
                roles[(co, e['job_key'], job['title'])] += 1
        def ranking(counter):
            return [(names[k], v) for k, v in sorted(counter.items(), key=lambda x: (-x[1], names[x[0]]))][:10]
        scores = {co: active[co] + repeats[co] + historical[co] for co in names}
        return {'days': days, 'new_jobs': counts['JOB_DISCOVERED'], 'closed_jobs': counts['JOB_CLOSED'],
                'reopened_jobs': counts['JOB_REOPENED'], 'net_growth': counts['JOB_DISCOVERED'] - counts['JOB_CLOSED'],
                'top_hiring_companies': ranking({k:v for k,v in active.items() if v}),
                'momentum': ranking(momentum), 'activity_scores': ranking(scores),
                'reopened_roles': [(names[co], title, n) for (co,key,title),n in sorted(roles.items(), key=lambda x: -x[1])]}

    def summary(self, days=30, now=None):
        s = self.hiring_intelligence(days, now)
        return {'companies_tracked': self.db.execute('SELECT COUNT(*) FROM companies').fetchone()[0],
                'active_jobs': self.db.execute("SELECT COUNT(*) FROM jobs WHERE current_status='LIVE'").fetchone()[0],
                'closed_jobs': self.db.execute("SELECT COUNT(*) FROM jobs WHERE current_status='CLOSED'").fetchone()[0],
                'new_jobs_last_days': s['new_jobs'], 'reopened_last_days': s['reopened_jobs'],
                'top_hiring_companies': s['top_hiring_companies']}

    def analytics_text(self, days=30):
        s = self.summary(days)
        return ('Monitoring summary\nCompanies tracked: {companies_tracked}\nActive jobs: {active_jobs}\nClosed jobs: {closed_jobs}\n'.format(**s)
                + self.intelligence_text(days))

    def intelligence_text(self, days=30, now=None):
        s = self.hiring_intelligence(days, now)
        lines = ['### Market Activity', '', f'Market activity ({days} days)',
                 f"New jobs discovered: {s['new_jobs']}", f"Jobs closed: {s['closed_jobs']}",
                 f"Jobs reopened: {s['reopened_jobs']}", f"Net growth (discoveries minus closures): {s['net_growth']:+d}"]
        for title, values in [('Top Hiring Companies', [f'{n} ({c} active jobs)' for n,c in s['top_hiring_companies']]),
                              ('Hiring Momentum', [f'{n} {c:+d}' for n,c in s['momentum']]),
                              ('Reopened Roles', [f'{n} — {t} — reopened {c} times' for n,t,c in s['reopened_roles']]),
                              ('Employer Activity Score', [f'{n}: {c}' for n,c in s['activity_scores']])]:
            lines += ['', '### ' + title, ''] + ['- ' + v.replace('\n', ' ') for v in (values or ['None yet.'])]
        return '\n'.join(lines) + '\n'
