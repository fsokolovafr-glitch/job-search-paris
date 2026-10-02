#!/usr/bin/env python3
"""Local-only application tracker. Its data file is ignored by Git."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from history_store import canonical, norm

STATUSES = {'applied', 'interview', 'offer', 'hired', 'rejected', 'withdrawn',
            'offer_declined', 'no_reply'}


def local_setting(name):
    value = os.environ.get(name)
    if value:
        return value
    env_file = Path(__file__).resolve().parents[1] / '.env'
    if env_file.is_file():
        for line in env_file.read_text(encoding='utf-8').splitlines():
            key, separator, candidate = line.partition('=')
            if separator and key.strip() == name:
                return candidate.strip()
    return None


def default_path():
    configured = local_setting('JOB_APPLICATIONS_FILE')
    return Path(configured) if configured else Path(__file__).resolve().parents[1] / 'private/applications.json'


def read_tracker(path):
    path = Path(path)
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('schema_version') != 1 or not isinstance(value.get('records'), list):
        raise ValueError('Unsupported private tracker format')
    return value


def find_record(tracker, url=None, company=None, title=None):
    if tracker is None:
        return None
    wanted_url = canonical(url) if url else None
    for record in tracker['records']:
        if wanted_url and record.get('url') and canonical(record['url']) == wanted_url:
            return record
        if not wanted_url and company and norm(record.get('company', '')) == norm(company):
            if not title or norm(record.get('title', '')) == norm(title):
                return record
    return None


def save_tracker(path, tracker):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(tracker, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.chmod(temp, 0o600)
    temp.replace(path)


def upsert(path, company, title, url, status, event_date, note):
    if status not in STATUSES:
        raise ValueError('Invalid application status')
    datetime.strptime(event_date, '%Y-%m-%d')
    tracker = read_tracker(path) or {'schema_version': 1, 'records': []}
    record = find_record(tracker, url=url)
    if record is None:
        record = {'company': company, 'title': title, 'url': canonical(url), 'events': []}
        tracker['records'].append(record)
    record.update(company=company, title=title, url=canonical(url), status=status,
                  updated_at=datetime.now(timezone.utc).isoformat())
    event = {'status': status, 'date': event_date}
    if note:
        event['note'] = note
    if event not in record['events']:
        record['events'].append(event)
    save_tracker(path, tracker)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path, default=default_path())
    sub = parser.add_subparsers(dest='command', required=True)
    check = sub.add_parser('check', help='Check for a previously recorded application')
    check.add_argument('--url')
    check.add_argument('--company')
    check.add_argument('--title')
    record = sub.add_parser('record', help='Record a user-confirmed application event')
    record.add_argument('--company', required=True)
    record.add_argument('--title', required=True)
    record.add_argument('--url', required=True)
    record.add_argument('--status', required=True, choices=sorted(STATUSES))
    record.add_argument('--date', required=True)
    record.add_argument('--note')
    record.add_argument('--confirm-recorded-action', action='store_true', required=True,
                        help='Confirm that this event actually happened; preparation alone is not an application')
    args = parser.parse_args()
    if args.command == 'check':
        if not args.url and not args.company:
            parser.error('check requires --url or --company')
        tracker = read_tracker(args.file)
        if tracker is None:
            print(json.dumps({'result': 'UNKNOWN', 'reason': 'private_tracker_missing'}, ensure_ascii=False))
            return 4
        found = find_record(tracker, args.url, args.company, args.title)
        if found:
            result, reason, code = 'FOUND', None, 0
        elif tracker.get('history_complete') is True:
            result, reason, code = 'NOT_RECORDED', None, 3
        else:
            result, reason, code = 'UNKNOWN', 'private_tracker_incomplete', 4
        print(json.dumps({'result': result, 'reason': reason, 'record': found}, ensure_ascii=False))
        return code
    print(json.dumps(upsert(args.file, args.company, args.title, args.url, args.status, args.date, args.note),
                     ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
