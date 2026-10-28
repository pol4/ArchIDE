"""Architecture synthesis from a human description (Ф11.1).

The architect states the task in the chat ("a service that takes orders, a manager
approves them, accounting books the trip"), optionally attaches requirement
documents, and ArchIDE runs a **design** pass: the material is the description
plus those documents, and the result is a full graph — not a reconstruction of
existing code.

The material is written into a scratch directory that looks exactly like a
repository of documents, so the normal design pass (Ф8.4) can consume it without
a special path in the runner.
"""
from __future__ import annotations

import pathlib
import re
import shutil

from . import config as config_mod

DESCRIPTION_FILE = "00-description.md"

DESCRIPTION_HEADER = """# Задание на архитектуру (постановка от архитектора)

Ниже — описание системы, сформулированное человеком в чате с агентом. Код
отсутствует: архитектуру нужно **спроектировать** по этому описанию и приложенным
документам. Опирайся только на текст ниже и на приложенные документы; каждый
элемент графа должен опираться на конкретное место описания или документа.

"""


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(name or "")).strip("-") or "system"


def material_root(name: str) -> pathlib.Path:
    # the model is named after the last path component, so the synthesis ends up
    # saved under the name the architect asked for
    return pathlib.Path(config_mod.DATA_DIR) / "scratch" / "design" / _safe(name)


def material(name: str, description: str, documents: list[dict] | None = None,
             keep: bool = True) -> tuple[pathlib.Path, list[str]]:
    """Write the synthesis material and return (directory, file names).

    ``documents`` is a list of ``{"name", "content"}`` — usually the requirement
    documents the architect attached in the chat.
    """
    text = str(description or "").strip()
    if not text:
        raise ValueError("a description is required to synthesise an architecture")
    root = material_root(name)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    (root / DESCRIPTION_FILE).write_text(DESCRIPTION_HEADER + text + "\n", encoding="utf-8")
    written = [DESCRIPTION_FILE]
    for index, doc in enumerate(documents or [], start=1):
        doc_name = str((doc or {}).get("name") or f"document-{index}.md")
        content = str((doc or {}).get("content") or "")
        if not content.strip():
            continue
        safe = _safe(pathlib.Path(doc_name).stem) + (pathlib.Path(doc_name).suffix or ".md")
        file_name = f"{index:02d}-{safe}"
        (root / file_name).write_text(content, encoding="utf-8")
        written.append(file_name)
    return root, written


def cleanup(name: str) -> None:
    shutil.rmtree(material_root(name), ignore_errors=True)
