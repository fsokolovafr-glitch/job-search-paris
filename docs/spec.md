# spec.md — Спецификация системы поиска работы

> **Уровень**: что именно должно работать (не зачем и не как именно реализовано)  
> **Связанные файлы**: `docs/intent.md` (зачем), `docs/plan.md` (как)  
> **Последнее обновление**: 2026-10-30

---

## 1. Мониторинг вакансий

### 1.1 Источники
Только три источника, больше не добавлять:
- LinkedIn Jobs
- Indeed France
- Welcome to the Jungle

### 1.2 Поисковые треки

**Трек 1 — Marketing Ops / Automation**
- Ключевые слова: `Marketing Operations`, `Marketing Automation`, `RevOps`, `HubSpot`, `Marketo`, `CRM Marketing`, `MOps`
- Уточнители: `B2B`, `SaaS`, `technology`
- Локация: Paris, France (+ remote FR)
- Формат контракта: CDI, иногда freelance

**Трек 2 — Growth / Demand Generation**
- Ключевые слова: `Demand Generation`, `Growth Marketing`, `Performance Marketing`, `Lead Generation`, `ABM`
- Уточнители: `B2B`, `SaaS`, `tech`, `scale-up`
- Локация: Paris, France (+ remote FR)
- Формат контракта: CDI, иногда freelance

### 1.3 Фильтры исключения
- Вакансии без указания зарплаты, если это явно junior-уровень
- Агентства (если не указано как клиентская сторона)
- Вакансии на русском / английском без французского рынка
- Стажировки и CDD < 12 месяцев

### 1.4 Периодичность
- Частота: еженедельно (пауза с конца сентября — нужно перезапустить Scheduled Task)
- Формат вывода: список вакансий с полями: название, компания, источник, ссылка, дата публикации, трек (1 или 2), пометка (new / reviewed / applied)

---

## 2. Дашборд (Job Search Pipeline)

### 2.1 Что это
HTML-артефакт в Claude: https://claude.ai/artifact/JUZYFXoraZXqr2QESQ8F2v  
*(примечание: это Company Wishlist — уточнить URL дашборда Pipeline отдельно)*

### 2.2 Таблица компаний (Company Wishlist)
Поля:
- Название компании
- Трек A / B / C (тип компании):
  - **A** = main B2B SaaS/tech
  - **B** = FMCG/retail
  - **C** = large consulting/agencies
- Трек поиска (1 или 2 — тип роли)
- Статус: `To explore` / `Found` / `Applied` / `Interviewing` / `Offer` / `Rejected` / `On hold`
- Ссылка на LinkedIn компании
- Комментарий

### 2.3 Что обновляется вручную
- Статус компании после каждого действия (отклик, ответ, отказ)
- Добавление новых компаний из мониторинга

### 2.4 Что обновляется автоматически (цель)
- Список новых вакансий из мониторинга → колонка "Found"
- Счётчики по статусам (pipeline overview)

---

## 3. CV-адаптация

### 3.1 Вход
- Текст или ссылка на вакансию
- Трек (1 или 2) — определяется автоматически по ключевым словам

### 3.2 Источник
- Master CV: `docs/Master_CV_Faina_Sokolova.md` (в Claude проекте)
- Выбор трека из `Master_CV_Tracks.md` — там прописаны версии под каждую роль

### 3.3 Выход
- Адаптированное CV в формате `.docx` или `.pdf`
- Ключевые изменения: приоритет достижений, переформулировка под JD, выделение релевантных инструментов
- Cover letter (опционально, по запросу)

### 3.4 Ограничения
- CV не коммитится в GitHub (per AGENTS.md)
- Хранится только в `private/` или отдельной папке вне репо

---

## 4. Трекинг откликов

### 4.1 Где хранится
- Файл: `private/applications.json` (gitignored)
- Дублируется вручную в дашборд (Company Wishlist)

### 4.2 Поля записи
```json
{
  "company": "Название",
  "role": "Название роли",
  "track": 1,
  "source": "LinkedIn | Indeed | WTTJ",
  "applied_date": "YYYY-MM-DD",
  "status": "Applied | Screening | Interview | Offer | Rejected | No response",
  "cv_version": "filename.pdf",
  "notes": "Свободное поле"
}
```

### 4.3 Воронка (ожидаемые переходы)
```
Applied → No response (5+ дней без ответа)
Applied → Screening (ответ HR)
Screening → Interview
Interview → Offer / Rejected
```

### 4.4 Напоминания
- Если статус `Applied` и прошло 7+ дней → флаг "нужен follow-up"
- Если статус `Interview` → дата следующего шага обязательна

---

## 5. Открытые вопросы по спецификации

- [ ] Scheduled Task для мониторинга вакансий — когда перезапускаем?
- [ ] Company Wishlist: пустой `.md` в Claude-проекте — удалить или оставить как указатель на артефакт?
- [ ] Дашборд Pipeline (HTML артефакт) — уточнить ссылку и синхронизацию с Company Wishlist
- [ ] Формат экспорта CV — `.docx` через skill `docx`, или только PDF?
- [ ] Автоматизация follow-up напоминаний — через Make.com или только вручную?
