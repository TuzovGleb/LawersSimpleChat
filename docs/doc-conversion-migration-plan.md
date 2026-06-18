# Перенос конвертации документов из Next.js в Python-бэкенд

**Цель:** перенести извлечение текста/конвертацию загружаемых файлов (pdf, docx, doc, txt/md, картинки) из Next.js в Python/LangGraph-бэкенд, чтобы:
1. LLM-вызовы конвертации были видны в **LangSmith**;
2. весь путь был обвязан **структурными логами** с корреляцией `request_id`/`chat_id`.

**Оценка:** Medium, **~8–9 рабочих дней** (после снятия главного риска — см. ниже).

## Статус реализации (ветка feature/python-langgraph-backend-search)

- ✅ Этап 0 — спайк `type:'file'` (риск снят)
- ✅ Этап 1 — S3-клиент в бэке (`5d8bcf3`)
- ✅ Этап 2–3 — зависимости + нативные парсеры + диспетчер (`b665056`)
- ✅ Этап 4 — LLM/vision через ChatOpenAI (`bd2e449`)
- ✅ Этап 5 — `POST /documents/extract` + запись в Supabase (`9c4b273`)
- ✅ Этап 6 — Next в прокси (`9ef3fec`)
- ✅ Этап 7 — родительский LangSmith-trace + логи (`d4fe1b5`)
- ✅ Этап 8 (зачистка кода) — удалён мёртвый Next-код извлечения (`e1d1d4e`)
- ⏳ Этап 8 (валидация) — **остаётся**: прокатить стейдж, нагрузить сканами (OOM/таймаут), глазами проверить вложенность трейсов в LangSmith.

Тесты бэка: 71 passed, 1 skipped (antiword не стоит локально). tsc фронта чистый.

---

## 0. Результат де-рискующего спайка (сделано)

`backend/experiments/spike_typefile/spike.py` доказал: **`langchain-openai==1.2.2` `ChatOpenAI` прокидывает OpenRouter-блок `type:'file'` (PDF `file_data`) в провайдер дословно** — Gemini 3.5 Flash распарсил PDF при вызове через `ChatOpenAI`, идентично сырому `openai`-SDK.

Следствия:
- Постраничный OCR сканов **не требует** растеризации в картинки → **не нужны** `pdf2image`/`poppler`/системные apt-пакеты, образ остаётся слим.
- Парсеры pdf/docx — чистый Python (`pypdf`/`python-docx`), без apt-добавок (Dockerfile ставит только `curl`+`ca-certificates`).
- Все вызовы извлечения идут через `ChatOpenAI` → авто-трейс в LangSmith без декораторов.

---

## 1. Архитектурное решение

- **Бэкенд сам читает файл из S3** (не Next пересылает байты). Живой роут `app/api/projects/[projectId]/documents/route.ts` **уже** принимает `{objectKey, filename, mimeType, size, userId}` и качает из S3 сам — переносим только `downloadFromS3 → extractText → insert`, Next становится тонким прокси (как уже сделано для чата).
- **Синхронный** эндпоинт (request/response JSON), не стриминг: у извлечения нет инкрементального UI-смысла, а контейнер уже терпит долгие вызовы (`execution-timeout 1800s`, `ChatOpenAI timeout=1800`).
- **Бэкенд — единственный писатель** строки `project_documents`. Next больше НЕ инсертит (иначе дубли).
- Новый эндпоинт: `POST /documents/extract` в `backend/app/server/main.py`, защищён `Depends(verify_backend_secret)` (как чат-роуты, `security.py:9`).

### Контракт эндпоинта
```
POST /documents/extract            (X-Backend-Secret, X-Request-Id, [X-Chat-Id])
body: { objectKey, filename, mimeType, size, projectId, documentId? }
flow: download S3 -> dispatch extract -> reject empty (422)
      -> upsert project_documents (sole writer, dedup by object_key)
      -> touch projects.updated_at
resp: 201 { document: <camelCase, как mapProjectDocument> }
```

---

## 2. Предварительные решения (рекомендованные дефолты)

- [ ] **Модель экстракции:** `google/gemini-3.5-flash` (паритет с `lib/model-config.ts:124`). ⚠️ НЕ переиспользовать запись `gemini` из `chat.yaml` — там `gemini-2.5-flash`, это тихая подмена модели/качества OCR. Нужна **отдельная запись** в `chat.yaml`.
- [ ] **Fallback-модель:** отказаться от прямого OpenAI `gpt-4o-mini` (текущий fallback, `document-processing.ts:44`) — реестр OpenRouter-only. Стандартизируемся на OpenRouter, убираем dual-SDK.
- [x] **.doc (legacy Word):** парсим **нативно** (системные зависимости — ОК), напр. `antiword`/`textract` (+ apt-пакет в Dockerfile). ⚠️ Запускать в **отдельном процессе / вне event loop**: `asyncio.create_subprocess_exec` для antiword, `asyncio.to_thread` для CPU-bound парсеров — чтобы не блокировать FastAPI.
- [x] **LangSmith:** НЕ отдельный проект. Оборачиваем всё извлечение (включая постраничный fan-out) в **один родительский трейс** `langsmith.run_helpers.trace(...)` (или `@traceable` на оркестраторе) → все per-page `ChatOpenAI` вкладываются в ОДИН ран на документ = одно дерево трейса в общем проекте, без флуда. Вложенность — через contextvar-интероп langsmith↔langchain (проверить на langchain-core 1.4.1).
- [x] **Память бэка:** поднять память контейнера (1GB → 2GB+) в обоих воркфлоу.
- [ ] **Корреляция:** извлечение — самостоятельное действие над документом проекта → основная корреляция по `request_id`; `X-Chat-Id` опционально.
- [ ] **Backfill:** не нужен — старые строки сохраняют `text`. Парити-тесты только на свежих загрузках. Строки с `object_key=NULL` (до миграции `20250310000000`) неперезаливаемы.

---

## 3. Чеклист реализации

### Этап 1 — S3-клиент в бэке (greenfield, ~1–1.5д)
- [ ] Добавить `aioboto3` (или `boto3`) в `backend/pyproject.toml`, `uv sync`.
- [ ] Блок `s3` в `config/app.yaml`: `bucket` через `${env['S3_BUCKET_NAME']}`; `endpoint`/`region` — **хардкод-дефолты** `https://storage.yandexcloud.net` / `ru-central1` (как `lib/s3-client.ts:10-11`; этих секретов нигде нет). Path-style signing.
- [ ] Поднять S3-клиент один раз в `lifespan` (`main.py:41-64`), положить на `app.state` (как `SupabaseRepo`).
- [ ] Прокинуть `S3_BUCKET_NAME`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` в env **бэкенд-контейнера** в **ОБОИХ** воркфлоу (`deploy-yandex-cloud-staging-python.yml`, `deploy-yandex-cloud-prod-python.yml`) — сейчас они только в блоке фронт-контейнера.
- [ ] **Поднять `--memory` бэк-контейнера** (1GB → 2GB+) в обоих воркфлоу под память per-page OCR.

### Этап 2 — Python-зависимости извлечения (~0.5д)
- [ ] `pypdf` (текст + разрезание страниц) или `pdfplumber` (лучше слой, тяжелее); `python-docx` (или `docx2txt` ближе к `mammoth`).
- [ ] Подтвердить, что слим-Dockerfile **не требует** apt-добавок при выбранной стратегии (.doc → LLM, без `antiword`/`poppler`).

### Этап 3 — Диспетчер + нативные парсеры (~2д)
- [ ] ⚠️ **Все нативные парсеры (pdf/docx/doc) — CPU-bound/subprocess → выносить с event loop** (`asyncio.to_thread` для pypdf/python-docx, `asyncio.create_subprocess_exec` для antiword), иначе блокируют FastAPI при concurrency-10.
- [ ] Модуль извлечения, повторяющий роутинг `extractTextFromDocument` (`document-processing.ts:67-116`): `isPlainText/isDocx/isDoc/isPdf/isImage` по mime+расширению.
- [ ] txt/md: `bytes.decode('utf-8', errors='replace')` (паритет с наивным decode Next).
- [ ] PDF текстовый: сохранить порог `MIN_TEXT_LENGTH_FOR_SUCCESS=80` (`document-processing.ts:43,95`) для решения «нативный vs скан».
- [ ] DOCX: `python-docx`; пустой/ошибка → LLM-fallback (повторить empty-check gate). ⚠️ `python-docx` хуже тянет таблицы/колонтитулы — для юр-доков с таблицами рассмотреть `docx2txt`.
- [ ] Форма результата `ExtractedDocument {text, rawTextLength, truncated:false, strategy}`.

### Этап 4 — LLM/vision-пути через `rag_core.llm` (~2–3д)
- [ ] Отдельная запись модели в `chat.yaml` → `ChatModelRegistry` пред-собирает `ChatOpenAI` для `google/gemini-3.5-flash` (`temperature=0`, `max_tokens=16384`).
- [ ] `extractWithVision` (картинки): `image_url` base64 data-URI через `ChatOpenAI`.
- [ ] `extractWithFileAttachment` (PDF/картинка одним запросом): `type:'file'` base64.
- [ ] `extractPdfPerPage`: `pypdf` split → `asyncio.Semaphore(5)` + 2 ретрая/страница + **all-or-nothing** (как `document-processing.ts:325-396`); каждая страница — `ChatOpenAI` с `type:'file'`-блоком (спайк подтвердил passthrough).

### Этап 5 — Эндпоинт + запись в Supabase (~1.5д)
- [ ] `POST /documents/extract` в `main.py`, `Depends(verify_backend_secret)`.
- [ ] Расширить `SupabaseRepo` записью `project_documents` (service-role `AsyncClient`). Колонка называется **`text`** (НЕ `extracted_text`). Заполнить NOT NULL: `text`, `raw_text_length`, `strategy`, `size`, + `truncated`(default false), `object_key`, `mime_type`, `name`.
- [ ] **Дедуп по `object_key`:** upsert/select-existing перед insert (клиент ретраит maxRetries=1; долгое извлечение + abort на 180с → дубли/сироты).
- [ ] **Повторить `projects.updated_at` touch** (`route.ts:212-221`) — иначе тихо ломается сортировка проектов в UI (`chat-page-client.tsx:950`).
- [ ] Пустой текст → 422 (как текущий роут). **Surface-ить ошибки** наружу — НЕ копировать swallow-and-continue из чат-пути (`supabase_repo.py:293-294`).
- [ ] Вернуть **camelCase**-документ (`mimeType/rawTextLength/uploadedAt`), чтобы клиентский `normalizeDocument` (`chat-page-client.tsx:925`) работал без изменений.
- [ ] Харднуть авторизацию: `verify_backend_secret` fail-open при пустом секрете (`security.py:15-17`); прод его требует (`...prod-python.yml:161`), но staging/local — открыты. Явный guard или явно задокументировать.

### Этап 6 — Перевод Next в прокси (~0.5д)
- [ ] `app/api/projects/[projectId]/documents/route.ts`: убрать `downloadFileFromS3` + `extractTextFromDocument` + `INSERT`; вместо этого `POST {objectKey,...}` на `BACKEND_URL/documents/extract` с `X-Backend-Secret`/`X-Request-Id` (как чат-прокси).
- [ ] Next больше НЕ пишет строку (бэк — единственный писатель).
- [x] **Клиентский таймаут распознавания поднят 180с → 1800с** (`chat-page-client.tsx`, под предел контейнера) — сделано как первый шаг, чинит постоянные падения по таймауту. `maxRetries=1` оставлен (рассмотреть 0 — ретрай перезапускает тяжёлый OCR). **Долгосрочно:** уйти от фронт-таймаута — асинхронная job-модель с polling/SSE прогресса.
- [ ] `app/api/projects/[projectId]/documents/[documentId]/route.ts` (DELETE) остаётся в Next и продолжает чистить S3 → Next сохраняет S3-креды.

### Этап 7 — Трейсинг + логи (~1д, инфра уже есть)
- [ ] **Родительский трейс на весь документ:** `with langsmith.run_helpers.trace(name="document_extraction", metadata={document_id, project_id, request_id}) as rt:` (или `@traceable` на функции-оркестраторе). Все вложенные `ChatOpenAI`-вызовы (per-page, vision, file-attachment) авто-привязываются детьми → ОДНО дерево на документ, без флуда чат-проекта.
- [ ] Доп. метаданные на per-page child — `RunnableConfig(metadata={page_index})` (образец `chat_stream.py:94-104`).
- [ ] **Проверить вложенность** langsmith↔langchain на langchain-core 1.4.1 (contextvar-интероп). Если авто-вложение не сработает — пробросить parent run в `config` детям.
- [ ] Логгеры в неймспейсе `app.*` → `JSONFormatter`+`RequestContextFilter` авто-штампят `surface/chat_id/request_id` (эндпоинт в request-lifecycle, `RequestContextMiddleware` биндит из заголовков). Next должен форвардить `X-Request-Id`.
- [ ] INFO-брейкркрамбы: `download_start/done`(bytes, object_key), `strategy_selected`(strategy, mime), per-page progress(`page_index`, `total_pages`, `attempt`, `duration_ms`), completion(strategy, raw_text_length, total_duration_ms); ошибки — `logger.exception`. В каждый page-таск добавить `page_index` в `extra=` (contextvars копируются в дочерние таски, но индекс надо различать).

### Этап 8 — Валидация на serverless + зачистка (~1.5–2д)
- [ ] Нагрузка: многостраничные сканы + картинки против 1GB/conc-10/1800s. При OOM — снизить page-concurrency / поднять память.
- [ ] Golden-file diff корпуса реальных юр-доков (нативный PDF, скан, docx с таблицами, .doc, картинки) старый-vs-новый до катки. Порог 80 симв. оставить идентичным (граничные доки не должны перескакивать в дорогой OCR).
- [ ] Подтвердить: раны в LangSmith-проекте, логи коррелируют по `request_id` сквозь client→bff→backend.
- [ ] Удалить мёртвое: пути `lib/document-processing.ts` и legacy Edge-роут `app/api/documents/route.ts` (0 живых ссылок). Затем `openrouter-client.ts`/`model-config.ts`, если после переноса они тоже осиротеют.

---

## 4. Риски и митигации

| Риск | Митигация |
|---|---|
| ~~`type:'file'` не пройдёт через LangChain~~ | **СНЯТО спайком** — проходит дословно. |
| OOM на 1GB при параллельном per-page OCR | Поднять память до 2GB+ (решено); снизить `PDF_PAGE_CONCURRENCY`, стримить S3-download, освобождать буферы. Нагрузка до катки. |
| Нативные парсеры блокируют event loop (concurrency-10) | Все CPU-bound/subprocess-парсеры вне loop: `asyncio.to_thread` / `create_subprocess_exec`. |
| Двойные писатели `project_documents` → дубли | Бэк — единственный писатель; убрать INSERT из Next. |
| Дубли/сироты из-за ретраев + abort на 180с | Идемпотентность: upsert/дедуп по `object_key`; при failure — удалять объект из S3 или sweeper. |
| Парити-дрейф текста (pdfplumber≠pdf-parse, python-docx теряет таблицы, .doc без аналога) | Golden-file diff; порог 80 идентичен; .doc → LLM-fallback. |
| Fail-open `verify_backend_secret` на staging/local | Явный guard на эндпоинте или задокументировать (прод требует секрет). |
| Per-page fan-out флудит общий LangSmith-проект | Родительский `trace()` — одно ран-дерево на документ, per-page как дети (не топ-левел). Флуда нет, отдельный проект не нужен. |
| `object_key=NULL` на legacy-строках | Эндпоинт отвергает отсутствующий `object_key` понятной ошибкой. |

## 5. Открытые вопросы (по желанию)
- [ ] Сделать поле `truncated` настоящим? Сейчас хардкод `false`, а `max_tokens=16384` может молча резать длинные одно-запросные извлечения.
- [ ] HEIC: модель может не принять напрямую → при отказе нужен `pillow-heif` (доп. зависимость).

---

## Ссылки на код
- Текущая конвертация: `lib/document-processing.ts` (диспетчер `:67-116`, per-page `:325-396`, порог `:43`).
- Живой роут загрузки: `app/api/projects/[projectId]/documents/route.ts`.
- Бэк: `backend/app/server/main.py`, `security.py:9`, `rag_core/llm.py`, `config/chat.yaml`, `services/supabase_repo.py`.
- Observability: `backend/app/utils.py` (RequestContext), `config/logging.yaml`, трейс-шаблон `chat_stream.py:94-104`.
- Деплой: `deploy-yandex-cloud-{staging,prod}-python.yml` (S3-секреты — в блок бэка).
- Спайк: `backend/experiments/spike_typefile/spike.py`.
