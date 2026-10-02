# Job Search Paris

Публичный мониторинг вакансий: проверенный снимок → SQLite → дашборд, JSON и отчёты.
Python 3.9+, стандартная библиотека. История откликов, интервью, отказов, контакты и CV
намеренно не публикуются.

## Что находится в публичной базе

Снимок на 30 сентября 2026 содержит 153 компании и 38 записей возможностей
(37 со ссылками и одну роль без прямой ссылки). Это сведения источника, а не новая
live-проверка. Текущие числа всегда берутся из [pipeline-summary.json](data/pipeline-summary.json).

- `data/job-search.db` — компании, вакансии, события и запуски мониторинга.
- `sources/artifact-*.json` — очищенные снимки без заявок, контактов и заметок.
- [dashboard/index.html](dashboard/index.html) и [reports/pipeline.md](reports/pipeline.md)
  автоматически строятся из SQLite.
- `private/applications.json` — локальный приватный трекер; Git его игнорирует.

Публичная база не отвечает на вопрос «подавалась ли я раньше». Если приватный трекер
недоступен, правильный ответ — `UNKNOWN`, а не «нет».

## Импорт и приватизация

Сначала очистите приватный экспорт, храня исходник вне репозитория:

```sh
python3 scripts/sanitize_public_snapshot.py /private/path/reviewed.json sources/artifact-YYYY-MM-DD.json
python3 scripts/pipeline.py --import-file sources/artifact-YYYY-MM-DD.json
python3 scripts/update-tracker.py
```

Публичный импорт отклонит снимок с непустым `applications`. Дополнительно SQLite
удаляет поля откликов, контактов, заметок и вариантов CV из импортируемых payload.

## Приватный трекер откликов

Проверка перед подготовкой документов:

```sh
python3 scripts/private_applications.py check --url 'https://example.com/jobs/123'
```

Если файла ещё нет, команда возвращает `UNKNOWN / private_tracker_missing`. Пока
исторический импорт не подтверждён полным, пустой результат также остаётся
`UNKNOWN / private_tracker_incomplete`. Записывать событие можно только после
подтверждения, что оно действительно произошло:

```sh
python3 scripts/private_applications.py record \
  --company 'Example' --title 'Marketing Manager' \
  --url 'https://example.com/jobs/123' --status applied --date 2026-10-02 \
  --confirm-recorded-action
```

Для другого расположения задайте `JOB_APPLICATIONS_FILE`. Пример структуры без реальных
данных: `private.example/applications.example.json`.

## Автоматический мониторинг

GitHub Actions **Monitor jobs** запускается по расписанию и вручную. Переменная
`MONITOR_ENABLED=false` — явная пауза; любое другое значение включает задачу.
Workflow импортирует очищенные источники, проверяет известные ссылки, обновляет SQLite,
дашборд и отчёты и коммитит только публичные результаты.

Без `BRAVE_SEARCH_API_KEY` режим `auto` продолжает проверять известные ссылки, но поиск
новых вакансий имеет статус `missing_api_key / partial`. Это ограничение показывается в
отчёте, а `UNKNOWN` никогда не превращается в `CLOSED`. Ключ добавляется только как
GitHub Actions secret и не должен появляться в файлах или сообщениях.

```sh
python3 scripts/check-jobs.py --mode live --discovery auto
python3 scripts/job-analytics.py --trend 30d
```

Аналитика открывает SQLite в режиме read-only и не создаёт таблицы или миграции.
Заявки и сообщения автоматически не отправляются.

## Инструкции для агентов

Codex читает `AGENTS.md`. Сценарий отклика находится в
`.claude/skills/job-application/SKILL.md` и применим к Claude и Codex без обязательного
`project_read`.

Master CV рекомендуется хранить в отдельном приватном Obsidian Vault, не внутри
публичного репозитория. Локальный путь задаётся переменной `JOB_MASTER_CV`; пример есть
в `.env.example`. Настоящий абсолютный путь, `.env`, содержимое Vault и каталог
`.obsidian` не публикуются. Если файл недоступен, результат остаётся `UNKNOWN`.

## Проверка

```sh
python3 -m unittest discover -s tests -v
python3 scripts/check-jobs.py --mode mock --output work/demo
python3 scripts/check-jobs.py --mode mock --output work/demo
python3 scripts/update-tracker.py --root work/demo
python3 scripts/job-analytics.py --db work/demo/data/job-search.db --trend 30d
```

Полные критерии — в [docs/acceptance.md](docs/acceptance.md). Успешные локальные тесты
не подтверждают удалённый запуск GitHub Actions, наличие ключа поиска или live-доступность
вакансий; это проверяется отдельно в GitHub.
