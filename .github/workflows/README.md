# Деплой-воркфлоу

Все воркфлоу запускаются ТОЛЬКО вручную (workflow_dispatch). Ветки: изменения
идут в `staging`, промоут в прод — merge `staging` → `main` (см. память/README
инфры про однонаправленный поток).

## Актуальные

- **deploy-yandex-cloud-staging-coi.yml** — стейджинг на COI VM
  `jhelper-app-staging` (staging.jhelper.ru). Ветка `staging`.
- **deploy-yandex-cloud-prod-coi.yml** — прод на COI VM `jhelper-app-prod`
  (jhelper.ru). Ветка `main`; флаг `skip_dns_check` — для прогонов до
  переключения DNS.
- **deploy-opensearch-staging.yml** — разовый провижн OpenSearch COI VM
  (общая для стейджинга и прода; данные переживают деплои приложений).
- **index-court-practice-staging.yml** — индексация судебной практики в
  OpenSearch.

Разовые шаги, секреты/переменные окружений и порядок cutover/отката —
[infra/staging-coi/README.md](../../infra/staging-coi/README.md).

## Легаси (удалить при выводе serverless-контура)

- **deploy-yandex-cloud-staging-python.yml**, **deploy-yandex-cloud-prod-python.yml**
  — деплой на старые Serverless Containers. После катовера прода на self-hosted
  Supabase (2026-08-09) откат на этот контур невозможен (старые бандлы указывают
  на мёртвый supabase.co), воркфлоу оставлены только до формального вывода
  контейнеров из эксплуатации.

## Секреты (общие для окружений Staging ENV / Deploy ENV)

`YC_SA_JSON`, `YC_CLOUD_ID`, `YC_FOLDER_ID`, `YC_REGISTRY_ID`,
`YC_SERVICE_ACCOUNT_ID`, `YC_SUBNET_ID` — доступ к Yandex Cloud;
`SUPABASE_SERVICE_ROLE_KEY`, `BACKEND_SHARED_SECRET`, `S3_*`, `LANGSMITH_API_KEY`,
`PROXY_LIST_B64` — рантайм приложения; `STAGING_SSH_PUBLIC_KEY` /
`PROD_SSH_PUBLIC_KEY` — SSH на VM (применяются при создании).
