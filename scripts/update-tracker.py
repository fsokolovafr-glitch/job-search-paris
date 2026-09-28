#!/usr/bin/env python3
"""Validate job data and report references. check-jobs.py performs updates."""
import argparse
from pathlib import Path
from monitor import ROOT, read_json, validate
from history_store import HistoryStore

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    validate(read_json(args.root / 'data/jobs.json'))
    with HistoryStore(args.root / 'data/job-search.db') as store:
        store.db.execute('PRAGMA integrity_check').fetchone()
    history = read_json(args.root / 'data/job-tracking.json')['check_history']
    for item in history:
        report = (args.root / item['report_file']).resolve()
        if args.root.resolve() not in report.parents or not report.is_file():
            raise ValueError('Missing or invalid report reference')
    print('OK: jobs and {} check history records'.format(len(history)))
