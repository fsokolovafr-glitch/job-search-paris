#!/usr/bin/env python3
"""Validate tracker/report references without mutating monitoring evidence.

Actual updates are shared with check-jobs.py through monitor.save_run.
"""
import argparse
from pathlib import Path
from monitor import ROOT, read_json, validate

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    validate(read_json(args.root / 'data/companies-shortlist.json'))
    history = read_json(args.root / 'data/job-tracking.json')['check_history']
    for item in history:
        report = (args.root / item['report_file']).resolve()
        if args.root.resolve() not in report.parents or not report.is_file():
            raise ValueError('Missing or invalid report reference')
    print('OK: shortlist and {} check history records'.format(len(history)))
