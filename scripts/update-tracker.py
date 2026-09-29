#!/usr/bin/env python3
"""Validate SQLite, regenerate JSON exports, or update an application status."""
import argparse
import json
from pathlib import Path
from history_store import HistoryStore, canonical
from monitor import ROOT, read_json, write_json, validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--job-url')
    parser.add_argument('--application-status', choices=['not_applied', 'applied', 'rejected'])
    args = parser.parse_args()
    if bool(args.job_url) != bool(args.application_status):
        parser.error('--job-url and --application-status must be supplied together')
    data = args.root / 'data'
    with HistoryStore(data / 'job-search.db') as store:
        if not store.initialized():
            store.bootstrap(read_json(data / 'jobs.json'), read_json(data / 'companies-shortlist.json'), read_json(data / 'job-tracking.json'))
        if store.db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite integrity check failed')
        if args.job_url:
            row = next((r for r in store.db.execute('SELECT * FROM jobs') if canonical(r['url']) == canonical(args.job_url)), None)
            if row is None:
                parser.error('Unknown job URL')
            payload = json.loads(row['payload'])
            payload.update(application_status=args.application_status, applied=args.application_status != 'not_applied')
            with store.db:
                store.db.execute('UPDATE jobs SET payload=? WHERE job_key=?', (json.dumps(payload), row['job_key']))
        jobs, companies, tracker = store.export_jobs(), store.export_companies(), store.export_tracker()
        validate(jobs)
        for item in tracker['check_history']:
            if not item.get('report_file'):
                continue  # Legacy SQLite runs may not have a report path.
            report = (args.root / item['report_file']).resolve()
            if args.root.resolve() not in report.parents or not report.is_file():
                raise ValueError('Missing or invalid report reference')
        for name, value in [('jobs.json', jobs), ('companies-shortlist.json', companies), ('job-tracking.json', tracker)]:
            write_json(data / name, value)
    print('OK: SQLite validated; compatibility exports regenerated')


if __name__ == '__main__':
    main()
