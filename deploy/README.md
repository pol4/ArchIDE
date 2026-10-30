# Развёртывание ArchIDE

Один и тот же исходный код (`archide-poc/`) даёт **две версии деплоя**:

| Версия | Что это | Как запускается |
|---|---|---|
| **Локальная станция** | один контейнер со всем внутри: HTTP-сервер, UI и встроенный агент в процессе | `deploy/build.sh` → `docker run` / `docker compose` |
| **Kubernetes** | тот же образ + Deployment, Service, Ingress, PVC и Secret | `helm upgrade --install` → `deploy/helm/archide` |

Разницы между версиями — только в параметрах запуска. Собирается **одна сборка**:
локально вы её запускаете контейнером, в кластере — тем же образом.

```
deploy/
  Dockerfile            all-in-one образ (сервер + UI + агент), non-root, /data
  entrypoint.sh         первичная настройка (config.json) из переменных окружения
  build.sh              сборка (+ опционально push в реестр)
  docker-compose.yml    запуск на станции
  helm/archide/         чарт для Kubernetes
    values.yaml         значения по умолчанию
    values-farm.yaml    значения для кластера farm
```

---

## 1. Локальная станция

```bash
./deploy/build.sh                       # соберёт archide:latest из общего дерева
docker compose -f deploy/docker-compose.yml up --build   # или сразу так
# → http://127.0.0.1:8000
```

Что внутри контейнера и что снаружи:

- **Агент работает в процессе** (`ARCHIDE_RUNNER=local`, значение по умолчанию) —
  контейнеру не нужен Docker-сокет. Если на станции есть демон Docker и вы хотите
  изоляцию агента в песочнице, поставьте `ARCHIDE_RUNNER=docker` и раскомментируйте
  монтирование `/var/run/docker.sock` в `docker-compose.yml`.
- **Состояние** (модели, прогоны, проекты, раскладки, документы) — на томе
  `archide-data`, смонтированном в `/data`. Контейнер можно удалять и пересобирать
  без потери результатов.
- **LLM** — на хосте; по умолчанию `http://host.docker.internal:11434/v1`
  (Ollama). Переопределяется переменными `ARCHIDE_LLM_BASE_URL`,
  `ARCHIDE_LLM_MODEL`, `ARCHIDE_LLM_API_KEY`, `ARCHIDE_LLM_MODE`.
- **Быстрый первый запуск без модели:** `ARCHIDE_LLM_MODE=mock` — весь конвейер
  пройдёт на детерминированном графе, инференс не нужен.

Обновление после правок в коде:

```bash
./deploy/build.sh && docker compose -f deploy/docker-compose.yml up -d --build
```

---

## 2. Kubernetes (кластер `farm`)

```bash
helm upgrade --install archide deploy/helm/archide \
  -n archide --create-namespace \
  -f deploy/helm/archide/values.yaml \
  -f deploy/helm/archide/values-farm.yaml \
  --set image.repository=192.168.20.254:30003/dev/archide \
  --set image.tag=<тег или digest> \
  --set-string config.llm.apiKey='<ключ инференса>'
```

Проверить, что получится, **ничего не создавая**:

```bash
helm template archide deploy/helm/archide -f deploy/helm/archide/values-farm.yaml > /tmp/archide.yaml
kubectl --context farm apply --dry-run=server -n archide -f /tmp/archide.yaml
```

### Особенности кластера `farm` (важно)

1. **Свой namespace.** Платформенные пространства (`dev-*`) управляются ArgoCD
   (`aisandbox-platform`, `syncPolicy: automated` с `prune` и `selfHeal`). ArchIDE
   разворачивается в **отдельном** namespace `archide` и намеренно **не несёт**
   метку `argocd.argoproj.io/instance` — поэтому платформенная синхронизация его
   не подхватывает и не удаляет.

2. **Образы только подписанные.** Политика Kyverno `aisandbox-verify-images`
   (`enforce`) проверяет подпись **cosign** для образов из
   `192.168.20.254:30003/dev/*` и подменяет тег на digest. Неподписанный образ
   отклоняется на admission — это штатное поведение, и обходить его (например,
   публикуя образ по другому пути в Harbor) **нельзя**. Образ нужно собрать и
   подписать штатным конвейером платформы, либо получить у владельца платформы
   ключ подписи и учётные данные для push в проект `dev`.

3. **Pod Security — restricted.** По политике `aisandbox-pod-security` под
   запускается только непривилегированным: `runAsNonRoot`, `runAsUser/Group 1000`,
   `fsGroup 1000`, `seccompProfile: RuntimeDefault`, `allowPrivilegeEscalation:
   false`, `capabilities: drop ALL`. Чарт уже настроен так; образ создаёт
   пользователя с UID 1000 и пишет только в `/data` (`readOnlyRootFilesystem: true`,
   отдельный `emptyDir` в `/tmp`).

4. **Хранилище.** `values-farm.yaml` использует `storageClass: ceph-rwo`
   (в кластере также есть `local-path`). Том один (`ReadWriteOnce`), поэтому
   стратегия обновления — `Recreate`, реплика одна.

5. **Доступ.** Ingress с классом `nginx` и хостом
   `archide.aisandbox.farm.local` (конвенция платформы — `*.aisandbox.farm.local`).
   Без Ingress: `kubectl -n archide port-forward svc/archide 8000:8000`.

6. **Секреты в git не попадают.** Ключ инференса передаётся `--set-string
   config.llm.apiKey=…` (создаётся Secret `archide-llm`) или заранее созданным
   Secret'ом: `--set config.llm.existingSecret=archide-llm`. Для платформенного
   хранилища секретов доступен `external-secrets`.

### Обновление и удаление

```bash
helm upgrade archide deploy/helm/archide -n archide -f deploy/helm/archide/values-farm.yaml \
  --set image.tag=<новый тег>
kubectl -n archide rollout status deploy/archide

# полное удаление (PVC с данными сохранится, удаляется отдельно)
helm uninstall archide -n archide
kubectl -n archide delete pvc archide-data
kubectl delete namespace archide
```

---

## 3. Общая среда разработки

Разработка ведётся в одном дереве `archide-poc/` — ни один из деплоев не форкает
код:

- **локально**: `python3 server.py` (или `./run.sh`) — без сборки образа;
- **в кластере**: код в кластер не «заливается» — там работает образ из этого же
  дерева, а результаты (модели, прогоны, проекты) лежат на PVC и переживают
  пересборку;
- **быстрая сборка докера в любой момент**: `./deploy/build.sh` (плюс
  `--push` с `REGISTRY=…` для кластерного образа);
- **проверка обоих вариантов из одного источника**: `bash tests/run_e2e.sh` —
  сквозные тесты (API + браузер) на том же коде, что попадает в образ.

Переменные окружения, которыми параметризуется один и тот же образ:

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `ARCHIDE_DATA` | `/data` | каталог состояния (модели, прогоны, проекты, конфиг) |
| `ARCHIDE_HOST` / `ARCHIDE_PORT` | `0.0.0.0` / `8000` | адрес и порт сервера |
| `ARCHIDE_RUNNER` | `local` | где работает агент: в процессе (`local`) или в песочнице (`docker`) |
| `ARCHIDE_LLM_BASE_URL` | `http://host.docker.internal:11434/v1` | OpenAI-совместимый эндпоинт |
| `ARCHIDE_LLM_MODEL` | `qwen2.5-coder:7b` | имя модели |
| `ARCHIDE_LLM_API_KEY` | — | ключ (в кластере — из Secret) |
| `ARCHIDE_LLM_MODE` | `live` | `mock` — прогон без инференса |
| `ARCHIDE_MAX_REPAIRS` | `3` | число попыток ремонта графа |
