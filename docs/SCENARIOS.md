# ArchIDE — основные сценарии работы

Статус: **согласовано** (S1–S10). Тесты — в `tests/` (API + браузер), обязательные
правила — в [`../AGENTS.md`](../AGENTS.md). Сквозной прогон: `bash tests/run_e2e.sh`.

Сценарии описывают пользовательские потоки. Тестирование идёт **в двух режимах**:
**браузер** (headless Chrome, реальные действия пользователя) и **API** (HTTP).
Тестовый профиль детерминирован: `mode=mock`, `runner=local`, стаб-docker — без
реального Docker и LLM.

## S1 — Настройка и сохранение конфигурации
- **Цель:** задать LLM (base_url/api_key/model), runner и бюджет контекста; настройки
  не теряются при перезагрузке.
- **Шаги:** открыть UI → заполнить поля (автосохранение) → жёсткая перезагрузка
  (Ctrl+Shift+R).
- **Ожидание:** значения восстановлены из `data/config.json`.
- **Покрытие:** API `S1 config round-trip` / `S1 config persisted to disk`; браузер
  (заполнение полей и запуск в `runLive`).

## S2 — Реконструкция из исходного кода (один проход)
- **Цель:** получить граф архитектуры по репозиторию.
- **Шаги:** ввести адрес репозитория → выбрать агента → «Reconstruct graph» →
  наблюдать активность.
- **Ожидание:** статус `Done`, граф отрисован, модель валидна, в ленте есть
  `pass 1/2`; **модель сохранена канонически** (`model: <hash>`, `GET /api/models`)
  под именем проекта (суффикс `.git` отбрасывается, `…/ArchIDE.git` → `ArchIDE`),
  поэтому её можно загрузить в UI по имени;
  граф **аспект-осознан** (содержит интерфейсы/данные из кода), при этом
  аспект-извлечение **якорится к существующим узлам** и не создаёт висячих
  `parent`/рёбер (граф остаётся валидным после слияния аспектов).
  Затем идёт **последовательная реконструкция аспектов (Ф3.5)** — после
  структурного C4-прохода каждый аспект (`data`, `interface`, `behavior`,
  `process`, `deployment`, `access`, `quality`) строится отдельным шагом
  агента и мёржится в граф: принимаются только новые узлы типов этого аспекта,
  с якорем на существующий узел, только разрешённые аспектом рёбра; результат
  формально валидируется, при ошибке шаг откатывается; каждый шаг пишется в
  ленту агента (`aspect \`X\`: +N nodes, +M edges`, пропуски — отдельным
  сообщением). Так состояния/процессы/развёртывание появляются в графе, и
  соответствующие аспектные окна рисуют диаграммы.
  Прогон идёт в **одной непрерывной сессии агента (Ф3.10)**: в docker-режиме контейнер
  поднимается один раз и репозиторий выгружается однократно; в local-режиме это **одна
  непрерывная беседа** — источники отправляются один раз, а каждый аспектный проход
  идёт коротким сообщением в той же беседе (без повторной отправки репозитория).
  Проход по документации использует свой набор материалов, поэтому идёт в отдельной
  сессии. Контекст каждого аспектного прохода формируется **под аспект (Ф3.6)**: в
  промпт попадают только релевантные файлы (отбор по ключевым словам/путям, с запасным
  вариантом, чтобы проход не остался без материала) и **срез графа** — элементы этого
  аспекта плюс виды узлов, к которым он может якориться.
  Проходы **устойчивы и возобновляемы (Ф3.7)**: аспект, который граф уже несёт, не
  пересобирается; прирост одного прохода ограничен (лишнее отбрасывается с записью в
  ленту); запуск можно **продолжить от сохранённой модели** (`model` в запросе, базовый
  проход пропускается) и задать свой `aspect_list`. По завершении выдаётся **сверка
  (Ф3.9)**: сколько элементов осталось без связей и без evidence (с разбивкой по
  аспектам), а метрики качества по аспектам доступны в `/api/eval` (**Ф3.8**).
  Найденное **исправляется агентом** тем же безопасным шагом (**Ф3.9-коррекция**):
  агенту передаются пробелы, а мёрж принимает только заполнение `evidence` и новые
  связи между существующими элементами — удалять, переименовывать и добавлять
  элементы коррекция не может (это фиксируется в ленте).
- **Покрытие:** API `S2 run completes/graph valid/events`, `Ф2.1 …`, `Ф3.1 extract/
  aspect-aware`, `Ф3.1 extraction has no dangling parents/edges`, `Ф3.1 extraction
  anchors an owned file to its component`, `Ф3.1 extraction prefers the most
  specific owner`, `Ф3.1 extraction covers all aspects`, `Ф3.1 extracted aspect
  nodes are anchored validly`, `Ф3.1 extraction survives a tiny context budget`,
  `Ф3.1 extraction tolerates a cyclic parent chain`, `Ф3.1 extraction reports
  unanchored elements`, `Ф3.1 augment preserves pre-existing edges`,
  `Ф3.5 aspects are reconstructed in sequential passes`, `Ф3.5 an out-of-aspect
  node is rejected by the merge`,
  `Ф3.10 the local session sends the sources once`, `Ф3.10 later passes continue the
  same conversation`,
  `Ф3.6 aspect pass gets only the relevant sources`, `Ф3.6 aspect graph summary keeps
  anchors and own elements only`, `Ф3.6 the docker session does not re-send the sources`,
  `Ф3.7 a run resumes from a saved model`, `Ф3.7 an aspect the graph already carries is skipped`,
  `Ф3.7 an oversized aspect addition is capped`, `Ф3.8 per-aspect metrics are reported`,
  `Ф3.8 aspect coverage summary is reported`, `Ф3.9 a reconciliation report is emitted for each run`,
  `Ф3.9 the agent fills in missing evidence and relationships`, `Ф3.9 a correction cannot
  remove or add elements`,
  `Ф3.4 local runner validates against the formal rules`,
  `Ф3.4 local validator URL prefers loopback`, `Ф1.4 legacy .git model is migrated`,
  `Ф1.4 model name strips the .git suffix`,
  `Ф1.4 model name from a local path`; браузер `S2 run finished`, `S2 graph rendered`,
  `Ф2.1 model hash`.

## S3 — Двухпроходная реконструкция код + документация
- **Цель:** сравнить модель, восстановленную по коду и по документации.
- **Шаги:** включить «compare with documentation» → запуск → переключение фильтра
  источников `all / code / docs`.
- **Ожидание:** выполнен проход по докам; элементы с `sources:["code","docs"]`;
  отклонения помечены; фильтр меняет видимый набор.
- **Покрытие:** API `S3 docs pass`, `S3 merged sources`, `S3 deviations flagged`,
  `Ф3.3 identity candidates`; браузер `S3 source filter …`.

## S4 — Навигация по уровням и аспектам
- **Цель:** смотреть систему по одному уровню и по аспектам, в т.ч. **несколько
  аспектов одновременно**.
- **Шаги:** просмотр уровня → клик по узлу с `⊕` (zoom in) → возврат (zoom out /
  крошки); переключение аспектов (чипы), выбор нескольких аспектов.
- **Ожидание:** крошки отражают путь; переход меняет набор узлов уровня; аспектный
  фильтр меняет видимый набор; несколько аспектов видны одновременно (overlay и
  **split-панели**); по запросу строятся **срезы/представления** (scope/depth/direction).
  Навигация по уровням работает кнопками **⇧ (уровень вверх) / ⇩ (внутрь выбранного
  узла)** и кликом по крошкам; **+ / − масштабируют вид**, **⌂** возвращает к целой
  системе (сброс фокуса и посадка вида). Внутри блока на канвасе видна **сводка
  содержимого** (сколько и каких дочерних элементов, напр. `3× Component`).
  Каждое **аспектное окно рисует специализированную нотацию** и имеет **переключатель
  вида** (`view type`): structure `c4` (**границы контейнеров**, детали в блоках),
  interface `api`, data `er` (сущности с атрибутами, хранилище-цилиндр, связи
  **ромбами** с подписью типа), behavior `state` (**стрелки переходов** между
  состояниями + маркер входа + события), process `bpmn` (**потоки по реальным
  `flows_to`**: задачи/активности/шлюзы), deployment (**слои с границами**
  Environment/Node/Runtime/InfraResource + вложения), quality/access `table`, плюс общий
  `graph`; у interface/process есть **`sequence`** — диаграмма последовательностей,
  производная из рёбер `calls`/`flows_to`/`publishes`/`subscribes` и операций
  (лифлайны участников, нумерованные сообщения, фильтр пары «from/to»); вид
  выгружается в **SVG**. Виды удобны в работе (**Ф2.12**): выбранный вид запоминается для
  аспекта, окно можно свернуть (и потянуть за размер), вид листается горячими клавишами
  `,`/`.`, а все представления выгружаются **одним SVG**. Навигация (**Ф2.13**):
  селектор **уровня C4** показывает уровень целиком (L1–L4), а **поиск узла** по имени
  находит его, включает нужный аспект, выделяет и раскрывает уровень родителя.
  Экспорт результата всегда пишет **полную модель** (даже когда активен срез), а
  импорт файла **спрашивает подтверждение** перед перезаписью существующей модели.
- **Покрытие:** браузер `S4 breadcrumb shown`, `S4 drill-down …`, `Ф2.5 aspect
  chips/default/locked`, `Ф2.6 multi-aspect`, `Ф2.6b split …`, `Ф2.7 slice applied/reset`,
  `Ф2.8 interface view lists operations`, `Ф2.8 data view shows attribute rows and a
  store cylinder`, `Ф2.8 behavior view draws states and events`, `Ф2.8 process view
  draws tasks and a gateway`, `Ф2.8 deployment view renders layered boundaries`, `Ф2.8
  quality view renders a table`, `Ф2.8 structure view shows in-box child detail`,
  `Ф2.8 view-type selector switches the notation`, `Ф2.8 aspect-views fixture model saved`,
  `Ф2.10 sequence fixture model saved`, `Ф2.10 sequence view draws lifelines and messages`,
  `Ф2.10 sequence participant filter narrows the messages`,
  `Ф2.11 notation fixture model saved`, `Ф2.11 state machine draws transitions as arrows`,
  `Ф2.11 process flows follow the declared flows_to edges`, `Ф2.11 deployment renders layered
  boundaries with containment`, `Ф2.11 data relationships are drawn as ER diamonds`,
  `Ф2.11 structure draws container boundaries (C4)`,
  `Ф2.12 the view type is remembered per aspect`, `Ф2.12 hotkeys switch the view type`,
  `Ф2.12 a window can be minimized`, `Ф2.12 every aspect view exports as one SVG`,
  `Ф2.13 level jump shows one whole C4 level`, `Ф2.13 node search selects and reveals a node`,
  `Ф2.9 zoom-in changes the view scale`, `Ф2.9 home fits the whole view again`, `Ф2.9
  level-up returns to the parent level`, `Ф2.9 level controls exist`, `Ф2.9 level
  controls change how many layers are visible`, `Ф2.9 home resets the aspect filter`,
  `Ф2.9 home shows several levels at once`,
  `export downloads the graph as a file`, `export writes the full model even in slice view`,
  `import loads the graph and saves it as a model`, `import asks before overwriting an
  existing model`, `dismissed import leaves the existing model intact`;
  API `Ф2.7 slice …`.

## S5 — Активность агента и прерывание
- **Цель:** видеть ход работы и уметь остановить агента.
- **Шаги:** запуск → лента событий (status/reasoning/output/log/error) → «Stop».
- **Ожидание:** лента наполняется; остановка переводит запуск в `cancelled`.
- **Покрытие:** API `S5 cancel stops a running agent`; браузер `S5 activity feed has events`.

## S6 — Среда агентов: выбор, ресурсы, том, шлюз
- **Цель:** переключать агентов (встроенный / Kilo Code CLI), задавать ресурсы песочницы,
  хранить общее состояние, ходить в LLM через шлюз с lease/аудитом и вести историю запусков.
- **Шаги:** выбрать агента; проверить общий Docker-volume; проверить шлюз (lease, аудит);
  проверить сохранённые запуски (`/api/runs`).
- **Ожидание:** агенты описываются **манифестом** (`data/agents.json`, `kind` + лимиты);
  лимиты применяются к `docker run`; контейнер получает **lease**, а не мастер-токен;
  шлюз ведёт **аудит** без секретов и применяет **квоту на lease**; том доступен и
  сбрасывается; **запуски сохраняются на диск** и доступны по id (restart-safe).
  В **docker-режиме** контейнер агента поднимается **один раз на прогон** (непрерывная
  сессия, Ф3.10): репозиторий выгружается однократно, а базовый проход, **все аспектные
  проходы** и сравнение с документацией идут как задания в этой же сессии; сессия
  закрывается по завершении/отмене.
- **Покрытие:** API `S6 agents registry`, `S6 volume …`, `Ф1.1 …`, `Ф1.2 …`,
  `Ф1.3 lease/audit/quota`, `Ф1.4 runs list/persisted/restart-safe`, `Ф1.5 scratch/GC`,
  `Ф3.10 a single container is started for the whole run`, `Ф3.10 base and aspect passes
  share one agent session`; браузер `Ф1.1 agent manifest limits shown`.

## S7 — Бюджет контекста и сжатие
- **Цель:** ограничить объём контекста для агента.
- **Шаги:** задать `max_chars` / `compress_at` / `strategy` → запуск.
- **Ожидание:** в логе — отчёт о сжатии (`context: … -> … chars`).
- **Покрытие:** браузер `S7 context report present`; API — через конфиг и meta.

## S8 — Валидация графа и цикл доработки
- **Цель:** гарантировать корректность графа.
- **Шаги:** запуск → панель Validation; настройка `max_repairs`.
- **Ожидание:** модель валидна (нет ошибок `✕`); агент дорабатывает до устранения
  ошибок; проверяются **якоря аспектов** и **кросс-аспектные связи** (реестр
  самосогласован); **агрегационные интерфейсы** имеют членов и валидный контракт;
  считается **покрытие требований** (трассировка).
- **Покрытие:** API `S8 validate tool rejects unknown job`, `S8 validator accepts/rejects`,
  `Ф2.2 …`, `Ф2.3 aggregation …`, `Ф2.4 coverage …`, `Ф3.2 evidence …`, `Ф3.4 eval …`;
  браузер `S8 validation panel reports a valid model`, `Ф2.4 coverage shown`.

## S9 — Устойчивость к ошибкам
- **Цель:** понятная ошибка при недоступном LLM/битом ответе.
- **Шаги:** указать недоступный `base_url` → запуск.
- **Ожидание:** запуск в статусе `error` с осмысленным сообщением.
- **Покрытие:** API `S9 failed run surfaces an error`.

## S13 — Генерация и валидация кода по модели
- **Цель:** из модели генерировать артефакты и проверять их соответствие (gate).
- **Шаги:** выбрать модель → **Generate** (openapi/ddl/schema/skeleton) → пакет работ
  (срез+критерии) → выпуск **подписанных меток** → **conformance** (метки+покрытие) →
  **repair-цикл** (gaps/подсказки).
- **Ожидание:** артефакты генерируются детерминированно; пакет работ содержит срез и
  критерии; метки подписаны и проверяются; матрица покрытия считается; conformance-цикл
  возвращает gaps/repair.
- **Покрытие:** API `Ф5.1 …`, `Ф5.2 …`, `Ф5.3 …`, `Ф5.4 …`, `Ф5.5 …`; браузер
  `Ф5.1 generate`, `Ф5.4 coverage`.

## S12 — Редактирование модели и непротиворечивость
- **Цель:** править модель (add/update/remove) с автоматическим сохранением
  непротиворечивости.
- **Шаги:** загрузить модель на канву → **визуально** добавить узел (`＋`), переименовать
  (выбор → Rename) или удалить (Delete); открыть **окна аспектов** и править там; всё
  идёт через gate; удаление — с referential actions; impact/auto-repair.
- **Ожидание:** валидные правки применяются и сохраняются; невалидные блокируются (422);
  `cascade`/`restrict` работают; impact виден; auto-repair чинит висячие ссылки.
- **Покрытие:** API `Ф4.1 edit/gate`, `Ф4.2 cascade/restrict`, `Ф4.3 impact/auto-repair`,
  `Ф4.4 constraints/validate`, `Ф4.5 ownership/conflict`, `Ф4.9 review`, `Ф4.10 reconcile`;
  браузер `Ф4.6 visual edit/aspect windows`, `Ф4.7 chat edit`, `Ф4.8 custom aspect`.

## S11 — Персональные документы и маркированный RAG
- **Цель:** архитектор отдаёт документы (Markdown/CSV) как требования/справку; они
  хранятся в личной истории, ищутся в RAG, а ответы **верифицируются по меткам чанков**.
- **Шаги:** в окне коммуникации **подключить документ** (Attach, md/csv → автоматически в
  RAG) → задать вопрос-промпт (это и есть RAG-поиск) → при необходимости нажать
  **«→ prompt»** у найденного чанка, чтобы присоединить его к промпту → закладка
  **Answer to verify** для проверки заземления; **ресайз** верхней границы окна.
- **Ожидание:** оригинал в личной истории (изоляция per-user); чанки промаркированы
  подписью; ответ RAG отделяет восстановленное от сгенерированного; требования
  извлекаются из документа.
- **Покрытие:** API `Ф7.1 …`, `Ф7.2 …`, `Ф7.3 …`, `Ф7.4 …`, `Ф7.5 …`; браузер
  `Ф7.1 uploaded`, `Ф7.2 search`, `Ф7.4 grounding`, `Ф4.6b comm tabs/prompt/resize`.

## S10 — Демо-режим (offline)
- **Цель:** показать UI без сервера (для доков/скриншотов).
- **Шаги:** открыть `frontend/index.html?demo=1` (плюс `&mode=code|docs`, `&focus=<id>`).
- **Ожидание:** граф и лента отрисованы из `demo.json`.
- **Покрытие:** API `S10 demo.json present and valid`; браузер `S10 demo renders graph nodes`.

## Матрица «сценарий → тесты»

| Сценарий | API | Браузер |
|---|---|---|
| S1 | ✔ | ✔ |
| S2 | ✔ | ✔ |
| S3 | ✔ | ✔ |
| S4 | — | ✔ |
| S5 | ✔ | ✔ |
| S6 | ✔ | (покрыт API) |
| S7 | ✔ | ✔ |
| S8 | ✔ | ✔ |
| S9 | ✔ | — |
| S10 | ✔ | ✔ |

## Обязательства
- Набор сценариев и тестов **согласуется** и расширяется при появлении новой
  функциональности (см. `../AGENTS.md`).
- Сквозной e2e (`tests/run_e2e.sh`) прогоняется **после каждой достаточно крупной
  доработки**; результат фиксируется.
