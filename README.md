# job-search-paris

Мониторинг маркетинговых вакансий во Франции для Krolik: demand generation,
field marketing и partner marketing. Python 3.9+, только стандартная библиотека.

**Состояние: подготовлен и локально протестирован. Репозиторий:
[fsokolovafr-glitch/job-search-paris](https://github.com/fsokolovafr-glitch/job-search-paris), Private.
Реальный мониторинг ещё не выполнен.** В shortlist есть только пример LumApps из задания.
Его LIVE — сообщение источника, не результат нашей проверки: рабочий статус UNKNOWN.
Не импортированы остальные 43 компании shortlist и backlog. Заявленные треки
89 + 6 + 26 дают 121, хотя общий итог указан как 120. См. `data/source-manifest.json`.

## Структура

```text
.github/workflows/
  monitor-jobs.yml         # расписание, live-проверка, commit/push
  tests.yml               # проверки Python 3.9 / 3.11 / 3.13
 data/
  companies-shortlist.json
  companies-backlog.json  # не участвует в автоматических проверках
  job-tracking.json       # история настоящих запусков, пока пустая
  applications-log.csv    # журнал заявок, пока только заголовок
  config.json             # страна, ключевые слова, глубина поиска
  source-manifest.json    # происхождение, пробелы, конфликт численности
 docs/
  monitoring-rules.md
  target-roles.md
  search-strategy.md
  acceptance.md
 scripts/
  check-jobs.py            # точка входа; обновляет трекер и отчёты
  parse-jobs.py            # извлечение JobPosting из сохранённого HTML
  update-tracker.py        # проверка согласованности трекера и отчётов
  monitor.py              # общий код поиска, сверки и сохранения
 reports/
  2026-09-28-report.md     # исходный статус, не выдуманный мониторинг
 tests/
  test_monitor.py
  fixtures/demo.json      # только искусственные компании и страницы
```

## Быстрый запуск

Команды выполняются из корня репозитория. Пакеты устанавливать не требуется.

```sh
python3 -m unittest discover -s tests -v
python3 scripts/check-jobs.py --mode mock --output ./work/demo
python3 scripts/update-tracker.py --root ./work/demo
```

Mock использует `tests/fixtures/demo.json` и отдельную папку: один новый результат,
одна закрытая ссылка и один UNKNOWN/403. Это не сведения о реальном рынке труда.
Повторный mock CLI запускает исходный сценарий заново. Тест повторной обработки
обновлённого состояния проверяет отсутствие дубликатов вакансий и событий.

Для реального поиска нужен ключ **Brave Search API** в переменной окружения
`BRAVE_SEARCH_API_KEY`. Ключ не хранится в Git. Скрипт завершится без обновления
данных, если ключ отсутствует. До включения заполните shortlist.

```sh
# Перед запуском задайте BRAVE_SEARCH_API_KEY в окружении безопасным способом.
python3 scripts/check-jobs.py --mode live --dry-run
python3 scripts/check-jobs.py --mode live
python3 scripts/update-tracker.py
python3 scripts/parse-jobs.py path/to/saved-job-page.html
```

`--dry-run` делает запросы и печатает отчёт, но не меняет JSON и отчёты.
Live-режим: WebSearch через Brave → GET страницы → JSON-LD JobPosting →
сопоставление работодателя, роли и страны → сохранение наблюдения и истории.
Он не использует инструменты ChatGPT внутри GitHub Actions.

## GitHub Actions

1. Опубликовать файлы в default branch выбранного `job-search-paris`.
2. Добавить Actions secret `BRAVE_SEARCH_API_KEY` в Settings → Secrets and variables → Actions.
3. После импорта списка добавить Actions variable `MONITOR_ENABLED` со значением `true`.
4. Запустить `Monitor jobs` вручную и проверить отчёт и commit.
5. Проверить разрешения Actions на запись contents и совместимость правил защиты ветки.

Workflow использует встроенный `GITHUB_TOKEN`; PAT для коммитов не нужен.
До включения переменной job пропускается. Запрошенное расписание
`0 10 */2 * *` означает 10:00 UTC в нечётные дни месяца: между 31-м и 1-м
возможен интервал в сутки. Это не гарантия запуска каждые 48 часов.
В Париже это обычно 11:00 зимой / 12:00 летом. GitHub может задерживать запуски.
Если нужен строгий ритм раз в два календарных дня, согласуйте замену расписания.
Отдельная автоматизация Codex не создавалась: расписание находится в Actions.

Workflow последовательно выполняет тесты, мониторинг, валидацию и commit/push JSON
и отчётов. При конфликте push не перезаписывает чужую историю: сохраняет recovery
artifact и завершает запуск ошибкой. При UNKNOWN отчёт сначала сохраняется в Git,
затем запуск отмечается неуспешным для внимания владельца. Повторные ручные запуски
сериализуются через concurrency; локально используется `.monitor.lock`.
Если процесс был аварийно завершён, перед удалением lock убедитесь, что он не работает.

## Добавление компании

Добавьте объект в `data/companies-shortlist.json`:

```json
{
  "company": "Exact employer name",
  "aliases": [],
  "track": "C",
  "priority": "High",
  "city": "Paris",
  "employee_count": null,
  "monitoring_status": "checked",
  "open_roles": [],
  "last_checked": null,
  "contact_status": "not_applied"
}
```

`checked` включает поиск новых ролей; `found_not_applied` проверяет сохранённые
вакансии. Для A/B с `employee_count >= 1000` поиск также включён.
Численность записывайте только по источнику, при неизвестной используйте `null`.
`paused` и `rejected` выключают компанию. Список backlog не читается монитором.
Для каждой известной вакансии нужны `title`, `url`, `status`, `found_date`, `applied`.
После подачи заявки вручную поставьте `applied: true` и добавьте строку в CSV;
после отказа — `application_status: "rejected"` у вакансии либо `contact_status:
"rejected"` у компании, если исключается вся компания. CSV — ручной журнал,
не источник автоматической синхронизации статусов.

Проверяйте JSON командой `python3 scripts/update-tracker.py` перед commit.
`aliases` нужны для точного сопоставления юридического/брендового имени работодателя.

## Другие страны и ограничения

Измените `country`, ISO-код `country_code`, `country_aliases`, `role_keywords`,
`excluded_keywords`, `search_terms` в `data/config.json` и соответствующие docs.
По умолчанию стажировки и alternance исключены как явное рабочее предположение;
уровень должности, зарплата, тип договора и язык пока не заданы.

Поиск ограничен `search_pages` страницами на запрос (по умолчанию 1, по 20 результатов).
Это мониторинг обнаруженных страниц, не полный обход всех ATS.
JS-only сайты, блокировки, редиректы, отсутствие/неоднозначность JobPosting и неизвестная
география дают UNKNOWN. Remote без явно указанной подходящей страны требует ручной проверки.
LIVE означает наличие подходящего структурированного объявления в момент проверки,
а не гарантию, что работодатель примет заявку. Неизменившаяся выдача не доказывает
отсутствия новых вакансий. Каждый отчёт содержит границы фактической проверки.

Владелец эксплуатации и приёмки: Krolik. Критерии и выполненные проверки —
[docs/acceptance.md](docs/acceptance.md).

Источники интерфейсов: [GitHub schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule),
[Brave Web Search API](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started).
Архитектурная основа: текущий [AI-Project-Template/main](https://github.com/fsokolovafr-glitch/AI-Project-Template/tree/main), прочитан 2026-09-28.
