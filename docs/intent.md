# Intent: Job Search Automation — Paris 2026

## Metadata

Created: 2026-10-30
Owner: Faina Sokolova (fsokolova.fr@gmail.com)
Related project: fsokolovafr-glitch/job-search-paris
Related files: docs/spec.md, docs/plan.md, docs/search-strategy.md, docs/target-roles.md

---

## 1. Problem

Поиск работы в Париже ведётся вручную: мониторинг вакансий разрозненный,
CV адаптируется без системы, статус по каждой компании хранится в голове
или теряется между сессиями. Каждый новый разговор с AI начинается
с переписки, а не с зафиксированного состояния задачи.

---

## 2. Goal

Найти и подписать контракт на роль в маркетинге во Франции.
Целевая дата выхода на работу — январь 2027.

---

## 3. Success Criteria

- Получен оффер на конкретную роль из целевого списка (см. раздел Roles).
- Контракт подписан.
- Первый рабочий день согласован.

Поиск считается завершённым в момент подписания контракта.

---

## 4. Users / Stakeholders

- Faina Sokolova — единственный пользователь системы.
- Claude (AI-агент) — исполнитель задач мониторинга, адаптации CV, обновления дашборда.

---

## 5. Current State

Что работает:
- Master CV в проекте Claude (один источник правды, версии GROWTH и OPS из него).
- Дашборд «Job Search Pipeline» как артефакт в Claude (три таблицы: подать / все компании / тесты).
- Scheduled Task мониторинга вакансий (сейчас на паузе).
- Скрапер fsokolovafr-glitch/job-scrapper-paris (приватный форк, адаптирован под Францию).
- Два email-ящика для откликов: fsokolova.fr@gmail.com и fa.sokolova@gmail.com.

Что не работает:
- Нет единого документа Intent/Spec/Plan — каждая сессия начинается с нуля.
- Мониторинг стоит на паузе с конца сентября.
- Скрапер искал по всем сайтам — слишком широко, много шума.

---

## 6. Desired State

Система автоматически:
1. Мониторит новые вакансии на трёх платформах: LinkedIn, Indeed, Welcome to the Jungle.
2. Фильтрует по целевым ролям и критериям (без обязательного французского, уровень Senior+).
3. Результаты появляются в дашборде без ручного ввода.
4. CV адаптируется под конкретную вакансию по запросу (на основе Master CV).

Человек делает вручную:
- Финальное решение — подавать или нет.
- Подготовку к интервью.
- Переговоры об оффере.

---

## 7. Systems Affected

| Система | Роль |
|---|---|
| LinkedIn | Источник вакансий #1 |
| Indeed | Источник вакансий #2 |
| Welcome to the Jungle | Источник вакансий #3 |
| job-scrapper-paris (GitHub, private) | Скрапер вакансий |
| Job Search Pipeline (Claude artifact) | Дашборд статусов |
| Master CV (Claude project doc) | Источник правды для CV |
| Gmail (fsokolova.fr + fa.sokolova) | Входящие отклики и отказы |
| Scheduled Task (Claude) | Запуск мониторинга каждые 2 дня |

---

## 8. Constraints

- Французский язык не является обязательным требованием (уровень B1/B2).
- Зарплата от €50K gross (жёсткого минимума нет — устроиться важнее).
- Предпочтительный формат: remote или hybrid.
- Поиск только на трёх платформах: LinkedIn, Indeed, Welcome to the Jungle.
  Другие агрегаторы — не мониторить (слишком много шума).
- Нельзя отправлять заявки автоматически — только с одобрения пользователя.

---

## 9. Non-goals

- Поиск по всем сайтам с вакансиями (был реализован ранее — отменён).
- Автоматическая подача заявок без участия пользователя.
- Поиск ролей вне маркетинга.
- Сбор вакансий для других людей.

---

## 10. Known Decisions

- Два вектора поиска:

  **Трек 1 — Marketing Ops / Automation (уклон в AI-воркфлоу)**
  Позиционирование: строю системы, на которых работает маркетинг: CRM, автоматизации, AI-агенты и воркфлоу.
  Названия для поиска: Marketing Operations Manager, Marketing Automation Manager, Growth Operations Manager, Marketing Ops Specialist, GTM Engineer (эксперимент).
  Подкрепляем: OneCRM, Dynamics 365, Pardot, Salesforce, HubSpot; проекты AI Operating System, DERMATHEY, автоматизация поиска вакансий.

  **Трек 2 — Growth & Demand Gen (приоритет)**
  Позиционирование: запускаю рост с нуля — от продукта и маржи до каналов и трекинга.
  Названия для поиска: Growth Marketing Manager, Demand Generation Manager, Field/Regional Marketing Manager, Growth & Acquisition Manager.
  Подкрепляем: Levenhuk (ROI 3.4, лучший квартал региона за 4 года), вебинар Leica (1000+ участников, +95% к цели), Meta Ads + Google Ads Search (сертификат фев 2025).
- Система треков компаний (как в артефакте Company Wishlist):
  - **Track A** — основной: B2B SaaS, AI scale-ups, HealthTech, FinTech, Cyber, HRTech и другие tech-компании (Stripe, Deel, Hack The Box, Riot, Alan, Canva, Probabl и т.д.)
  - **Track B** — вторичный: FMCG, Retail, e-commerce (HelloFresh, Raja France, Veepee, ManoMano и т.д.)
  - **Track C** — крупный консалтинг и агентства (Capgemini, Accenture, Veeam, NielsenIQ, OMD и т.д.)
- CV существует в двух версиях (GROWTH и OPS), собираются из Master CV.
- Все отклики идут через fa.sokolova@gmail.com (Easy Apply на LinkedIn).
- Company Wishlist живёт как артефакт в Claude (не в .md файле проекта).

---

## 11. Open Questions

- Когда возобновляется Scheduled Task мониторинга?
- Скрапер job-scrapper-paris: текущий статус — работает или требует доработки?
- Company Wishlist.md в проекте пустой — подтверждено: реальный список компаний живёт в артефакте Claude (Company Wishlist artifact). Файл .md можно удалить или оставить как указатель.
- Дашборд: нужно ли его обновить с учётом батча вакансий от 26.10?

---

## 12. Related Files

| Файл | Где | Описание |
|---|---|---|
| docs/search-strategy.md | GitHub | Стратегия поиска (Track A/B/C) |
| docs/target-roles.md | GitHub | Целевые роли и компании |
| docs/monitoring-rules.md | GitHub | Правила мониторинга вакансий |
| Master CV | Claude project | Мастер-резюме со всеми буллетами |
| Job Search Pipeline | Claude artifact | Дашборд статусов |
| claude/Vacancies_Batch_2026-10-26.md | Claude project | Последний батч вакансий |
