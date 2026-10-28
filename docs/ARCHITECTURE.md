**Русский** · [English](ARCHITECTURE.en.md)

# ArchIDE — архитектура системы

Как устроена система целиком. Концепция — [`Concept.md`](Concept.md), план —
[`ROADMAP.md`](ROADMAP.md), PoC — [`archide-poc/`](archide-poc/).

## 1. Обзор

ArchIDE — среда, в которой архитектура описана **единым фасетным графом**
(каркас C4 + аспекты), а код, диаграммы и документы — его **проекции**. Система:

- хранит и **версионирует** модель (Merkle-DAG коммитов);
- **реконструирует** её из исходного кода и документации (с валидацией обоих);
- даёт **редактировать** с автоматическим обеспечением непротиворечивости;
- **генерирует и валидирует** код по модели (с подписанными метками трассируемости);
- связывает модель с **корпоративными документами** через RAG;
- исполняет **агентов в песочнице** через собственный LLM-шлюз.

**Принцип — динамическая устойчивость.** Система не опирается на «идеальную»
онтологию заранее: базовая графовая модель **легко расширяема** и может быть
неполной, а её непротиворечивость **поддерживается динамически** — непрерывным
циклом «наблюдение (валидация/impact/покрытие) → расхождение → коррекция
(предложения и авто-починка, в т.ч. агентом)». Агент выступает **стабилизатором**
целостности модели.

## 2. Контекст (C4 L1)

**Акторы:** Архитектор, Оператор агентов, Аудитор.
**Внешние системы:** LLM-инференс (OpenAI-совместимый), VCS (git), корпоративные
документы (Confluence/Wiki/файлы), реестр требований (Jira/…), CI/CD, объектное
хранилище артефактов.

```mermaid
graph LR
  ARCH["Архитектор"] --> A["ArchIDE"]
  OP["Оператор агентов"] --> A
  AUD["Аудитор"] --> A
  A --> LLM["LLM-инференс (OpenAI-совместимый)"]
  A --> VCS["VCS (git)"]
  A --> DOCS["Корпоративные документы"]
  A --> REQ["Реестр требований"]
  A --> CI["CI/CD"]
  A --> STORE["Хранилище артефактов"]
```

## 3. Контейнеры (C4 L2)

```mermaid
graph TD
  UI["Web UI (SPA):<br/>аспекты, канвас, timeline"]
  EDIT["Agent Editor (chat):<br/>транзакции, вопросы, impact, диф"]
  MEM["Agent Memory & Adaptation:<br/>диалоги, профиль, рефлексия"]
  API["API / Orchestrator"]
  MS["Model Store:<br/>граф + версии (CAS)"]
  VAL["Validator /<br/>Constraint Engine"]
  RECON["Reconciler<br/>(динамическая устойчивость)"]
  PROJ["Projection Engine"]
  SBX["Agent Sandbox Runtime"]
  GW["LLM Gateway"]
  RAG["RAG / Index Service"]
  ING["Document Ingestion"]
  PDOC["Personal Document Store:<br/>md/csv, личная история"]
  ART["Artifact / CI Service"]
  REG["Registry:<br/>требования, контракты, алиасы"]

  UI --> API
  UI --> EDIT
  EDIT --> API
  EDIT --> VAL
  EDIT --> MS
  EDIT --> MEM
  MEM --> MS
  MEM --> SBX
  API --> MS
  API --> VAL
  API --> PROJ
  API --> SBX
  API --> RAG
  API --> ART
  API --> REG
  SBX --> GW
  GW --> LLM["LLM-инференс"]
  RAG --> MS
  ING --> RAG
  ING --> PDOC
  PDOC --> RAG
  ING --> DOCS["Документы"]
  ART --> MS
  VAL --> MS
  RECON --> VAL
  RECON --> MS
  RECON --> EDIT
  RECON --> SBX
  PROJ --> MS
  MS --> VCS["VCS (git)"]
```

## 4. Компоненты по контейнерам

**Model Store**
- `Graph Store` — property graph (узлы/рёбра, аспекты, якоря).
- `Version Store` — content-addressed коммиты (Merkle-DAG), деревья снапшотов.
- `Metamodel Registry` — типы узлов/рёбер, схемы аспектов, правила якорей, словари.
- `Identity/Alias` — стабильные ID, namespace'ы, алиасы (rename-safe).
- `Requirements namespace` — внешние требования + провенанс.

**Reconstruction Pipeline (реализовано в PoC)**
- `Base pass (C4)` — один проход агента по исходникам → структурный граф.
- `Aspect extraction` — эвристики по коду (интерфейсы/операции, данные, автоматы,
  события, доступ, развёртывание, ограничения) с **якорением к существующим узлам**.
- `Sequential aspect passes (Ф3.5)` — после C4 каждый аспект строится **отдельным
  шагом агента** и мёржится с якорями и формальной валидацией (откат при ошибке),
  с логом в ленту; контекст прохода **сужается под аспект** (Ф3.6: релевантные файлы +
  срез графа), проходы **устойчивы и возобновляемы** (Ф3.7: пропуск покрытых аспектов,
  лимит прироста, старт от сохранённой модели).
- `Continuous agent session (Ф3.10)` — один контейнер/беседа на прогон: репозиторий
  выгружается однократно, база + все аспектные проходы + сверка идут в одной сессии.
- `Reconciliation (Ф3.9)` — отчёт о сиротах/элементах без evidence + **коррекция
  агентом** с безопасным мёржем (только evidence и связи между существующими узлами).
- `Quality metrics (Ф3.8)` — покрытие типов по аспектам, сироты, evidence в `/api/eval`.
- `Notation fields (Ф2.14)` — модель несёт то, что нужно диаграммам: `Transition.from/to`,
  `Participant`-дорожки, `cardinality` у связей; представления строятся **из графа**,
  а не хранятся отдельно.

**Validator / Constraint Engine**
- `Schema validator` (жёсткие инварианты), `Referential actions`
  (cascade/restrict/nullify/repoint), `Cross-aspect rules`, `Constraint language`,
  `Impact analyzer`, `Incremental validator`.

**Agent Editor (chat)**
- `Intent→transaction` — перевод намерения пользователя в транзакцию правок.
- `Clarifying questions` — уточняющие вопросы при неоднозначности.
- `Impact advisor` — предупреждения о последствиях (impact, нарушения инвариантов,
  потеря покрытия требований).
- `Diff proposer` — семантический диф на подтверждение.
- `Consistency reviewer` — **после ручных правок** человека проверяет граф и
  **предлагает исправления** для сохранения непротиворечивости/целостности.
- Работает через `Agent Sandbox Runtime` + `LLM Gateway`; опирается на `Validator` и
  `Model Store`. Оба режима (чат и ручное редактирование) проходят через один gate.

**Reconciler (динамическая устойчивость, §0.1 концепции)**
- `State observer` — собирает сигналы состояния: валидация, якоря, кросс-аспектные
  правила, impact, покрытие требований, отклонения код↔доки.
- `Divergence detector` — определяет расхождения/нарушения целостности и полноты.
- `Correction planner (agent)` — формирует план коррекции (семантический диф);
  при неоднозначности задаёт уточняющие вопросы.
- `Auto-repair applier` — применяет подтверждённые исправления и **дорабатывает
  связанные части графа** (через `Validator` + `Model Store`).
- `Trigger scheduler` — запускает цикл **по событиям** (после правки/коммита,
  изменения требований/федерации) и/или **периодически**.
- Инвариант: модель может быть **не идеальной**, но целостность **динамически
  стабилизируется** циклом «наблюдение → расхождение → коррекция».

**Agent Memory & Adaptation (Concept §18)**
- `Dialogue store` — сохранение диалогов (per architect / per project) со структурой
  (намерение → транзакция → исход) и провенансом.
- `Feedback collector` — явная и неявная обратная связь, атрибуция к изменению/исходу.
- `Architect profile` — предпочтения (стиль, детализация, риск-аппетит, форматы);
  версионируется и обратим.
- `Adaptation policy` — подстройка поведения **в рамках инвариантов** (не меняет
  метамодель, не ослабляет gate/безопасность).
- `Reflection scheduler` — периодический батч-обзор опыта, извлечение уроков,
  обновление профиля/плейбука, оценка результата (сходимость, доля принятых диффов).
- `Toxicity filter` / `Stability guard` — нейтрализация оскорбительной/манипулятивной
  обратной связи, приоритет объективных сигналов, ограничение влияния негатива,
  защита от «отравления» профиля, аудит и откат адаптаций.

**Projection Engine**
- `Layout engine`, `Aspect renderers` (C4/DFD/state/BPMN/роли/…),
  `Serializers` (OpenAPI/proto/DDL/JSON Schema), `Slice builder` (§6),
  `Coverage matrix` (§11).

**Agent Sandbox Runtime**
- `Agent registry`, `Scheduler`, `Container runtime` (изоляция, лимиты),
  `Volume manager`, `Event bus` (лента активности), `Secrets/lease`.

**LLM Gateway**
- `Auth (lease)`, `Router` (upstream), `Stream` (SSE), `Quota/rate`, `Audit`.

**RAG / Index Service**
- `Chunker`, `Embedder`, `Vector index`, `Graph retriever`, `Hybrid ranker`,
  `Provenance/citations`, `Doc↔model comparator`,
  **`Marker-based grounding verifier`** — поклеймовая атрибуция: источник (метка чанка) +
  **fidelity** (`verbatim/paraphrase/derived/unsupported`) и `added/omitted` (что изменено
  LLM); метки **привязаны к содержимому** (дайджест текста чанка).

**Document Ingestion**
- `Connectors` (Confluence/Wiki/файлы, **Markdown/CSV из чата**), `Parser`, `Chunker`,
  `Requirement linker`, `Personal store` (личная история пользователя, изоляция),
  `Chunk marker issuer` (подписанные метки чанков, аналог §11).

**Artifact / CI Service**
- `Generators`, `Marker issuer/verifier` (§11), `Conformance suite`, `Gate`.

**Registry**
- `Requirements`, `Contracts (GCR)`, `Aliases`, `Keys`.

## 5. Данные и хранилища

| Хранилище | Содержимое | Технология (варианты) |
|---|---|---|
| Graph Store | узлы/рёбра/аспекты/якоря | property graph (Neo4j/Kuzu/…) или in-memory + персистентность |
| Version Store | коммиты модели (Merkle-DAG) | CAS, git-подобные объекты |
| Каноническая сериализация | git-friendly модель | текстовые файлы + парсер |
| Vector Index | эмбеддинги чанков документов и элементов графа | pgvector/Qdrant/… |
| Document Store | исходные документы, метаданные, спаны | объектное хранилище |
| Artifact Store | артефакты, квитанции, метки | объектное хранилище |
| Registry | требования/контракты/алиасы/ключи | реляционная БД |

## 6. Ключевые потоки

**6.1 Реконструкция:** ingest репозитория → collect/compress → sandbox-агент
(проход 1: код; проход 2: документация) → validate (хост+агент, repair) → merge
(отклонения) → **commit** в модель.

**6.1a Синтез архитектуры по описанию (реализовано):** постановка в чате
(`design <описание>`, при желании — приложенные документы) → материал синтеза
(`data/scratch/design/<имя>`: `00-description.md` + документы) → **проектировочный
проход** (код не нужен) → те же валидация/ремонт и аспектные проходы, что и при
реконструкции → модель. Далее: **архитектурная документация из модели**, пакет
передачи кодогенерирующему агенту, корректировка визуально или запросами к агенту.
Синтез — не отдельный контур, а другой **вход** в ту же модель и тот же gate.

**6.2 Ручное редактирование (человек):** UI-правка → **транзакция** → incremental
validate → impact → commit или блок с причиной (referential actions); далее
**агент-ревьюер** проверяет граф и **предлагает исправления** (диф на подтверждение).

**6.3 Генерация/верификация:** slice → выдача **меток (lease)** → агент →
артефакты → **conformance gate** (подпись/размещение/покрытие/атрибуция) → привязка
`implemented_by` + commit артефактов.

**6.4 RAG-запрос:** query → гибридный retrieval (вектор + ключи + обход графа) →
(доки + элементы модели) → заземлённый ответ с **цитатами**; сверка док↔модель.

**6.5 Федерация:** publish контракта → GCR → discovery → федеративные рёбра →
derived-требования/тесты (§14).

**6.6 Диалоговое редактирование (агент, основной сценарий):** чат-намерение →
уточняющие вопросы → транзакция правок + impact-предупреждения → **семантический
диф** на подтверждение → применение + авто-починка зависимых частей → commit (gate).
По запросу — кастомные аспекты/срезы/представления.

**6.9 Персональные документы и маркированный RAG (Ф7):** архитектор прикладывает
Markdown/CSV в чате → оригинал в **личной истории** → ingestion/chunking → каждый чанк
получает **подписанную метку** → индекс RAG → запрос + **маркированные** чанки → **LLM (через шлюз/песочницу)** →
**заземлённый ответ с цитатами**, который **верифицируется по меткам**
(восстановлено vs сгенерировано).

**6.7 Цикл стабилизации (reconciliation, §0.1):** триггер (правка/коммит, изменение
требований/федерации, периодика) → **наблюдение** (валидация, impact, покрытие,
отклонения) → **расхождение** → **план коррекции** (агент) → подтверждение или
авто-починка → commit. Обеспечивает **динамическую устойчивость** модели.

**6.8 Накопление опыта и рефлексия (§18):** диалог → обратная связь (явная/неявная) →
обновление **профиля архитектора** → **адаптация поведения** (в рамках инвариантов);
периодическая **рефлексия** (обзор опыта → уроки → обновление профиля/плейбука).
**Токсичная** обратная связь нейтрализуется, приоритет у объективных сигналов
(валидатор/gate) — стабильность и эффективность сохраняются.

```mermaid
sequenceDiagram
  participant U as Web UI
  participant A as API/Orchestrator
  participant S as Sandbox Runtime
  participant G as LLM Gateway
  participant V as Validator
  participant M as Model Store
  U->>A: reconstruct(repo, agent)
  A->>S: start job (repo, prompt, constraints)
  S->>G: chat/completions (gateway token)
  G->>G: inject real key, forward upstream
  S->>A: events + graph
  A->>V: validate(graph)
  V->>M: commit(version)
  M-->>U: graph + validation + history
```

## 7. Подсистема версионности

- **Commit** = `(parent, changes, author, ts, signature)`; адресация по контенту.
- **Tree** — снапшот модели (узлы/рёбра/аспекты) → хэш; дедупликация.
- **Ветки/merge**, **семантический diff**, **as-of** запросы, **restore**,
  **branch-from** (§12).
- **Пины** — запуск/срез фиксирует commit; метки (§11) ссылаются на commit.
- **GC** — сборка недостижимых объектов.

## 8. Подсистема RAG

```mermaid
graph LR
  DOCS["Документы"] --> ING["Ingestion: parse→chunk→embed"]
  GRAPH["Модель (элементы+срезы)"] --> EMB["Embed"]
  ING --> VEC["Vector index"]
  EMB --> VEC
  Q["Запрос"] --> HYB["Hybrid retriever<br/>(вектор+ключи+граф)"]
  VEC --> HYB
  HYB --> ANS["Grounded answer + citations"]
  HYB --> CMP["Doc↔model comparator"]
  CMP --> DEV["Отклонения (в граф)"]
```

- **Chunking**: структурный для документов (разделы/абзацы), семантический для графа
  (элемент + срез/проекция).
- **Retrieval**: гибрид + обход графа + фильтры (версия, проект, аспект).
- **Requirement linking**: извлечение → кандидаты связей → ревью → подтверждённые
  связи с **провенансом** (§13).
- **Grounding**: ответы с цитатами; утверждение из документа без опоры в модели
  помечается как отклонение.

## 9. Безопасность и доверие

- **Ключи**: реальный ключ инференса — только у хоста; агенты — токен/lease шлюза;
  ключ в контейнер не попадает.
- **Изоляция песочницы**: лимиты cpu/mem/pids/time, `--read-only`, cap-drop,
  сетевой доступ только к шлюзу.
- **Метки** подписываются хостом (§11); агент недоверенный — все артефакты через gate.
- **Изоляция личной истории** (§13.6): оригиналы документов пользователя хранятся
  per-user; чанки несут подписанные метки (§13.7), RAG-ответ верифицируется по ним.
- **Устойчивость к токсичной обратной связи** (§18): нейтрализация оскорбительной/
  манипулятивной обратной связи, приоритет объективных сигналов, ограничение влияния
  негатива, аудит и откат адаптаций профиля.
- **Права**: RBAC на редактирование (владение элементами/аспектами), аудит.

## 10. Развёртывание

- **Контейнеры**: `web`, `api`, `model-store`, `validator`, `projection`,
  `sandbox-runtime`, `gateway`, `rag`, `ingestion`, `artifact-ci`.
- **Тома**: shared agent volume, model store, vector index, doc store.
- **Сети**: внутренняя; egress наружу — только у `gateway`.
- **Масштабирование**: горизонтально — stateless (api/projection/rag); граф-стор —
  вертикально/партиционирование; валидация — инкрементальная.

## 11. Технологический стек (варианты)

| Слой | PoC | Целевое (варианты) |
|---|---|---|
| Backend | Python stdlib (`server.py`) | FastAPI/uvicorn, воркеры |
| Graph | in-memory dict + JSON | Neo4j / Kuzu / property-graph БД |
| Версии | — | git-подобный CAS (свои объекты или git) |
| Векторы | — | pgvector / Qdrant / Milvus |
| Frontend | vanilla SPA + SVG | тот же SPA (без сборки) либо framework |
| Агенты | Docker + stdlib/Kilo CLI | реестр-плагины, адаптеры |
| Эмбеддинги | — | локальные или внешние провайдеры |

## 12. Нефункциональные требования

- **Консистентность**: каждая версия проходит валидатор; нет невалидной истории.
- **Воспроизводимость**: запуски/генерации пиннят версию модели и артефактов.
- **Аудит**: провенанс требований/артефактов, подписи меток и квитанций.
- **Масштаб**: инкрементальная валидация, партиционирование графа.
- **Безопасность**: изоляция песочницы, ключи у хоста, RBAC.
- **Latency**: асинхронные запуски, стриминг событий, кэш проекций.
- **Динамическая устойчивость**: непрерывный reconciliation-цикл (наблюдение →
  расхождение → коррекция); **сходимость** — число расхождений монотонно убывает при
  коррекциях; модель может быть не идеальной, но целостность поддерживается.
- **Стабильность агента**: поведение не деградирует при токсичной обратной связи;
  адаптации обратимы; объективные сигналы (валидатор/gate) приоритетны.

## 13. Текущее состояние vs целевое

Реализовано (фазы `docs/IMPLEMENTATION_PLAN.md`; тесты — API + браузер):

| Компонент | PoC (сейчас) | Целевое |
|---|---|---|
| Model Store | граф + JSON, **раскладка канваса отдельно** (`backend/layouts.py`) | граф-БД + версии (CAS) |
| Validator | метамодель, якоря, кросс-аспектные связи, ограничения, **цикл ремонта** | + referential actions, impact |
| Projection | **подзакладки аспектов** (C4 / ER / state / BPMN / …) с нотацией C4, линзы, срезы, уровни | + экспорт спецификаций |
| Reconciler | отчёт сверки (элементы без связей/evidence), аспектные проходы | + автоматическая коррекция |
| Sandbox | Docker без mount, общий том, **одна сессия на прогон** | + лимиты, lease |
| Gateway | токен + подстановка ключа, аудит в JSONL | + квоты, стриминг |
| RAG | загрузка md/csv, маркированные чанки, поиск, **заземлённый ответ с цитатами** | + гибридный retrieval |
| Artifact / приёмка | артефакты, work package, подписанные метки, **граф-дифф и вердикт приёмки**, пакет передачи (handoff), адаптер внешнего CLI-агента | + conformance gate в CI |
| Синтез и шаблоны | **проектирование по ТЗ**, **синтез по описанию в чате**, стартовые шаблоны, **архитектурная документация из модели** | + шаблоны предметных областей |
| Проекты | проекты как первоклассное понятие, рабочая область, история прогонов и чата | + федерация |
| Опыт агента | **диалоги с провенансом**, **обратная связь с атрибуцией** | + профиль, рефлексия, toxicity filter |
| Versioning | — | коммиты, ветки, time-travel |

### 13.1. Карта модулей PoC

| Контейнер (диаграмма выше) | Модули `archide-poc/` |
|---|---|
| API / Orchestrator | `server.py` (stdlib HTTP: UI, API, песочница, шлюз) |
| Model Store | `backend/model_store.py`, `backend/metamodel.py`, `backend/graph_schema.py` |
| Validator / Constraint Engine | `backend/graph_schema.py`, `backend/constraints.py`, `backend/edit.py` |
| Reconciler | `backend/merge.py`, `backend/extract.py`, `backend/aspect_pass.py`, `backend/evaluate.py` |
| Projection Engine | `frontend/app.js` (SVG-рендер, подзакладки, линзы), `backend/analysis.py` |
| Agent Sandbox Runtime | `agent/`, `agents/kilo/`, `backend/sandbox.py`, `backend/runtime.py` |
| LLM Gateway | `backend/gateway.py` |
| RAG / Index, Document Ingestion, Personal Document Store | `backend/docs.py`, `backend/rag.py`, `backend/markers.py` |
| Artifact / приёмка | `backend/generate.py`, `backend/handoff.py`, `backend/codegen.py`, `backend/graphdiff.py` |
| Синтез, проектирование, шаблоны, документация | `backend/design.py`, `backend/templates.py`, `backend/archdoc.py`, `backend/prompt.py` |
| Проекты, опыт агента | `backend/projects.py`, `backend/dialogue.py`, `backend/feedback.py` |
| Запуск прогонов | `backend/runner.py`, `backend/runs.py`, `backend/context.py`, `backend/compress.py` |

## 14. Соответствие концепции

| Раздел `Concept.md` | Контейнеры/компоненты |
|---|---|
| §0–§2 идея, граф, субграфы | Model Store, Metamodel Registry |
| §0.1 динамическая устойчивость | Reconciler (наблюдение → расхождение → коррекция) |
| §18 опыт/самоадаптация агента | Agent Memory & Adaptation (диалоги, профиль, рефлексия, toxicity filter) |
| §3 каркас/уровни, §5 агрегация | Projection Engine, Layout |
| §4 аспекты, §15 аспектный UI | Web UI (**подзакладки аспектов**, дерево C4, раскладка канваса), Projection Engine |
| §9.1 проектирование по ТЗ, §9.2 синтез по описанию | Prompt/design (`backend/design.py`), Runner, ArchDoc |
| §6 срезы | Projection Engine (Slice builder), API |
| §7 требования | Registry, Document Ingestion, RAG |
| §8 строгость/инварианты | Validator / Constraint Engine |
| §9 реконструкция | Sandbox Runtime, Projection Engine |
| §10 редактирование/impact, диалог с агентом | Validator (transactions, impact), Agent Editor (chat), API |
| §11 генерация/метки | Artifact/CI (issuer/verifier) |
| §12 версионность/time-travel | Model Store (Version Store), Web UI (timeline) |
| §13 RAG, персональные документы, метки чанков, заземлённый ответ | RAG/Index, Document Ingestion, Personal Document Store |
| §13.7 заземлённый ответ с цитатами | `backend/rag.py` (генерация + поклеймовая атрибуция) |
| §14 федерация | Registry (GCR), Validator, RAG |
| §15 поверхности/окна | Web UI |
| §16 техконтур | все контейнеры |
| §17 гипотеза/ограничения | — (сквозной) |
