"""Conservative job monitor. Python 3.9+, standard library only."""
import argparse
import copy
import hashlib
import json
import os
import re
import time
import unicodedata
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


def normalize(text):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD', str(text).lower())
                            if not unicodedata.combining(c)).split())


def canonical(url):
    p = urlsplit(url)
    if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password:
        raise ValueError('Expected a public HTTP(S) URL')
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('gclid', 'fbclid')]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip('/') or '/',
                       urlencode(sorted(query)), ''))


class JsonLD(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.buffer = []
        self.values = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script' and dict(attrs).get('type', '').lower() == 'application/ld+json':
            self.active = True
            self.buffer = []

    def handle_data(self, data):
        if self.active:
            self.buffer.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.active:
            try:
                self.values.append(json.loads(''.join(self.buffer)))
            except ValueError:
                pass
            self.active = False


def job_postings(html):
    parser = JsonLD()
    parser.feed(html)
    def walk(value):
        if isinstance(value, list):
            for item in value:
                yield from walk(item)
        elif isinstance(value, dict):
            types = value.get('@type', [])
            if 'JobPosting' in (types if isinstance(types, list) else [types]):
                yield value
            for child in value.values():
                if isinstance(child, (dict, list)):
                    yield from walk(child)
    return list(walk(parser.values))


def countries(value):
    result = []
    if isinstance(value, list):
        for child in value:
            result.extend(countries(child))
    elif isinstance(value, dict):
        country = value.get('addressCountry')
        if country:
            result.append(normalize(country.get('name', '') if isinstance(country, dict) else country))
        if value.get('@type') == 'Country':
            result.append(normalize(value.get('name', '')))
        for child in value.values():
            if isinstance(child, (dict, list)):
                result.extend(countries(child))
    return result


def matching_job(job, company, config):
    employer = job.get('hiringOrganization', {})
    employer = employer.get('name', '') if isinstance(employer, dict) else ''
    aliases = [company['company']] + company.get('aliases', [])
    if normalize(employer) not in [normalize(a) for a in aliases]:
        return False
    title = normalize(job.get('title', ''))
    if not any(normalize(term) in title for term in config['role_keywords']):
        return False
    if any(normalize(term) in title for term in config.get('excluded_keywords', [])):
        return False
    locations = countries(job.get('jobLocation', [])) + countries(job.get('applicantLocationRequirements', []))
    return bool(set(locations) & set(map(normalize, config['country_aliases'])))


def classify(page, company, config, today, expected_title=None):
    code = page.get('status', 0)
    if code in (404, 410):
        return 'CLOSED', 'HTTP ' + str(code), None
    if code != 200:
        return 'UNKNOWN', 'HTTP ' + str(code) if code else page.get('error', 'fetch_failed'), None
    if page.get('redirected'):
        return 'UNKNOWN', 'redirect_requires_review', None
    matching = [j for j in job_postings(page.get('body', '')) if matching_job(j, company, config)]
    if expected_title:
        matching = [j for j in matching if normalize(j.get('title', '')) == normalize(expected_title)]
    if len(matching) != 1:
        return 'UNKNOWN', 'missing_or_ambiguous_matching_JobPosting', None
    job = matching[0]
    expires = job.get('validThrough')
    if expires:
        try:
            if date.fromisoformat(str(expires)[:10]) < today:
                return 'CLOSED', 'JobPosting.validThrough_expired', job
        except ValueError:
            return 'UNKNOWN', 'invalid_validThrough', job
    return 'LIVE', 'matching_JobPosting', job


class LiveProvider:
    def __init__(self, config):
        self.config = config
        self.key = os.environ.get('BRAVE_SEARCH_API_KEY')
        if not self.key:
            raise ValueError('Set BRAVE_SEARCH_API_KEY for live mode; no data was changed.')

    def search(self, company):
        results = []
        sources = []
        for term in self.config['search_terms']:
            query = '"{}" "{}" {}'.format(company['company'], term, self.config['country'])
            sources.append('https://search.brave.com/search?' + urlencode({'q': query}))
            for offset in range(self.config.get('search_pages', 1)):
                time.sleep(1.1)
                params = urlencode({'q': query, 'country': self.config['country_code'], 'count': 20, 'offset': offset})
                req = Request('https://api.search.brave.com/res/v1/web/search?' + params,
                              headers={'X-Subscription-Token': self.key, 'Accept': 'application/json'})
                try:
                    with urlopen(req, timeout=30) as response:
                        payload = json.load(response)
                except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
                    raise RuntimeError('search_failed:' + type(error).__name__) from None
                if not isinstance(payload, dict) or 'error' in payload:
                    raise RuntimeError('invalid_search_response')
                results.extend(payload.get('web', {}).get('results', []))
                if not payload.get('query', {}).get('more_results_available', False):
                    break
        return results, sources

    def fetch(self, url):
        try:
            req = Request(canonical(url), headers={'User-Agent': 'JobSearchParis/1.0', 'Accept': 'text/html'})
            with urlopen(req, timeout=25) as response:
                body = response.read(3_000_001)
                if len(body) > 3_000_000:
                    return {'status': 0, 'error': 'page_too_large'}
                return {'status': response.status, 'body': body.decode('utf-8', errors='replace'),
                        'redirected': canonical(response.url) != canonical(url)}
        except HTTPError as error:
            # An error at a redirected destination does not prove the original role closed.
            return {'status': error.code if canonical(error.url) == canonical(url) else 0,
                    'error': 'redirect_http_error'}
        except (URLError, TimeoutError, OSError, ValueError):
            return {'status': 0, 'error': 'fetch_failed'}


class MockProvider:
    def __init__(self, fixture):
        self.fixture = fixture

    def search(self, company):
        entry = self.fixture.get('search', {}).get(company['company'])
        if entry is None:
            raise RuntimeError('missing_mock_search_fixture')
        if isinstance(entry, dict) and 'error' in entry:
            raise RuntimeError(entry['error'])
        return entry, []

    def fetch(self, url):
        return self.fixture.get('pages', {}).get(canonical(url), {'status': 0, 'error': 'missing_mock_page'})


def validate(companies):
    if not isinstance(companies, list):
        raise ValueError('shortlist must be a JSON array')
    names = set()
    for company in companies:
        name = normalize(company['company'])
        if not name or name in names:
            raise ValueError('duplicate or empty company name')
        names.add(name)
        if company['track'] not in ('A', 'B', 'C'):
            raise ValueError('track must be A, B or C')
        if company.get('monitoring_status') not in ('found_not_applied', 'checked', 'paused', 'rejected'):
            raise ValueError('invalid monitoring_status')
        seen = set()
        for job in company['open_roles']:
            url = canonical(job['url'])
            if url in seen or not job.get('title') or type(job.get('applied')) is not bool:
                raise ValueError('invalid or duplicate job')
            if job.get('status') not in ('UNKNOWN', 'LIVE', 'CLOSED'):
                raise ValueError('invalid job status')
            seen.add(url)


def monitor(companies, config, provider, now):
    validate(companies)
    companies = copy.deepcopy(companies)
    day = now.date()
    result = {'date': day.isoformat(), 'checked_at': now.isoformat(), 'companies_checked': 0,
              'companies_skipped': 0, 'new': [], 'closed': [], 'reopened': [], 'unknown': [],
              'large': [], 'sources': [], 'observations': [], 'live_jobs': 0}
    for company in companies:
        if company.get('contact_status') == 'rejected' or company['monitoring_status'] in ('rejected', 'paused'):
            result['companies_skipped'] += 1
            continue
        large = company['track'] in ('A', 'B') and (company.get('employee_count') or 0) >= 1000
        result['companies_checked'] += 1
        company['last_checked'] = day.isoformat()
        errors_before = len(result['unknown'])
        changes_before = len(result['new']) + len(result['closed']) + len(result['reopened'])
        known = {canonical(j['url']): j for j in company['open_roles']}
        inspected = set()

        def inspect(url, job=None):
            url = canonical(url)
            if url in inspected:
                return
            inspected.add(url)
            result['sources'].append(url)
            page = provider.fetch(url)
            status, reason, posting = classify(page, company, config, day, job['title'] if job else None)
            evidence = {'company': company['company'], 'url': url, 'status': status,
                        'reason': reason, 'checked_at': now.isoformat(),
                        'content_sha256': hashlib.sha256(page.get('body', '').encode()).hexdigest()}
            result['observations'].append(evidence)
            if job is None:
                if status != 'LIVE':
                    if status == 'UNKNOWN':
                        result['unknown'].append(dict(evidence, title='Кандидат из поиска: требуется проверка'))
                    return
                job = {'title': posting['title'], 'url': url, 'status': 'UNKNOWN',
                       'found_date': day.isoformat(), 'applied': False}
                company['open_roles'].append(job)
                known[url] = job
                result['new'].append(dict(evidence, title=job['title']))
            old_status = job['status']
            job['last_checked'] = day.isoformat()
            job['last_check_status'] = status
            job['evidence'] = evidence
            if status == 'UNKNOWN':
                result['unknown'].append(dict(evidence, title=job['title']))
                # Keep the last confirmed state; this run's uncertainty remains explicit.
                return
            job['status'] = status
            if status == 'CLOSED' and old_status != 'CLOSED':
                result['closed'].append(dict(evidence, title=job['title']))
            if status == 'LIVE':
                result['live_jobs'] += 1
                if old_status == 'CLOSED':
                    result['reopened'].append(dict(evidence, title=job['title']))

        for job in list(company['open_roles']):
            if not job['applied'] and job.get('application_status') != 'rejected':
                inspect(job['url'], job)
        # Found: check saved roles; Checked and large A/B: also discover new roles.
        if company['monitoring_status'] == 'checked' or large:
            try:
                hits, search_sources = provider.search(company)
                result['sources'].extend(search_sources)
                for hit in hits:
                    url = hit.get('url', '')
                    try:
                        key = canonical(url)
                    except ValueError:
                        continue
                    existing = known.get(key)
                    if existing and (existing['applied'] or existing.get('application_status') == 'rejected'):
                        continue
                    title = normalize(hit.get('title', ''))
                    if existing or any(normalize(t) in title for t in config['role_keywords']):
                        inspect(key, existing)
            except RuntimeError as error:
                result['unknown'].append({'company': company['company'], 'title': 'WebSearch',
                                          'url': '', 'reason': str(error)})
        company['last_check_status'] = 'partial' if len(result['unknown']) > errors_before else 'completed'
        if large:
            delta = len(result['new']) + len(result['closed']) + len(result['reopened']) - changes_before
            result['large'].append({'company': company['company'], 'changes': delta,
                                    'status': company['last_check_status']})
    result['sources'] = sorted(set(result['sources']))
    result['new_jobs_found'] = len(result['new'])
    result['jobs_closed'] = len(result['closed'])
    result['status'] = 'partial' if result['unknown'] else 'completed'
    return companies, result


def md_text(value):
    return str(value).replace('\n', ' ').replace('[', '\\[').replace(']', '\\]')


def report(result, mode):
    lines = ['# Monitoring Report — ' + result['date'], '',
             '**Режим: {}. Статус: {}.**'.format(mode.upper(), result['status']),
             'MOCK — искусственные данные; не является проверкой реальных вакансий.' if mode == 'mock'
             else 'Подтверждение LIVE: подходящий JobPosting на момент проверки; полнота WebSearch не гарантируется.', '']
    for title, key in [('Новые вакансии (найдено)', 'new'), ('Закрытые вакансии', 'closed'),
                       ('Повторно открытые вакансии', 'reopened'), ('Требуют проверки', 'unknown')]:
        lines += ['## ' + title, '']
        for item in result[key]:
            link = ' — [источник](<{}>)'.format(item['url']) if item.get('url') else ''
            lines.append('- {} — {}{} — {}'.format(md_text(item['company']), md_text(item['title']), link, item['reason']))
        if not result[key]:
            lines.append('- Не обнаружено в этом запуске.' if key != 'unknown' else '- Нет.')
        lines.append('')
    lines += ['## Крупные компании (Track A/B)', '']
    lines += ['- {} — изменений: {}; проверка: {}.'.format(md_text(i['company']), i['changes'], i['status']) for i in result['large']] or ['- В проверяемых данных отсутствуют.']
    lines += ['', '## Статистика', '', '- Проверено компаний: ' + str(result['companies_checked']),
              '- Пропущено компаний: ' + str(result['companies_skipped']),
              '- Живых вакансий подтверждено в этом запуске: ' + str(result['live_jobs']),
              '- Новых: ' + str(result['new_jobs_found']), '- Закрыто: ' + str(result['jobs_closed']),
              '- Неопределённых результатов: ' + str(len(result['unknown'])), '', '## Источники', '']
    lines += ['- [Источник {}](<{}>)'.format(i + 1, url) for i, url in enumerate(result['sources'])] or ['- Нет внешних источников.']
    return '\n'.join(lines) + '\n'


def save_run(output, companies, result, mode, tracker):
    """Save snapshot and history. Run under one writer (Actions concurrency / CLI lock)."""
    output = Path(output)
    reports = output / 'reports'
    reports.mkdir(parents=True, exist_ok=True)
    run_id = result['checked_at'].replace(':', '').replace('+', '_')
    filename = 'reports/{}-{}.md'.format(run_id, mode)
    result = dict(result, mode=mode, report_file=filename)
    body = report(result, mode)
    (output / filename).write_text(body, encoding='utf-8')
    # Dated report is the latest run; timestamped reports retain earlier checks.
    (reports / (result['date'] + '-report.md')).write_text(body, encoding='utf-8')
    write_json(reports / (run_id + '-' + mode + '.json'), result)
    tracker = copy.deepcopy(tracker)
    tracker['check_history'].append({key: result[key] for key in
        ('date', 'checked_at', 'mode', 'status', 'companies_checked', 'new_jobs_found', 'jobs_closed', 'report_file')})
    write_json(output / 'data/companies-shortlist.json', companies)
    write_json(output / 'data/job-tracking.json', tracker)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['live', 'mock'], default='mock')
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, help='Required separate output directory for mock mode')
    parser.add_argument('--fixture', type=Path, default=ROOT / 'tests/fixtures/demo.json')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    if args.mode == 'mock' and (not args.output or args.output.resolve() == root):
        parser.error('Mock requires --output pointing outside the working repository root.')
    output = args.output.resolve() if args.output else root
    config = read_json(root / 'data/config.json')
    fixture = read_json(args.fixture) if args.mode == 'mock' else None
    provider = MockProvider(fixture) if fixture is not None else LiveProvider(config)
    companies = fixture['companies'] if fixture is not None else read_json(root / 'data/companies-shortlist.json')
    if not companies:
        parser.error('Shortlist is empty; no monitoring performed.')
    output.mkdir(parents=True, exist_ok=True)
    lock = output / '.monitor.lock'
    try:
        descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        parser.error('Another writer or stale .monitor.lock exists; verify before removing it.')
    try:
        os.close(descriptor)
        now = datetime.now(timezone.utc)
        updated, result = monitor(companies, config, provider, now)
        print(report(result, args.mode))
        if not args.dry_run:
            tracker_path = output / 'data/job-tracking.json'
            tracker = read_json(tracker_path) if tracker_path.exists() else {'check_history': []}
            save_run(output, updated, result, args.mode, tracker)
    finally:
        lock.unlink()


if __name__ == '__main__':
    main()
