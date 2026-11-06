# План: доработка и развёртывание ArchIDE на платформе `farm`

Источник требований — онбординг-документ платформы
(`https://docs.platform.local/onboarding/`, версия 1.1.0) и живой контракт
`PlatformContract` v1.8.0 (`kubectl -n pf-onboarding get cm platform-contract`).
Эталонный тенант — `platform-config/tenants/demo/demo-tenant.yaml` (ns `t-demo-app`,
E2E OK); он прочитан в кластере и разобран ниже как образец.

Документ описывает **разрыв** между текущим ArchIDE и требованиями платформы,
**план доработок** (фаза Ф12) и **план развёртывания** с границами безопасности.

---

## 1. Что требует платформа (сжато)

**Правила.** Менять только своё: namespace `t-<project>-*` и файлы своего
репозитория. Не трогать `dev-*`, `pf-*`/`daas`, `argocd`, `kube-system`, другие
`t-*`. Только consumption-идентичности. **GitOps через PR**, перед применением —
`kubectl apply --dry-run=server`. Без `prune` и авто-удаления; namespace'ами и
кластерными ресурсами не управлять. Платформенные ресурсы тенанта (Keycloak
client, Harbor project+robot, Vault role+KV, БД/Redis, AppProject) создаёт
**Platform Team по заявке** — самому их создавать нельзя.

**Порядок.** 1) заявка на тенант → 2) онбординг создаёт репозиторий, креды и
ресурсы → 3) проект копирует шаблон `deploy/tenant/` → 4) переключает зависимости
на общие сервисы → 5) PR в свой репозиторий и в `platform-config/tenants/<project>/`
→ 6) ArgoCD `Synced/Healthy` и smoke → 7) выключить свои локальные сервисы.

**Технические требования (обязательные политики):** `runAsNonRoot`,
`readOnlyRootFilesystem`, **`no-latest-tag`**, requests+limits,
**default-deny NetworkPolicy**.

**Целевые адреса и конвенции.**

| Что | Значение |
|---|---|
| Образы | `harbor.platform.local/<project>/<app>:<git-sha>`; robot на проект |
| Базовые образы | pull-through к Docker Hub: `harbor.platform.local/proxy-dockerhub/<repo>:<tag>` |
| Pull-секрет | ExternalSecret `harbor-pull` → `kubernetes.io/dockerconfigjson` + default SA |
| Секреты | Vault `secret/tenants/<project>/*`, ESO `external-secrets.io/v1`, SA `eso-vault` |
| Данные | PostgreSQL `daas-pg-rw.daas.svc.cluster.local:5432` (`tenant_<project>`), Redis `daas-redis.daas.svc.cluster.local:6379` |
| OIDC | Keycloak realm `tenants`, клиент `<project>-web`, redirect `<hosts>/*` |
| Наблюдаемость | Prometheus-scrape `/metrics`, OTLP `platform-tempo…:4317`, логи в Loki |
| Сеть | default-deny; egress DNS(53), daas(5432), платформенные сервисы; ingress от ingress-nginx |
| Хост | `<app>.platform.local` (внешние записи DNS добавляет платформа) |

**Эталон `t-demo-app` (образец для копирования):** Deployment (порт 8080,
`runAsNonRoot`, `seccompProfile: RuntimeDefault`, `drop ALL`, requests+limits),
Service, ExternalSecret `harbor-pull` и `tenant-db`, SecretStore `vault`,
NetworkPolicy `default-deny` + `allow-dns-and-daas` + `allow-ingress-controller`
(от ns `ingress-nginx`), ResourceQuota `tenant-quota`, LimitRange `tenant-defaults`,
Ingress `demo.platform.local` класса `nginx`.

---

## 2. Разрыв: что в ArchIDE нужно доработать

| # | Требование платформы | Сейчас в ArchIDE | Что делать |
|---|---|---|---|
| 1 | OIDC-вход (Keycloak `tenants`, клиент `<project>-web`) | **аутентификации нет вообще** — HTTP-сервер открыт | Ф12.1: OIDC code-flow, сессии, маппинг claims → пользователь приложения |
| 2 | `/metrics` (Prometheus-scrape) | нет | Ф12.2: `/metrics`, `/healthz`, `/readyz` |
| 3 | `no-latest-tag` | в чарте `tag: latest` | Ф12.4: сборка и пуш по git-SHA |
| 4 | Манифесты тенанта `deploy/tenant/` | чарт в репозитории, но без тенант-обвязки | Ф12.3: ns/quota/limitrange/SA/SecretStore/ExternalSecret/netpol/Ingress |
| 5 | Секреты из Vault через ESO | ключ LLM — обычный Secret | Ф12.5: ExternalSecret (`external-secrets.io/v1`) |
| 6 | default-deny + разрешённый egress | нет NetworkPolicy | Ф12.6: политики (DNS, LLM-эндпоинт, платформенные сервисы) |
| 7 | Образы из `harbor.platform.local` | локальная сборка, чарт смотрит в легаси-реестр | Ф12.4: сборка/пуш роботом, pull через `harbor-pull` |
| 8 | Наблюдаемость (OTLP, структурные логи) | часть есть (JSONL-аудит), трейсов нет | Ф12.7 (опционально): OTLP |
| 9 | Локальный деплой не должен усложняться | есть all-in-one контейнер | Ф12.8: OIDC и платформенная обвязка — **флаги**, локальный режим без них |

Отдельно (вне платформы, но важно): текущий LLM живёт на рабочей станции
(`192.168.20.20:8000`). Для кластера это внешний вызов: он либо разрешается
политикой egress, либо первый деплой идёт в `ARCHIDE_LLM_MODE=mock`.

---

## 3. План доработок — фаза Ф12

| ID | Задача | Изменения | Тесты (до реализации) | Готово, когда |
|---|---|---|---|---|
| Ф12.1 | **OIDC-аутентификация** | код-flow (stdlib), сессионная cookie, конфиг из сборки/значений; `AUTH=oidc\|none`; claims `sub/email/groups` → пользователь приложения (личные документы, диалоги, обратная связь) | API + браузер | без сессии защищённые маршруты → 401; после входа — доступ; пользователь = claim |
| Ф12.2 | **Метрики и health** | `/healthz`, `/readyz`, `/metrics` (Prometheus text: прогоны, узлы, ошибки, длительности) | API | scrape-контракт выполняется, пробы проходят |
| Ф12.3 | **Тенант-манифесты** | `deploy/tenant/` по шаблону платформы: ns, quota, limitrange, SA `eso-vault`, SecretStore, ExternalSecret, netpol, Ingress, Application | `kubectl apply --dry-run=server` + схема | dry-run зелёный в тенант-ns |
| Ф12.4 | **Образы** | сборка/пуш `harbor.platform.local/<project>/<app>:<git-sha>`; базовый образ через `proxy-dockerhub`; pull через `harbor-pull` | скрипт + проверка тега | образ в Harbor, под стартует без `latest` |
| Ф12.5 | **Секреты Vault/ESO** | ExternalSecret для ключа LLM и прочих секретов, `external-secrets.io/v1` | API + dry-run | секрет синхронизируется, приложение читает его |
| Ф12.6 | **Сеть** | default-deny + egress DNS/daas/LLM + ingress от ingress-nginx | манифесты + dry-run | политики применяются, трафик приложения не ломается |
| Ф12.7 | **Наблюдаемость** (опц.) | OTLP-трейсы, структурные логи | API | трейсы уходят в Tempo |
| Ф12.8 | **Сохранение локального деплоя** | все платформенные механизмы — за флагами; `deploy/Dockerfile` без изменений по сути | существующий e2e | локальный контейнер работает как раньше |

Порядок: **Ф12.1 → Ф12.2 → Ф12.3 → Ф12.4 → Ф12.5 → Ф12.6 → (Ф12.7) → Ф12.8**.
Ф12.1 обязателен до выхода в общий кластер: открытый HTTP-сервер в продуктивном
тенанте недопустим.

---

## 4. План развёртывания

### Шаг 0. Согласовать идентичность проекта

Определить и согласовать: `<project>` (предлагается `archide`), `<app>`
(`archide`), хост (`archide.platform.local`), состав namespace'ов
(`t-archide-app`), квоты, нужен ли DaaS PostgreSQL (сейчас состояние — на PVC,
это допустимо квотой `persistentvolumeclaims: 10`).

### Шаг 1. Заявка на тенант (Platform Team)

```
curl -k -H 'Host: intake.platform.local' -H 'Content-Type: application/json' \
  -X POST https://192.168.20.254:30965/api/intake \
  -d '{"project":"archide","repo":"archide","description":"ArchIDE — IDE архитектурного графа","onboarding":true}'
```

Платформа создаёт: Gitea org/repo + deploy-token (→ Vault
`secret/tenants/archide/git`), Keycloak client `archide-web`, Harbor
project+robot, Vault role `tenant-archide` + KV, AppProject `tenant-archide`,
scrape-таргеты. Дополнительно в заявке указать: callbacks
`https://archide.platform.local/*`, scrape `/metrics`.

### Шаг 2. Доработки Ф12 (в своём репозитории)

По плану выше, каждый пункт — тесты до реализации, затем реализация; в конце
каждого пункта — `bash tests/run_e2e.sh` (обязательное правило репозитория).

### Шаг 3. Тенант-обвязка

Скопировать `tenants/_templates/tenant-template.yaml` в `deploy/tenant/`,
заменить `<project>` на `archide`, добавить манифесты приложения (Deployment,
Service, Ingress, PVC). Перед применением — **обязательно**
`kubectl apply --dry-run=server -n t-archide-app`.

### Шаг 4. Образ

**Статус (Ф12.4): путь найден.** Контракт обновлён до v1.9.0 — в нём появился
раздел `build`: **BuildKit-демон** `buildkitd.pf-ci.svc:1234` (ns `pf-ci`,
проверен — запущен) и Job с **`buildctl`** по шаблону
`cluster-baseline/base/ci/buildctl-job.yaml`; tool-образ из шаблона —
`docker.io/moby/buildkit:v0.16.0`, контекст клонируется внутри Job, тег всегда
новый, push в `harbor.<domain>/<project>/<image>:<tag>`. Инструменты копирования
(`image-copy.pf-ci.svc:8080`, `platform/crane`, `platform/skopeo`) — отдельно.

**Как собираем:** `deploy/build-job.yaml` в нашем тенанте (self-managed —
`isolation.supported: [platform-managed, self-managed]`): Job в `t-archide-app`,
демон общий, креды **свои** из Vault (git deploy-token и робот Harbor), плюс
NetworkPolicy `allow-buildkit-egress` к `pf-ci:1234`. Платформенные секреты
`pf-ci` не монтируем (кросс-namespace и правила «не трогать pf-*»).

```bash
kubectl -n t-archide-app apply -f deploy/build-job.yaml
kubectl -n t-archide-app logs job/archide-build -f   # тег = короткий git-SHA
```

Альтернатива без кластера — локальный Docker: `./scripts/build-image.sh --push`.
После сборки заменить в `deploy/tenant/60-app.yaml` тег `REPLACE_WITH_GIT_SHA`
на полученный SHA.



```bash
./deploy/build.sh --tag "$(git rev-parse --short HEAD)" \
  --push --registry harbor.platform.local/archide
```

Базовый образ — через `harbor.platform.local/proxy-dockerhub/python:3.11-slim`;
pull в кластере — через ExternalSecret `harbor-pull` и default SA.

### Шаг 5. PR и GitOps

PR в свой репозиторий (манифесты + чарт) и в `platform-config/tenants/archide/`.
ArgoCD (AppProject `tenant-archide`) доводит тенант до `Synced/Healthy`.

### Шаг 6. Smoke (по §9 онбординга)

```bash
kubectl get ns t-archide-app
kubectl -n t-archide-app get externalsecret,secret,deploy,svc,ingress
kubectl -n t-archide-app logs deploy/archide
curl -k https://archide.platform.local/healthz
kubectl -n argocd get application | grep archide
```

### Шаг 7. Приёмка и передача

Зафиксировать результат прогона (pass/fail, число проверок), обновить
`docs/ARCHITECTURE.md` (§13 «Текущее состояние») и `deploy/README.md`.

---

## 5. Границы безопасности (обязательства)

- Работаем **только** в `t-archide-*` и в своём репозитории; `dev-*`, `pf-*`,
  `daas`, `argocd`, `kube-system` и чужие `t-*` не трогаем.
- Никаких `prune`/авто-удаления; namespace и кластерные ресурсы не создаём —
  namespace приходит из шаблона/онбординга.
- Перед каждым применением — `kubectl apply --dry-run=server`.
- Не создаём платформенные ресурсы (Keycloak client, Harbor project+robot, Vault
  role, БД) — только заявка.
- Только consumption-идентичности; provisioning-доступ не запрашиваем.
- Секреты — только через Vault/ESO; в git секретов нет.

---

## 6. Решения (согласовано 05.11)

1. **Идентичность тенанта:** `<project>` = `archide`, приложение `archide`, хост
   **`archide.platform.local`**, namespace `t-archide-app`.
2. **LLM для кластера:** эндпоинт рабочей станции
   `http://192.168.20.20:8000/v1` (модель `Qwen3.8-27B-UD-Q5_K_XL`), режим `live`;
   для него добавлено правило egress `allow-llm-egress`. Если эндпоинт окажется
   недоступен из кластера — переключение на `mock` одной строкой в манифесте.
3. **Данные:** остаются на **PVC** (`ceph-rwo`, 10Gi), не DaaS PostgreSQL.
   Обоснование: ArchIDE хранит состояние файлами (модели, прогоны, раскладки,
   диалоги, обратная связь) и работает в один реплика-под; переезд на Postgres —
   переписывание слоя хранения без выигрыша на таком профиле (низкий QPS, мелкие
   файлы), а квота тенанта разрешает до 10 PVC. DaaS PG остаётся опцией на случай
   нескольких реплик или внешних потребителей данных.
4. **Заявка и PR:** заявку на тенант подаёт агент проекта (подана), согласует
   владелец продукта; PR в `platform-config` ревьюит Platform Team.
5. **Две версии, один продукт.** Локальная версия публикуется в GitHub
   (`pol4/ArchIDE`) и поддерживается в актуальном состоянии по логике и промптам.
   Платформенная версия ведётся в **отдельном локальном репозитории**
   `~/Documents/archide-platform` (затем публикуется в Gitea платформы); она
   **не форкает** продукт: `scripts/sync-upstream.sh` копирует `archide-poc/`
   из основного репозитория по зафиксированному коммиту (`vendor/UPSTREAM`),
   поэтому поведение и тексты промптов совпадают в обеих версиях.

### 6.1. Состав платформенного репозитория

```
archide-platform/
  vendor/archide-poc/     копия продукта (генерируется sync-upstream.sh)
  vendor/deploy/          entrypoint (общий для обеих версий)
  scripts/sync-upstream.sh
  deploy/Dockerfile       образ для Harbor (база — через proxy-dockerhub)
  deploy/tenant/          17 объектов: ns, quota, limitrange, 2×SA, SecretStore,
                          2×ExternalSecret, 4×NetworkPolicy, Deployment/Service/
                          PVC/Ingress, ArgoCD Application
```

## 7. Что нужно решить до старта (архив вопросов)

1. `<project>` и хост — предлагается `archide` и `archide.platform.local`.
2. Квоты тенанта: предлагается `requests.cpu 4 / memory 8Gi`, `limits 8 / 16Gi`
   (как в шаблоне).
3. LLM для кластера: эндпоинт рабочей станции с правилом egress **или** первый
   деплой в `mock` с последующим включением живой модели.
4. Нужен ли DaaS PostgreSQL вместо PVC (состояние сейчас — JSON-файлы на томе).
5. Кто подаёт заявку и подтверждает PR в `platform-config` (Platform Team
   ревьюит; подача — со стороны проекта).
