#!/usr/bin/env python3
"""Validate public SQLite and regenerate public derived files."""
import argparse
import json
from pathlib import Path
from history_store import HistoryStore
from monitor import ROOT, read_json, validate
from pipeline import export_all, writer_lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    data = args.root / 'data'
    with writer_lock(args.root), HistoryStore(data / 'job-search.db') as store:
        if not store.initialized():
            store.bootstrap(read_json(data / 'jobs.json'), read_json(data / 'companies-shortlist.json'), read_json(data / 'job-tracking.json'))
        if store.db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite integrity check failed')
        jobs, companies, tracker = store.export_jobs(), store.export_companies(), store.export_tracker()
        validate(jobs)
        for item in tracker['check_history']:
            if not item.get('report_file'):
                continue  # Legacy SQLite runs may not have a report path.
            report = (args.root / item['report_file']).resolve()
            if args.root.resolve() not in report.parents or not report.is_file():
                raise ValueError('Missing or invalid report reference')
        export_all(store, args.root)
    print('OK: public SQLite validated; JSON, dashboard and reports regenerated')


if __name__ == '__main__':
    main()
