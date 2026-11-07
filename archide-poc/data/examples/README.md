# Примеры моделей

Готовые модели, которые можно открыть или импортировать в ArchIDE.

| Файл | Что показывает |
|---|---|
| `ApprovalHub.json` | сквозной пример: **все четыре уровня C4** (система → контейнеры → компоненты → модули) и **все десять аспектов** (structure, interface, data, behavior, process, deployment, access, quality, aggregation, crosscutting). 121 узел, 90 связей. |

Как воспользоваться:

* **локально**: скопировать файл в `data/models/` и открыть по имени �� панели
  **Projects** (поле model name → Load);
* **в кластере**: `kubectl -n t-archide-app cp ApprovalHub.json \
  $(kubectl -n t-archide-app get pod -l app=archide -o jsonpath='{.items[0].metadata.name}'):/data/models/` — после этого модель видна в списке.

Разбор примера и оба сценария работы с ним — в
[`docs/EXAMPLE_GUIDE.md`](../../../docs/EXAMPLE_GUIDE.md).
