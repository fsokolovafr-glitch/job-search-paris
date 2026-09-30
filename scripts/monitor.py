"""Conservative vacancy monitoring with optional discovery. Python 3.9+."""
import argparse
import copy
import hashlib
import json
import os
import re
import time
import tempfile
import shutil
import unicodedata
from history_store import HistoryStore, canonical
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from itertools import zip_longest
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = {
    'linkedin': ('linkedin.com',),
    'indeed': ('indeed.com', 'indeed.fr'),
    'welcome_to_the_jungle': ('welcometothejungle.com',),
}


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


def platform_for(url):
    """Hard allowlist. Config cannot expand the set of permitted sites."""
    try:
        p = urlsplit(url)
        if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443):
            return None
        host = p.hostname or ''
        for name, domains in PLATFORMS.items():
            if any(host == d or host.endswith('.' + d) for d in domains):
                if name == 'linkedin' and re.match(r'^/jobs/view/[^/]+', p.path):
                    return name
                if name == 'indeed' and p.path in ('/viewjob', '/rc/clk'):
                    return name
                if name == 'welcome_to_the_jungle' and re.match(r'^/[^/]+/companies/[^/]+/jobs/[^/]+', p.path):
                    return name
    except (ValueError, TypeError):
        pass
    return None



def has_phrase(text, phrase):
    return bool(re.search(r'(?<!\w)' + re.escape(normalize(phrase)) + r'(?!\w)', normalize(text)))


def allowed_job_page(url, config):
    if platform_for(url):
        return True
    try:
        p = urlsplit(url)
        if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443):
            return False
        # Exact public hosts and path prefixes reviewed by the owner. Config does
        # not grant blanket permission to arbitrary redirects or internal hosts.
        approved = {
            'payhawk.com': '/careers/', 'job.lumapps.com': '/jobs/',
            'careers.doctolib.com': '/jobs/', 'job-boards.greenhouse.io': '/artefactjobs/jobs/',
            'jobs.ashbyhq.com': '/mistral.ai/', 'jobgether.com': '/offer/',
            'startup.jobs': '/demand-generation-lead-mistral-ai-',
            'www.growthtalent.org': '/jobs/', 'growthtalent.org': '/jobs/',
        }
        return (config.get('check_reviewed_employer_sites', False) and
                p.hostname in approved and p.path.startswith(approved[p.hostname]))
    except (ValueError, TypeError):
        return False


def title_matches(title, config):
    text = normalize(title)
    # Early-stage describes the company, not a French internship.
    exclusion_text = re.sub(r'\bearly[ -]stage\b', '', text)
    return (any(has_phrase(text, term) for term in config['role_keywords']) and
            not any(has_phrase(exclusion_text, term) for term in config['excluded_keywords']))
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



def eligible(job, config):
    employer = job.get('hiringOrganization', {})
    employer = employer.get('name', '') if isinstance(employer, dict) else ''
    locations = countries(job.get('jobLocation', [])) + countries(job.get('applicantLocationRequirements', []))
    return (bool(employer) and title_matches(job.get('title', ''), config) and
            bool(set(locations) & set(map(normalize, config['country_aliases']))))


def classify(page, config, now, expected=None):
    status = page.get('status', 0)
    if status in (404, 410):
        return 'CLOSED', 'HTTP ' + str(status), None
    if status != 200:
        return 'UNKNOWN', page.get('error', 'HTTP ' + str(status)), None
    candidates = [j for j in job_postings(page.get('body', '')) if eligible(j, config)]
    if expected:
        names = [expected['company']] + expected.get('company_aliases', [])
        candidates = [j for j in candidates if normalize(j['hiringOrganization']['name']) in set(map(normalize, names))]
    if len(candidates) != 1:
        return 'UNKNOWN', 'missing_or_ambiguous_matching_JobPosting', None
    job = candidates[0]
    expires = job.get('validThrough')
    if expires:
        try:
            text = str(expires)
            if len(text) == 10:
                end = datetime.combine(date.fromisoformat(text) + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
            else:
                end = datetime.fromisoformat(text.replace('Z', '+00:00'))
                if end.tzinfo is None:
                    return 'UNKNOWN', 'expiration_timezone_missing', job
            if end <= now:
                return 'CLOSED', 'JobPosting.validThrough_expired', job
        except ValueError:
            return 'UNKNOWN', 'invalid_validThrough', job
    return 'LIVE', 'matching_JobPosting', job


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LiveProvider:
    def __init__(self, config, discovery='required'):
        self.config = config
        self.key = os.environ.get('BRAVE_SEARCH_API_KEY')
        self.discovery = discovery
        if not self.key and discovery == 'required':
            raise ValueError('Set BRAVE_SEARCH_API_KEY; no monitoring data was changed.')
        self.opener = build_opener(NoRedirect())

    def search(self):
        """One global search per platform/role, independent of company count."""
        if self.discovery == 'off' or not self.key:
            return {'hits': [], 'errors': [], 'queries': [], 'query_count': 0,
                    'discovery_status': 'disabled' if self.discovery == 'off' else 'missing_api_key'}
        hits, errors, sources = [], [], []
        queries = 0
        for platform, domains in PLATFORMS.items():
            scope = '(' + ' OR '.join('site:' + d for d in domains) + ')'
            for term in self.config['search_terms']:
                query = '{} "{}" {}'.format(scope, term, self.config['country'])
                sources.append({'platform': platform, 'query': query})
                queries += 1
                params = urlencode({'q': query, 'country': self.config['country_code'], 'count': 20})
                success = False
                for attempt in range(3):
                    time.sleep(1.1 * (2 ** attempt))
                    request = Request('https://api.search.brave.com/res/v1/web/search?' + params,
                                      headers={'X-Subscription-Token': self.key, 'Accept': 'application/json'})
                    try:
                        with self.opener.open(request, timeout=25) as response:
                            payload = json.load(response)
                        if not isinstance(payload, dict) or 'error' in payload:
                            raise ValueError('Invalid search payload')
                        hits.extend(payload.get('web', {}).get('results', []))
                        success = True
                        break
                    except HTTPError as error:
                        if error.code not in (429, 500, 502, 503, 504):
                            break
                    except (URLError, TimeoutError, OSError, ValueError):
                        pass
                if not success:
                    errors.append({'platform': platform, 'query': query, 'reason': 'search_failed'})
        return {'hits': hits, 'errors': errors, 'queries': sources, 'query_count': queries}

    def fetch(self, url):
        current = url
        # Follow at most five redirects, checking BEFORE every request.
        for _ in range(6):
            if not allowed_job_page(current, self.config):
                return {'status': 0, 'error': 'blocked_outside_allowed_job_pages'}
            for attempt in range(3):
                try:
                    req = Request(current, headers={'User-Agent': 'JobSearchParis/2.0', 'Accept': 'text/html'})
                    with self.opener.open(req, timeout=self.config.get('fetch_timeout_seconds', 20)) as response:
                        body = response.read(3_000_001)
                        if len(body) > 3_000_000:
                            return {'status': 0, 'error': 'page_too_large'}
                        return {'status': response.status, 'body': body.decode('utf-8', errors='replace'), 'final_url': current}
                except HTTPError as error:
                    if error.code in (301, 302, 303, 307, 308):
                        target = error.headers.get('Location')
                        if not target:
                            return {'status': 0, 'error': 'redirect_without_location'}
                        current = urljoin(current, target)
                        break
                    if error.code in (429, 500, 502, 503, 504) and attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                    # A missing redirected target does not prove the saved listing closed.
                    if current != url and error.code in (404, 410):
                        return {'status': 0, 'error': 'redirect_target_missing'}
                    return {'status': error.code}
                except (URLError, TimeoutError, OSError, ValueError):
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                    return {'status': 0, 'error': 'fetch_failed'}
        return {'status': 0, 'error': 'redirect_limit'}


class MockProvider:
    def __init__(self, fixture):
        self.fixture = fixture
        self.fetched = []
        self.search_calls = 0

    def search(self):
        self.search_calls += 1
        return copy.deepcopy(self.fixture['search'])

    def fetch(self, url):
        self.fetched.append(url)
        return copy.deepcopy(self.fixture['pages'].get(url, {'status': 0, 'error': 'missing_mock_page'}))


def validate(jobs):
    if not isinstance(jobs, list):
        raise ValueError('jobs must be a JSON array')
    seen = set()
    for job in jobs:
        key = canonical(job['url'])
        if key in seen or not job.get('title') or not job.get('company'):
            raise ValueError('Invalid or duplicate job')
        if job.get('status') not in ('UNKNOWN', 'LIVE', 'CLOSED'):
            raise ValueError('Invalid status')
        seen.add(key)


def monitor(jobs, companies, config, provider, now):
    validate(jobs)
    jobs = copy.deepcopy(jobs)
    result = {'date': now.date().isoformat(), 'checked_at': now.isoformat(), 'new': [], 'closed': [],
              'reopened': [], 'unknown': [], 'excluded': [], 'observations': [], 'sources': [],
              'pages_checked': 0, 'live_jobs': 0, 'platforms': list(PLATFORMS)}
    priority = set()
    rejected = set()
    for company in companies:
        names = [normalize(company['company'])] + list(map(normalize, company.get('aliases', [])))
        if company.get('exclude_company') or company.get('monitoring_status') == 'paused':
            rejected.update(names)
        elif company.get('list') != 'backlog':
            priority.update(names)
    search = provider.search()
    result['search_queries'] = search['queries']
    result['query_count'] = search['query_count']
    result['discovery_status'] = search.get('discovery_status', 'enabled')
    for err in search['errors']:
        result['unknown'].append(dict(err, company=err.get('platform', ''), title='WebSearch', url=''))
    known = {canonical(j['url']): j for j in jobs}
    queue = []
    queued = set()
    for job in sorted(jobs, key=lambda item: item.get('last_checked') or ''):
        if job['status'] == 'CLOSED':
            continue  # Recheck a closed listing only if search finds it again.
        if normalize(job['company']) in rejected:
            queued.add(canonical(job['url']))
        else:
            queue.append((job['url'], job))
            queued.add(canonical(job['url']))
    saved_queue = queue
    queue = []
    for hit in search['hits']:
        url = hit.get('url', '')
        if not allowed_job_page(url, config):
            result['excluded'].append({'url': url, 'reason': 'outside_allowed_job_pages'})
            continue
        key = canonical(url)
        if key not in queued:
            queue.append((url, known.get(key)))
            queued.add(key)
    queue = [item for pair in zip_longest(saved_queue, queue) for item in pair if item is not None]
    for url, job in queue:
        if not allowed_job_page(url, config):
            result['excluded'].append({'url': url, 'reason': 'outside_allowed_job_pages'})
            continue
        if result['pages_checked'] >= config['max_pages_per_run']:
            result['unknown'].append({'company': job['company'] if job else '', 'title': job['title'] if job else 'Кандидат',
                                      'url': url, 'reason': 'page_budget_exceeded'})
            continue
        page = provider.fetch(url)
        result['pages_checked'] += 1
        result['sources'].append(url)
        if page.get('final_url') and allowed_job_page(page['final_url'], config):
            result['sources'].append(page['final_url'])
        status, reason, posting = classify(page, config, now, job)
        evidence = {'url': url, 'status': status, 'reason': reason, 'checked_at': now.isoformat(),
                    'content_sha256': hashlib.sha256(page.get('body', '').encode()).hexdigest()}
        result['observations'].append(evidence)
        if not job:
            if status != 'LIVE':
                if status == 'UNKNOWN':
                    result['unknown'].append(dict(evidence, company='', title='Кандидат: требуется проверка'))
                continue
            employer = posting['hiringOrganization']['name']
            if normalize(employer) in rejected:
                continue
            job = {'company': employer, 'title': posting['title'], 'url': url, 'status': 'UNKNOWN',
                   'found_date': result['date']}
            jobs.append(job)
            result['new'].append(dict(evidence, company=employer, title=job['title'], priority=normalize(employer) in priority))
        previous = job['status']
        job['platform'] = platform_for(url)
        job['priority_company'] = normalize(job['company']) in priority
        job['last_checked'] = result['date']
        job['last_check_status'] = status
        job['evidence'] = evidence
        event = dict(evidence, company=job['company'], title=job['title'], priority=job['priority_company'])
        if status == 'UNKNOWN':
            result['unknown'].append(event)
            continue
        job['status'] = status
        if status == 'CLOSED' and previous != 'CLOSED':
            result['closed'].append(event)
        if status == 'LIVE':
            job['title'] = posting['title']
            job['posting'] = posting
            result['live_jobs'] += 1
            if previous == 'CLOSED':
                result['reopened'].append(event)
    result['sources'] = sorted(set(result['sources']))
    result['new_jobs_found'] = len(result['new'])
    result['jobs_closed'] = len(result['closed'])
    result['status'] = 'partial' if result['unknown'] or result['discovery_status'] == 'missing_api_key' else 'completed'
    return jobs, result


def report(result, mode):
    lines = ['# Monitoring Report — ' + result['date'], '', '**{} / {}**'.format(mode.upper(), result['status']),
             'Площадки: LinkedIn Jobs, Indeed, Welcome to the Jungle; разрешённые карточки работодателей при включённой настройке.',
             'Поиск новых вакансий: ' + result.get('discovery_status', 'enabled'),
             'MOCK: искусственные данные.' if mode == 'mock' else 'LIVE подтверждает подходящую разметку на момент проверки; полнота поиска не гарантируется.', '']
    for title, key in [('Новые вакансии', 'new'), ('Закрытые вакансии', 'closed'),
                       ('Повторно открытые вакансии', 'reopened'), ('Требуют проверки', 'unknown')]:
        lines += ['## ' + title, '']
        for item in result[key]:
            label = (item.get('company', '') + ' — ' + item.get('title', '')).replace('\n', ' ')
            label = label.replace('[', '\\[').replace(']', '\\]')
            lines.append('- {}{}{} — {}'.format('★ ' if item.get('priority') else '', label,
                         ' — [ссылка](<{}>)'.format(item['url']) if item.get('url') else '', item['reason']))
        if not result[key]:
            lines.append('- Не обнаружено в этом запуске.')
        lines.append('')
    lines += ['## Market changes', '', '- New jobs discovered: ' + str(result['new_jobs_found']),
              '- Closed jobs: ' + str(result['jobs_closed']),
              '- Reopened jobs: ' + str(len(result['reopened'])), '',
              '## Статистика', '', '- Поисковых запросов: ' + str(result['query_count']),
              '- Проверено страниц вакансий: ' + str(result['pages_checked']),
              '- Подтверждено LIVE: ' + str(result['live_jobs']), '- Новых: ' + str(result['new_jobs_found']),
              '- Закрыто: ' + str(result['jobs_closed']), '- Исключено сторонних ссылок: ' + str(len(result['excluded'])),
              '', '★ Компания из приоритетного списка. Заявки не отправлялись.', '', '## Источники', '']
    lines += ['- [Страница](<{}>)'.format(url) for url in result['sources']]
    lines += ['', '## Поисковые запросы', '']
    lines += ['- ' + q['query'] for q in result['search_queries']]
    return '\n'.join(lines) + '\n'


def save_run(output, jobs, result, mode, tracker, companies):
    output = Path(output)
    reports = output / 'reports'
    reports.mkdir(parents=True, exist_ok=True)
    run_id = result['checked_at'].replace(':', '').replace('+', '_')
    filename = 'reports/{}-{}.md'.format(run_id, mode)
    result = dict(result, mode=mode, report_file=filename)
    with HistoryStore(output / 'data/job-search.db') as store:
        if not store.initialized():
            store.bootstrap([], companies, tracker)
        store.record_run(jobs, dict(result, companies=companies), mode, datetime.fromisoformat(result['checked_at']))
        write_json(output / 'data/jobs.json', store.export_jobs())
        write_json(output / 'data/companies-shortlist.json', store.export_companies())
        write_json(output / 'data/job-tracking.json', store.export_tracker())
        body = report(result, mode) + '\n## Hiring Intelligence\n\n' + store.intelligence_text(30, datetime.fromisoformat(result['checked_at']))
    (output / filename).write_text(body, encoding='utf-8')
    (reports / (result['date'] + '-report.md')).write_text(body, encoding='utf-8')
    write_json(reports / (run_id + '-' + mode + '.json'), result)
    from pipeline import export_all
    with HistoryStore(output / 'data/job-search.db') as store:
        export_all(store, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['live', 'mock'], default='mock')
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--fixture', type=Path, default=ROOT / 'tests/fixtures/demo.json')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--discovery', choices=['required', 'auto', 'off'], default='required')
    parser.add_argument('--max-pages', type=int)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.mode == 'mock' and (not args.output or args.output.resolve() == root):
        parser.error('Mock requires a separate --output directory.')
    output = args.output.resolve() if args.output else root
    config = read_json(root / 'data/config.json')
    if args.max_pages is not None:
        config['max_pages_per_run'] = args.max_pages
    if type(config['max_pages_per_run']) is not int or not 1 <= config['max_pages_per_run'] <= 100:
        parser.error('max_pages_per_run must be 1..100')
    fixture = read_json(args.fixture) if args.mode == 'mock' else None
    provider = MockProvider(fixture) if fixture is not None else LiveProvider(config, args.discovery)
    sandbox = None
    if args.dry_run:
        sandbox = tempfile.TemporaryDirectory()
        previous_output = output
        output = Path(sandbox.name)
        (output / 'data').mkdir()
        if (previous_output / 'data/job-search.db').exists():
            import sqlite3
            with sqlite3.connect(previous_output / 'data/job-search.db') as source_db:
                with sqlite3.connect(output / 'data/job-search.db') as target_db:
                    source_db.backup(target_db)
    output.mkdir(parents=True, exist_ok=True)
    lock = output / '.monitor.lock'
    try:
        descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        parser.error('Another writer or stale .monitor.lock exists.')
    try:
        os.close(descriptor)
        with HistoryStore(output / 'data/job-search.db') as store:
            if not store.initialized():
                if fixture is not None:
                    store.bootstrap(fixture['jobs'], fixture['companies'], {'check_history': []})
                else:
                    store.bootstrap(read_json(root / 'data/jobs.json'), read_json(root / 'data/companies-shortlist.json'),
                                    read_json(root / 'data/job-tracking.json'))
            jobs, companies, tracker = store.export_jobs(), store.export_companies(), store.export_tracker()
        updated, result = monitor(jobs, companies, config, provider, datetime.now(timezone.utc))
        print(report(result, args.mode))
        if not args.dry_run:
            save_run(output, updated, result, args.mode, tracker, companies)
    finally:
        lock.unlink()
        if sandbox:
            sandbox.cleanup()


if __name__ == '__main__':
    main()
