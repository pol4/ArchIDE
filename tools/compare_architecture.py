#!/usr/bin/env python3
"""Сравнить реконструкцию с архитектурной документацией проекта.

    python3 tools/compare_architecture.py --model archide-poc/data/models/MLScan.json \
        --docs /tmp/kilo/MLScan/Arch_v3

Что делает:

1. читает **ожидаемые** элементы из документации — имена узлов mermaid-диаграмм и
   заголовки разделов (это то, что архитектор считает составом системы);
2. читает **полученные** элементы из модели (имя, тип, аспект);
3. сопоставляет по нормализованному имени (регистр, пунктуация, пояснения в
   скобках не учитываются) и по вхождению;
4. печатает покрытие, расхождения и элементы модели, которых нет в документации.

Инструмент только читает файлы; ничего не меняет.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

MERMAID_NODE = re.compile(r'^\s*([A-Za-z0-9_]+)\["([^"]+)"\]', re.M)
MERMAID_SUBGRAPH = re.compile(r'^\s*subgraph\s+[A-Za-z0-9_]+\["([^"]+)"\]', re.M)
HEADING = re.compile(r'^#{2,3}\s+(.+)$', re.M)

STOPWORDS = {
    "обзор", "архитектура", "введение", "итоги", "выводы", "приложение", "ссылки",
    "overview", "architecture", "summary", "conclusion", "appendix", "references",
    "цели", "задачи", "контекст", "требования", "риски", "ограничения",
}


def normalize(name: str) -> str:
    """Привести имя к сравнимому виду: без пояснений в скобках, регистра и знаков."""
    text = str(name or "")
    text = re.sub(r"^\s*\d+[.)]\s*", "", text)   # нумерация заголовков
    text = re.sub(r"\([^)]*\)", " ", text)
    text = text.replace("<br/>", " ").replace("<br>", " ")
    text = text.lower()
    text = re.sub(r"[^a-zа-я0-9]+", " ", text)
    words = [w for w in text.split() if w and w not in STOPWORDS]
    return " ".join(words).strip()


def expected_from_docs(docs_dir: pathlib.Path) -> dict[str, set[str]]:
    """Ожидаемые элементы: узлы и подграфы mermaid + заголовки приложений."""
    nodes: set[str] = set()
    groups: set[str] = set()
    for path in sorted(docs_dir.rglob("*.md")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for _, label in MERMAID_NODE.findall(text):
            if normalize(label):
                nodes.add(label)
        for label in MERMAID_SUBGRAPH.findall(text):
            if normalize(label):
                groups.add(label)
        for heading in HEADING.findall(text):
            candidate = heading.strip().rstrip(":")
            # заголовок-элемент короткий и без глагольных оборотов; длинные фразы
            # («как это работает», «что дальше») элементом системы не являются
            if 3 < len(candidate) <= 60 and len(candidate.split()) <= 6 and normalize(candidate):
                nodes.add(candidate)
    return {"nodes": nodes, "groups": groups}


def produced_from_model(model_path: pathlib.Path) -> list[dict]:
    model = json.loads(model_path.read_text(encoding="utf-8"))
    return [n for n in (model.get("nodes") or []) if n and n.get("name")]


def match(expected: str, produced: list[dict]) -> dict | None:
    """Найти в модели элемент, соответствующий ожидаемому имени."""
    want = normalize(expected)
    if not want:
        return None
    for node in produced:
        name = normalize(node.get("name"))
        if name == want:
            return node
    for node in produced:
        name = normalize(node.get("name"))
        if name and (name in want or want in name):
            return node
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True, help="путь к JSON модели")
    ap.add_argument("--docs", required=True, help="каталог с архитектурной документацией")
    ap.add_argument("--limit", type=int, default=40, help="сколько строк показывать")
    args = ap.parse_args()

    model_path = pathlib.Path(args.model)
    docs_dir = pathlib.Path(args.docs)
    if not model_path.is_file():
        print(f"модель не найдена: {model_path}", file=sys.stderr)
        return 2
    if not docs_dir.is_dir():
        print(f"каталог документации не найден: {docs_dir}", file=sys.stderr)
        return 2

    produced = produced_from_model(model_path)
    exp = expected_from_docs(docs_dir)
    expected = sorted(exp["nodes"])

    found, missing = [], []
    for name in expected:
        node = match(name, produced)
        (found if node else missing).append((name, node))

    print(f"ожидаемых имён в документации: {len(expected)}")
    print(f"элементов в модели:            {len(produced)}")
    print(f"совпало:                       {len(found)}")
    print(f"не найдено:                    {len(missing)}")
    print()

    if missing:
        print(f"— не найдено в модели ({min(args.limit, len(missing))} из {len(missing)}):")
        for name, _ in missing[:args.limit]:
            print(f"    {name[:90]}")

    by_aspect: dict[str, int] = {}
    for node in produced:
        by_aspect[str(node.get("type"))] = by_aspect.get(str(node.get("type")), 0) + 1
    print()
    print("— типы элементов модели:")
    for type_name, count in sorted(by_aspect.items(), key=lambda kv: -kv[1])[:args.limit]:
        print(f"    {type_name:18s} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
