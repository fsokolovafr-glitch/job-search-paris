"""Import publication-safe snapshots and derive public views from SQLite.

Import is additive, transactional and versioned. Application history, contacts,
notes and CV data are rejected or stripped before they can reach public outputs.
"""
import argparse
import hashlib
import html
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from history_store import HistoryStore, canonical, norm, public_payload

@contextmanager
def writer_lock(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / '.monitor.lock'
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.close(fd)
        yield
    finally:
        path.unlink()


def setup(store):
    store.db.executescript('''
      CREATE TABLE IF NOT EXISTS source_imports (
        digest TEXT PRIMARY KEY, source TEXT NOT NULL, snapshot_at TEXT NOT NULL);
    ''')


def record_id(item):
    identity = canonical(item['url']) if item.get('url') else norm(item['company']) + '|' + norm(item.get('title', ''))
    return hashlib.sha256(identity.encode()).hexdigest()


def check_snapshot(snapshot):
    if snapshot.get('schema_version') != 1 or not snapshot.get('source_url'):
        raise ValueError('Expected versioned snapshot with source_url')
    stamp = datetime.fromisoformat(snapshot['snapshot_at'].replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('snapshot_at requires timezone')
    if snapshot.get('applications'):
        raise ValueError('Public snapshots must not contain application history')
    for group in ('companies', 'jobs'):
        seen = set()
        for item in snapshot[group]:
            if not item.get('company'):
                raise ValueError('Missing company')
            key = norm(item['company']) if group == 'companies' else record_id(item)
            if key in seen:
                raise ValueError('Duplicate ' + group + ': ' + item['company'])
            seen.add(key)
            if group == 'jobs':
                if not item.get('title') or item.get('status') not in {'LIVE', 'CLOSED', 'UNKNOWN'}:
                    raise ValueError('Invalid vacancy')
                canonical(item['url'])
    return stamp.astimezone(timezone.utc).isoformat()


def import_snapshot(store, snapshot):
    stamp = check_snapshot(snapshot)
    setup(store)
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if store.db.execute('SELECT 1 FROM source_imports WHERE digest=?', (digest,)).fetchone():
        return False
    with store.db:
        existing_companies = {norm(c['company']): c for c in store.export_companies()}
        for item in snapshot['companies']:
            old = existing_companies.get(norm(item['company']), {})
            if old.get('source_updated_at', '') > stamp:
                continue
            merged = dict(old, **item, source_updated_at=stamp, source_url=snapshot['source_url'])
            # The old single-company bootstrap example must not survive as truth.
            merged.pop('open_roles', None)
            store.upsert_companies([merged], stamp)
        for item in snapshot['jobs']:
            current = next((r for r in store.db.execute('SELECT * FROM jobs')
                            if canonical(r['url']) == canonical(item['url'])), None)
            previous = public_payload(json.loads(current['payload'])) if current else {}
            if previous.get('source_updated_at', '') > stamp:
                continue
            merged = public_payload(dict(previous, **item, source_updated_at=stamp, source_url=snapshot['source_url']))
            merged.setdefault('found_date', '')
            if current:
                observed_at = previous.get('evidence', {}).get('checked_at', '')
                if observed_at > stamp:
                    merged['status'] = current['current_status']
                store.db.execute('UPDATE jobs SET title=?,current_status=?,payload=? WHERE job_key=?',
                                 (merged['title'], merged['status'], json.dumps(merged, ensure_ascii=False), current['job_key']))
            else:
                store._save_job(merged, {}, stamp, imported=True)
        store.db.execute('INSERT INTO source_imports VALUES(?,?,?)', (digest, snapshot['source_url'], stamp))
        store.db.execute("INSERT OR IGNORE INTO metadata VALUES('bootstrap','1')")
    return True


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(text, encoding='utf-8')
    temp.replace(path)


def export_all(store, root):
    root = Path(root)
    setup(store)
    if store.db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('SQLite integrity check failed')
    companies, jobs = store.export_companies(), store.export_jobs()
    imports = [dict(r) for r in store.db.execute('SELECT * FROM source_imports ORDER BY snapshot_at')]
    tracker = store.export_tracker()
    latest = tracker.get('check_history', [])[-1:] or [{}]
    summary = {
        'companies_total': len(companies),
        'companies_france_explicit': sum(c.get('geography') == 'france' for c in companies),
        'companies_remote_paris': sum(c.get('geography') == 'remote_paris' for c in companies),
        'companies_other': sum(c.get('geography') == 'other' for c in companies),
        'shortlist_total': sum(c.get('list') != 'backlog' for c in companies),
        'backlog_total': sum(c.get('list') == 'backlog' for c in companies),
        'vacancy_records': len(jobs) + sum(len(c.get('unlinked_roles', [])) for c in companies),
        'live_vacancies': sum(j['status'] == 'LIVE' for j in jobs),
        'unknown_vacancies': sum(j['status'] == 'UNKNOWN' for j in jobs),
        'private_application_history': 'not_published',
        'latest_monitoring': {k: latest[0].get(k) for k in ('checked_at', 'mode', 'status', 'pages_checked', 'query_count')},
        'imports': imports,
    }
    views = {'jobs.json': jobs, 'companies-shortlist.json': [c for c in companies if c.get('list') != 'backlog'],
             'companies-backlog.json': [c for c in companies if c.get('list') == 'backlog'],
             'job-tracking.json': tracker, 'pipeline-summary.json': summary}
    for name, value in views.items():
        atomic_text(root / 'data' / name, json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    dashboard = render_dashboard(companies, jobs, summary)
    atomic_text(root / 'dashboard/index.html', dashboard)
    atomic_text(root / 'reports/pipeline.html', dashboard)
    label = 'MOCK: synthetic test data.' if summary['latest_monitoring'].get('mode') == 'mock' else 'Generated from SQLite; imported facts retain their source.'
    atomic_text(root / 'reports/pipeline.md', '# Current pipeline\n\n' + label + '\n\n' +
                '\n'.join('- {}: {}'.format(k, v) for k, v in summary.items() if k not in ('imports', 'latest_monitoring')) +
                '\n\nMonitoring: ' + json.dumps(summary['latest_monitoring']) + '\n')
    return summary


def render_dashboard(companies, jobs, summary):
    esc = lambda x: html.escape(str(x if x is not None else ''))
    def link(url):
        if not url:
            return '—'
        try:
            url = canonical(url)
        except ValueError:
            return '—'
        return '<a href="{}" rel="noreferrer">Источник</a>'.format(esc(url))
    def table(headers, rows, key):
        return '<div class="scroll"><table id="{}"><thead><tr>{}</tr></thead><tbody>{}</tbody></table></div>'.format(
            key, ''.join('<th>'+esc(h)+'</th>' for h in headers),
            ''.join('<tr>'+''.join('<td>'+c+'</td>' for c in r)+'</tr>' for r in rows))
    job_rows = [[esc(j['company']), esc(j['title']), esc(j['status']),
                 esc(j.get('evidence', {}).get('reason', 'Source snapshot; not live-verified')),
                 link(j['url'])] for j in jobs]
    for c in companies:
        for j in c.get('unlinked_roles', []):
            job_rows.append([esc(c['company']), esc(j['title']), esc(j['status']), 'No direct URL', '—'])
    company_rows = [[esc(c['company']), esc(c.get('city')), esc(c.get('geography', 'unknown')), esc(c.get('track')),
                     esc(c.get('list')), esc(c.get('priority')), esc(c.get('monitoring_status', 'unknown'))] for c in companies]
    metrics = [('Компаний всего', summary['companies_total']), ('Явно France', summary['companies_france_explicit']),
               ('Remote / Paris', summary['companies_remote_paris']), ('Другие страны', summary['companies_other']),
               ('Шорт-лист всего', summary['shortlist_total']), ('Резерв', summary['backlog_total']),
               ('Открытые вакансии', summary['live_vacancies']), ('Неясный статус', summary['unknown_vacancies'])]
    provenance = ''.join('<li>'+link(s['source'])+' · '+esc(s['snapshot_at'])+'</li>' for s in summary['imports'])
    return '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job Search · Pipeline</title><style>
body{font:16px system-ui;background:#f5f3ec;color:#173d35;margin:auto;max-width:1440px;padding:24px}h1{font:42px Georgia}h2{margin-top:36px}.metrics{display:flex;gap:12px;flex-wrap:wrap}.metric{background:white;padding:16px;border-radius:8px}.metric b{display:block;font-size:28px}.scroll{overflow:auto}table{border-collapse:collapse;background:white;width:100%;font-size:14px}th,td{padding:12px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top;min-width:100px}input{padding:12px;width:min(90%,480px);font:inherit;margin:20px 0}a{color:#176451}[hidden]{display:none!important}</style>
<h1>Поиск работы · публичный мониторинг</h1><p>Автоматически сформировано из SQLite. Здесь публикуются только компании, вакансии и результаты мониторинга.
История откликов и резюме намеренно хранятся отдельно и не публикуются. Импортированные статусы не означают новую live-проверку.</p>
<div class="metrics">'''+''.join('<div class="metric"><b>'+esc(v)+'</b>'+esc(k)+'</div>' for k,v in metrics)+'''</div>
<p>Последняя проверка монитором: '''+esc(json.dumps(summary['latest_monitoring'], ensure_ascii=False))+'''</p>
<label>Поиск по всем таблицам <input id="search" type="search" placeholder="Компания, роль, статус…"></label>
<h2>Вакансии</h2>'''+table(['Компания','Роль','Статус','Проверка','Ссылка'],job_rows,'jobs')+'''
<h2>Компании</h2>'''+table(['Компания','Локация из источника','География','Трек','Список','Приоритет','Мониторинг'],company_rows,'companies')+'''
<h2>Источники импортов</h2><ul>'''+provenance+'''</ul><p>Заявки автоматически не отправляются. Этот файл обновляется при импорте и запуске мониторинга.</p>
<script>document.getElementById('search').addEventListener('input',function(){const q=this.value.toLocaleLowerCase();document.querySelectorAll('tbody tr').forEach(r=>r.hidden=!r.textContent.toLocaleLowerCase().includes(q));});</script></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--import-file', type=Path)
    args = parser.parse_args()
    with writer_lock(args.root), HistoryStore(args.root / 'data/job-search.db') as store:
        if args.import_file:
            import_snapshot(store, json.loads(args.import_file.read_text(encoding='utf-8')))
        print(json.dumps(export_all(store, args.root), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
