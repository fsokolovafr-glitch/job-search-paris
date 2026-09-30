#!/usr/bin/env python3
"""Create a publication-safe monitoring snapshot from a private reviewed export."""
import argparse
import json
from pathlib import Path

COMPANY_FIELDS = {'company', 'city', 'geography', 'list', 'monitoring_status',
                  'priority', 'review_status', 'roles', 'track'}
JOB_FIELDS = {'company', 'title', 'url', 'status', 'source_reported_status', 'status_basis'}


def select(item, allowed):
    return {key: item[key] for key in allowed if key in item and item[key] not in (None, '')}


def sanitize(source):
    companies = []
    for item in source.get('companies', []):
        clean = select(item, COMPANY_FIELDS)
        roles = []
        for role in item.get('unlinked_roles', []):
            role_clean = select(role, JOB_FIELDS - {'company', 'url'})
            if role_clean:
                roles.append(role_clean)
        if roles:
            clean['unlinked_roles'] = roles
        companies.append(clean)
    return {
        'schema_version': 1,
        'source_url': 'private-reviewed-source-withheld',
        'snapshot_at': source['snapshot_at'],
        'snapshot_time_precision': source.get('snapshot_time_precision', 'exact'),
        'privacy': 'application history, contacts, notes and CV data removed before publication',
        'companies': companies,
        'jobs': [select(item, JOB_FIELDS) for item in source.get('jobs', [])],
        'applications': [],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='Private input; keep it outside the repository')
    parser.add_argument('output', type=Path, help='Public snapshot path')
    args = parser.parse_args()
    value = sanitize(json.loads(args.input.read_text(encoding='utf-8')))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Sanitized: {} companies, {} jobs, 0 applications'.format(len(value['companies']), len(value['jobs'])))


if __name__ == '__main__':
    main()
