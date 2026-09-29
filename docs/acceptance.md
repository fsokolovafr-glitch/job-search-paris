# Проверка результата

- Unit tests: `python3 -m unittest discover -s tests -v`.
- Покрытие SQLite: `python3 -m coverage run --source=scripts -m unittest discover -s tests`;
  `python3 -m coverage report --include='*/history_store.py' --fail-under=90`.
- Mock: `python3 scripts/check-jobs.py --mode mock --output work/demo` дважды.
- Валидация: `python3 scripts/update-tracker.py --root work/demo`.
- Аналитика: `python3 scripts/job-analytics.py --db work/demo/data/job-search.db --trend 30d`.

Проверки включают Unicode, tracking URL, close/reopen, пропущенные проверки,
UNKNOWN, отсутствие ложных обновлений, rollback, однократный импорт и статусы заявок.
CI выполняет проверки на Python 3.9, 3.11, 3.13. Текущий результат смотрите в Actions.

Успешные тесты не подтверждают live-доступность площадок и полноту поиска.
