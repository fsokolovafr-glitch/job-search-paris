"""SQLite history store for job lifecycle and monitoring analytics."""
import hashlib
import json
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    # NFKC handles compatibility spaces; accent folding makes Société/Societe the same key.
    value = unicodedata.normalize("NFKC", str(value))
    value = "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))
    return " ".join(value.lower().split())


def job_key(company, title, url):
    raw = "".join((norm(company), norm(title), norm(url))).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class HistoryStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    def _event(self, key, event_type, timestamp, details):
        self.db.execute("INSERT INTO job_events(job_key,event_type,event_timestamp,details) VALUES(?,?,?,?)",
                        (key, event_type, timestamp, json.dumps(details, ensure_ascii=False, sort_keys=True)))

    def upsert_companies(self, companies, timestamp):
        for company in companies:
            self.db.execute("""INSERT INTO companies(company_name,aliases,city,priority,monitoring_status,created_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(company_name) DO UPDATE SET aliases=excluded.aliases,
                city=excluded.city,priority=excluded.priority,monitoring_status=excluded.monitoring_status""",
                (company["company"], json.dumps(company.get("aliases", []), ensure_ascii=False), company.get("city"),
                 company.get("priority"), company.get("monitoring_status"), timestamp))

    def record_run(self, jobs, result, mode, timestamp):
        """Persist observations and emit lifecycle events in one transaction."""
        now = timestamp.isoformat()
        self.upsert_companies(result.get("companies", []), now)
        for item in jobs:
            key = job_key(item["company"], item["title"], item["url"])
            current = self.db.execute("SELECT * FROM jobs WHERE job_key=?", (key,)).fetchone()
            status = item.get("status", "UNKNOWN")
            if current is None:
                self.db.execute("INSERT INTO jobs(job_key,company_name,title,url,first_seen,last_seen,current_status) VALUES(?,?,?,?,?,?,?)",
                                (key, item["company"], item["title"], item["url"], item.get("found_date", now[:10]), now, status))
                self._event(key, "JOB_DISCOVERED", now, {"status": status, "url": item["url"]})
            else:
                old = current["current_status"]
                self.db.execute("UPDATE jobs SET last_seen=?,current_status=?,title=?,url=? WHERE job_key=?",
                                (now, status, item["title"], item["url"], key))
                if old != status:
                    event = "JOB_REOPENED" if old == "CLOSED" and status == "LIVE" else "JOB_CLOSED" if status == "CLOSED" else "JOB_STATUS_CHANGED"
                    self._event(key, event, now, {"from": old, "to": status})
                elif item.get("last_check_status") == "LIVE":
                    self._event(key, "JOB_UPDATED", now, {"status": status})
        self.db.execute("INSERT INTO monitoring_runs(run_timestamp,mode,jobs_found,jobs_new,jobs_closed,jobs_unknown) VALUES(?,?,?,?,?,?)",
                        (now, mode, result.get("live_jobs", 0), result.get("new_jobs_found", 0), result.get("jobs_closed", 0), len(result.get("unknown", []))))
        self.db.commit()

    def summary(self, days=30, now=None):
        now = now or datetime.now(timezone.utc)
        since = (now - timedelta(days=days)).isoformat()
        companies = self.db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        active = self.db.execute("SELECT COUNT(*) FROM jobs WHERE current_status='LIVE'").fetchone()[0]
        closed = self.db.execute("SELECT COUNT(*) FROM jobs WHERE current_status='CLOSED'").fetchone()[0]
        new = self.db.execute("SELECT COUNT(*) FROM job_events WHERE event_type='JOB_DISCOVERED' AND event_timestamp>=?", (since,)).fetchone()[0]
        top = self.db.execute("SELECT company_name,COUNT(*) n FROM jobs WHERE current_status='LIVE' GROUP BY company_name ORDER BY n DESC,company_name LIMIT 10").fetchall()
        reopened = self.db.execute("SELECT COUNT(*) FROM job_events WHERE event_type='JOB_REOPENED' AND event_timestamp>=?", (since,)).fetchone()[0]
        return {"companies_tracked": companies, "active_jobs": active, "closed_jobs": closed,
                "new_jobs_last_days": new, "reopened_last_days": reopened,
                "top_hiring_companies": [(r["company_name"], r["n"]) for r in top]}

    def analytics_text(self, days=30):
        s = self.summary(days)
        lines = ["Monitoring summary", f"Companies tracked: {s['companies_tracked']}",
                 f"Active jobs: {s['active_jobs']}", f"Closed jobs: {s['closed_jobs']}",
                 f"New jobs last {days} days: {s['new_jobs_last_days']}", f"Reopened last {days} days: {s['reopened_last_days']}",
                 "Top hiring companies:"]
        lines += [f"{i}. {name} ({count} active jobs)" for i, (name, count) in enumerate(s["top_hiring_companies"], 1)] or ["None yet."]
        return "\n".join(lines) + "\n"
