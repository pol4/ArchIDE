# ArchIDE — руководство архитектора

> **Пошаговый гайд по интерфейсу со скриншотами:** `ARCHITECT_GUIDE_UI.md`
> (те же два сценария, но управление только через UI).

Документ описывает две рабочие схемы и **точные шаги** для них:

- **Сценарий A** — исследование готового репозитория с кодом, внесение и валидация
  архитектурных изменений, формирование комплекта документа/заданий произвольному агенту
  кодирования, получение результата в виде нового репозитория и валидация результата по
  изменённому графу.
- **Сценарий B** — разработка архитектуры приложения **с нуля**, генерация артефактов и
  передача их внешнему агенту кодогенерации, чтение результата из отдельного репозитория
  и валидация по графу.

Для каждого шага указано: что делать (UI и API), ожидаемый результат и **текущий статус
поддержки** (✅ поддержано, 🟡 поддержано частично/вручную, ❌ пока нет — с обходным путём
и ссылкой на пункт плана доработок, раздел 4).

---

## 0. Подготовка

**Понятия.** *Модель* — это и есть архитектура: граф узлов и рёбер в каноническом JSON
(`archide-poc/data/models/<Имя>.json`). *Аспекты* (structure, interface, data, behavior,
process, access, deployment, quality) — ортогональные субграфы одной модели. Каждый узел
имеет *якорь* (`parent` по правилам метамодели) и *evidence* (файлы-источники, которыми он
обоснован). *Метки* — подписанные маркеры трассируемости для передачи наружу. *Work
package* — срез графа с критериями приёмки для исполнителя.

**Запуск сервера и UI**

```bash
cd archide-poc && python3 server.py          # UI и API: http://127.0.0.1:8200
# переменные: ARCHIDE_PORT, ARCHIDE_HOST, ARCHIDE_DATA (каталог состояния)
```

**Настройка LLM и агента (UI, панели 2–4):** `Base URL` OpenAI-совместимого эндпоинта,
`API key`, `Model`, `Mode` (`live`/`mock`), `Runner` (`local` — в процессе хоста;
`docker` — агент в песочнице-контейнере). Проверка готовности:

```bash
curl -s localhost:8200/api/agents           # список агентов и лимитов
curl -s localhost:8200/api/metamodel        # типы узлов/рёбер, аспекты, якоря
curl -s localhost:8200/api/models           # сохранённые модели
```

**Где что лежит.** `data/models/<Имя>.json` — модели; `data/runs/<id>.json` — история
запусков с событиями; `data/users/<user>/docs/` — персональные документы (RAG);
`data/local-llm.pid|log` — если поднимался локальный бэкенд. Все эти каталоги
`git-ignored` (в репозиторий попадает только сид-манифест `data/agents.json`).

---

## 1. Сценарий A — существующий репозиторий

### A1. Реконструкция архитектуры по коду ✅

UI: панель **1 · Repository** → URL или локальный путь → агент (`Built-in Reconstructor`
или `Kilo Code CLI`) → **Reconstruct graph**. Лента показывает шаги: базовый C4-проход,
аспект-извлечение и **последовательные аспектные проходы** (`aspect `data`: +N nodes…`).

API:

```bash
curl -s -X POST localhost:8200/api/reconstruct \
  -H 'Content-Type: application/json' \
  -d '{"repo":"https://github.com/ORG/REPO.git","agent":"builtin"}'
# → {"run_id":"..."}   дальше опрос:
curl -s "localhost:8200/api/runs/<run_id>?since=0"
# события: run started → collected N files → pass 1/2 → aspect `x` → reconciliation …
# финал: status=done|invalid|error; модель сохранена под именем проекта
curl -s localhost:8200/api/models/REPO
```

Полезные опции запуска: `{"model":"ArchIDE"}` — **продолжить от сохранённой модели**
(базовый проход пропускается), `{"aspect_list":["behavior","process"]}` — выбрать аспекты
для этого прогона (уже покрытые аспекты пропускаются).

### A2. Изучение модели ✅

- **Аспектные окна** (кнопка открытия окна + селектор аспекта): каждый аспект рисуется
  своей нотацией, вид переключается (`,` / `.`): structure `c4`, interface `api`/`sequence`,
  data `er`, behavior `state`, process `bpmn`, deployment `tree`, quality/access `table`.
  Выбранный вид запоминается для аспекта; окно сворачивается и тянется за размер; `⤓ all`
  выгружает все представления одним SVG, `⤓` — один вид.
- **Навигация**: `↑`/`↓` — сколько слоёв вокруг фокуса показывать; селектор **уровня C4**
  (L1…L4) — уровень целиком; поиск узла по имени (включает нужный аспект и выделяет узел);
  `+`/`−`/`⌂` — зум и «домой».
- **Срезы и метрики**:

```bash
curl -s -X POST localhost:8200/api/models/REPO/slice \
  -H 'Content-Type: application/json' \
  -d '{"scope":"component://backend/runner","depth":2,"direction":"down"}'
curl -s localhost:8200/api/models/REPO/coverage        # покрытие требований
curl -s localhost:8200/api/models/REPO/aggregations
curl -s -X POST localhost:8200/api/eval -H 'Content-Type: application/json' \
  -d "{\"graph\": $(curl -s localhost:8200/api/models/REPO)}"
# metrics: score, validator_ok, canonical_stable, roundtrip_ok, slice_deterministic,
#          aspects.<аспект>.{elements,coverage,orphans,without_evidence}, aspect_summary
```

- **Документы (RAG)**: панель Documents — загрузка md/csv (`POST /api/docs`), поиск и
  проверка утверждений (`POST /api/rag/search`, `/api/rag/verify`), подписанные чанк-метки.

### A3. Изменение архитектуры ✅

Все правки проходят через транзакцию с gate (валидация + ограничения) и referential
actions (auto-repair ссылок). Ручной путь — UI (канвас/формы/чат), программный — API:

```bash
HASH=$(curl -s localhost:8200/api/models/REPO | python3 -c 'import sys,json;print(json.load(sys.stdin).get("hash",""))')
curl -s -X POST localhost:8200/api/models/REPO/edit -H 'Content-Type: application/json' -d '{
  "user": "architect", "expected_hash": "'"$HASH"'", "auto_repair": true,
  "operations": [
    {"op": "add_node",  "node": {"id":"component://backend/cache","type":"Component",
                                 "name":"Cache","parent":"container://backend","evidence":[]}},
    {"op": "add_edge",  "edge": {"source":"component://backend/cache",
                                 "target":"datastore://config-store","type":"reads"}},
    {"op": "update_node","id":"component://backend/runner","patch":{"name":"Reconstruction Runner"}}
  ]}'
```

Оценка и сопровождение изменений:

```bash
curl -s -X POST localhost:8200/api/models/REPO/impact   -d '{"node":"component://backend/cache"}'
curl -s -X POST localhost:8200/api/models/REPO/review
curl -s -X POST localhost:8200/api/models/REPO/reconcile -d '{"apply": true}'   # авто-починка
curl -s -X POST localhost:8200/api/models/REPO/constraints -d '{"constraints":[...]}'
```

Операции правок: `add_node`, `update_node`, `remove_node`, `add_edge`, `remove_edge`.
`expected_hash` защищает от гонок (409 при расхождении), владение узлами — 403.

### A4. Валидация изменённой модели ✅

```bash
curl -s localhost:8200/api/models/REPO/validate      # {"validation":{ok,errors}, "violations":[...]}
curl -s -X POST localhost:8200/api/validate -d "{\"graph\": $(cat archide-poc/data/models/REPO.json)}"
curl -s -X POST localhost:8200/api/eval     -d "{\"graph\": $(cat archide-poc/data/models/REPO.json)}"
```

Жёсткие инварианты (дерево вложенности, якоря аспектов, кросс-аспектные правила рёбер,
один корень) + декларативные ограничения модели + метрики по аспектам (покрытие типов,
сироты, элементы без evidence).

### A5. Комплект для агента кодирования ✅ (собирается по частям)

```bash
# 1) задание: срез графа + критерии приёмки по цели
curl -s -X POST localhost:8200/api/models/REPO/workpackage -d '{"node":"component://backend/cache"}'
# 2) артефакты (спецификации/скелеты)
curl -s -X POST localhost:8200/api/models/REPO/generate \
  -d '{"kinds":["openapi","ddl","jsonschema","skeleton"]}'
# 3) подписанные метки трассируемости (для проверки «что сделано»)
curl -s -X POST localhost:8200/api/models/REPO/markers -d '{"node":"component://backend/cache"}'
# 4) матрица покрытия (цель ↔ артефакты/метки)
curl -s -X POST localhost:8200/api/models/REPO/coverage-matrix
```

UI: панель **8 · Generate** (окна «Artifacts» и «Coverage»).

### A6. Передача внешнему агенту кодирования 🟡 (сейчас — вручную)

Сформированный комплект передаётся агенту как обычное задание в его репозитории. Что
класть в задание: (1) контекст — `work package` (срез + критерии) и/или срез графа
(`/api/models/REPO/slice`); (2) спецификации из `/generate`; (3) метки из `/markers`
(их агент обязан вставить в артефакты/код, чтобы результат был проверяем); (4) требования
к результату — ветка/PR и команда сборки/тестов. Результат принимается как **новый
репозиторий** (или ветка) и фиксируется по коммиту.

*Обходной путь:* делать это через тот же интерфейс, которым пользуетесь для кода
(например, Kilo Code), указав в промпте путь к сохранённому пакету. Автоматического
адаптера «пакет → запуск внешнего агента → сбор репозитория» в системе пока нет (см. Ф8.1,
Ф8.2 в разделе 4).

### A7. Приёмка результата по изменённому графу 🟡 (частично)

```bash
# 1) реконструировать полученный репозиторий
curl -s -X POST localhost:8200/api/reconstruct \
  -d '{"repo":"https://github.com/ORG/NEW-REPO.git","agent":"builtin"}'
# 2) сопоставить с целевой моделью
curl -s -X POST localhost:8200/api/merge/candidates -d "{\"code\": <граф результата>, \"docs\": <целевой граф>}"
# 3) трассируемость артефактов: конформанс по меткам
curl -s -X POST localhost:8200/api/models/REPO/conformance-loop -d '{"node":"component://backend/cache"}'
curl -s -X POST localhost:8200/api/models/REPO/conformance -d '{"markers": [...]}'
```

Что уже даёт ответ: покрытие требований, конформанс по меткам, метрики качества
реконструкции результата, список неоднозначных соответствий.

Чего **не хватает**: отчёта **«целевая модель ↔ репозиторий результата»** (что появилось,
что пропало, что изменено, по элементам и аспектам, с вердиктом). Сегодня это собирается
вручную из `eval`/`coverage`/`candidates` (см. Ф8.3).

---

## 2. Сценарий B — разработка архитектуры с нуля

### B1. Создание модели ❌/🟡 (нет «design mode» и шаблонов)

Готового сценария «архитектура по ТЗ без кода» пока нет: базовый проход реконструкции
требует **исходных файлов**. Сегодняшние пути:

- создать модель программно и наполнить её правками (операции A3):

```bash
curl -s -X PUT localhost:8200/api/models/NewApp -H 'Content-Type: application/json' -d '{
  "graph": {"nodes":[{"id":"system://newapp","type":"System","name":"NewApp","parent":null,
                      "description":"...","evidence":[]}],
            "edges":[]}}'
curl -s -X POST localhost:8200/api/models/NewApp/edit -d '{
  "user":"architect","auto_repair":true,"operations":[
    {"op":"add_node","node":{"id":"container://newapp/api","type":"Container","name":"API",
                             "parent":"system://newapp","evidence":[]}},
    {"op":"add_node","node":{"id":"component://newapp/orders","type":"Component","name":"Orders",
                             "parent":"container://newapp/api","evidence":[]}}]}'
```

- либо разложить ТЗ и требования как документы и реконструировать по ним — **требуется
  доработка** (Ф8.4: проход только по документации).

UI-пути: канвас (добавить/связать/переименовать узел), чат с агентом («добавь компонент
кеша в backend»), импорт готового JSON (`Load`/импорт файла модели).

### B2. Валидация и метрики ✅
Как в A4 (для модели без реконструкции покрытие требований и конформанс считаются так же).

### B3. Генерация артефактов и заданий ✅
Как в A5: `workpackage`, `generate`, `markers`, `coverage-matrix`. Именно эти artifacts и
передаются внешнему кодогенератору (спецификации + критерии приёмки + метки).

### B4. Передача агенту кодогенерации 🟡
Как в A6 (вручную; адаптер — Ф8.2).

### B5. Чтение результата и валидация по графу 🟡
Как в A7: получить репозиторий результата → `POST /api/reconstruct` → сравнить с моделью
(`eval`, `coverage`, `candidates`, `conformance-loop`); при расхождениях — правки модели
(B2) и повторная передача. Автоматический граф-дифф — Ф8.3.

---

## 3. Шпаргалка API

| Метод | Путь | Тело | Что делает |
|---|---|---|---|
| POST | `/api/reconstruct` | `{repo, agent, model?, aspect_list?}` | запуск реконструкции (прогон) |
| GET | `/api/runs/<id>?since=N` | — | события и статус прогона |
| POST | `/api/runs/<id>` | — | отмена прогона |
| GET | `/api/models` | — | список моделей |
| GET/PUT | `/api/models/<n>` | `{graph}` | чтение/замена модели |
| POST | `/api/models/<n>/edit` | `{operations,user,expected_hash?,auto_repair?}` | транзакция правок с gate |
| POST | `/api/models/<n>/impact` | `{node}` | последствия правки узла |
| POST | `/api/models/<n>/review` | — | ревью-отчёт по модели |
| POST | `/api/models/<n>/reconcile` | `{apply?}` | авто-починка целостности |
| GET | `/api/models/<n>/validate` | — | валидация + нарушения ограничений |
| GET | `/api/models/<n>/coverage` | — | покрытие требований |
| GET | `/api/models/<n>/aggregations` | — | агрегационные интерфейсы |
| POST | `/api/models/<n>/slice` | `{scope,depth,direction,aspects?}` | срез модели |
| POST | `/api/models/<n>/constraints` | `{constraints}` | декларативные ограничения |
| POST | `/api/models/<n>/generate` | `{kinds?}` | спецификации и скелеты |
| POST | `/api/models/<n>/workpackage` | `{node}` | задание исполнителю (срез + критерии) |
| POST | `/api/models/<n>/markers` | `{node}` | подписанные метки трассируемости |
| POST | `/api/models/<n>/coverage-matrix` | — | матрица «цель ↔ артефакты» |
| POST | `/api/models/<n>/conformance` | `{markers}` | конформанс по меткам |
| POST | `/api/models/<n>/conformance-loop` | `{node}` | конформанс + repair-цикл |
| POST | `/api/validate` | `{graph, files?}` | формальная валидация (метамодель+якоря+кросс) |
| POST | `/api/eval` | `{graph}` | метрики качества (в т.ч. по аспектам) |
| POST | `/api/merge/candidates` | `{code, docs}` | сопоставление двух графов |
| POST | `/api/slice` | `{graph,scope,depth,direction,aspects?}` | срез произвольного графа |
| POST | `/api/docs` | `{user,name,kind,content}` | загрузка документа (RAG) |
| POST | `/api/rag/search`, `/api/rag/verify` | `{...}` | поиск и проверка утверждений |

## 3а. Проверено на практике (сценарий B, «Trip Approval»)

Полный прогон сценария B на демо-приложении «согласование командировок» (все аспекты):

1. **Модель собрана** (`PUT /api/models/TripApproval`): 55 узлов / 33 ребра, валидна;
   покрыто 10 аспектов; покрытие требований 3/3 (`GET .../coverage`).
2. **Комплект**: `POST .../generate` → спецификации; `POST .../workpackage
   {"node":"component://trip/api"}` → срез 10 узлов + **5 критериев приёмки**;
   `POST .../markers` → **10 подписанных меток**.
3. **Кодогенерация внешним агентом**: `POST .../codegen {"node":"component://trip/api",
   "target":"triprepo"}` → агент вернул карту файлов, репозиторий создан и закоммичен
   (`data/generated/triprepo`, коммит `e07bbc8`): `trip_api.py` (190 строк, HTTP-сервер с
   операциями `createTrip/submitTrip/approveTrip/rejectTrip`), тесты, `README`,
   `pyproject.toml`; **метки трассируемости вставлены в код** (по 10 в `trip_api.py`/`app.py`).
4. **Приёмка по графу**: доставленный репозиторий реконструирован
   (`POST /api/reconstruct {"repo":"…/triprepo","aspect_list":[]}` → модель `triprepo`,
   88 элементов) и сопоставлен с целевой моделью:
   `POST /api/graphdiff {"target_model":"TripApproval","result_model":"triprepo"}` →
   **вердикт `gaps`**: matched 14/55 (coverage 0.26), missing 41, extra 77, разбивка по
   аспектам (`process` 8 missing, `access` 5, `behavior` 6, `interface` 2 + 32 extra…).

Вывод прогона (честный): агент реализовал **узкий срез** (компонент API), а целевая модель
описывает всё приложение — дифф это корректно показал. Для точной приёмки нужны
сопоставление по маркерам/evidence и оценка в границах среза (Ф8.3b), а для длинных
аспектных проходов — управление контекстом сессии (Ф3.11).

**Полная реконструкция в docker-режиме** (репозиторий ArchIDE, 8 вызовов LLM в одном
контейнере): `status=done`, **443 узла / 501 ребро**, валидна; аспектные проходы дали
`data +8/+15`, `interface +23/+29`, `behavior +18/+26`, `deployment +3/+6`, `access +6/+4`.
Два прохода упали честно и это ушло в план: `process` — **«The model ran out of context
space»** (накопленный контекст непрерывной сессии превысил окно модели → Ф3.11), `quality` —
невалидный ответ модели (→ Ф8.2b/повтор).

## 4. Что поддержано сейчас, а что уходит в план

| Шаг | Статус | Что не хватает | Пункт плана |
|---|---|---|---|
| A1–A2 исследование | ✅ | — | — |
| A3–A4 правки и валидация | ✅ | — | — |
| A5 комплект для исполнителя | ✅ | единый **пакет передачи** одним вызовом | **Ф8.1** |
| A6 запуск агента кодогенерации | 🟡 | агент кодогенерации есть (`/codegen` + кнопка в UI); не хватает запуска **произвольного внешнего** CLI-агента и устойчивости его ответа | **Ф8.2**, **Ф8.2b** |
| A7 приёмка результата по графу | 🟡 | граф-дифф есть (`/api/graphdiff` + кнопка); нужно точное сопоставление (маркеры/evidence, границы среза) | **Ф8.3**, **Ф8.3b** |
| B1 архитектура с нуля | ❌/🟡 | проход **только по документации/ТЗ**, шаблоны и мастер новой модели | **Ф8.4**, **Ф8.5** |
| B3–B5 генерация и приёмка | ✅/🟡 | то же, что A5–A7 (+ управление контекстом длинных проходов) | Ф8.1–Ф8.3b, **Ф3.11** |

## 5. Дисциплина работы с моделью

1. **Модель — единственный источник.** Диаграммы и спецификации производны от графа и не
   хранятся отдельно.
2. **Правки — только операциями** (`/edit`) или через UI: транзакция, gate, referential
   actions, аудит. Прямое редактирование JSON — исключение (импорт/диагностика).
3. **Передавайте `expected_hash`** при правках — защита от гонок (409) и аудируемость.
4. **После правок всегда** `.../validate` + `/api/eval`: инварианты, ограничения, метрики
   аспектов (покрытие типов, сироты, элементы без evidence).
5. **Каждый элемент с evidence.** Для реконструкции это файлы-источники; для
   проектирования — документы/решения; при передаче наружу метки подписываются ключом
   обмена (`/markers`, проверка — `/api/markers/verify`).
6. **Приёмка результата — по графу, а не по «на глаз»:** реконструируйте полученный
   репозиторий, сверяйте аспекты и покрытие, фиксируйте вердикт (и дорабатывайте модель или
   задание при расхождениях).
