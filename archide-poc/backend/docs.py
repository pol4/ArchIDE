"""Personal documents + chunked RAG (Ф7).

Architects attach Markdown/CSV documents as requirements/reference. Originals are
kept in the user's **personal history** (per-user isolation); each chunk gets a
**signed marker** (Ф7.3); retrieval is keyword-based (deterministic); answers are
grounding-verified against marked chunks (Ф7.4); requirements are extracted (Ф7.5).
"""
from __future__ import annotations

import json
import pathlib
import re
import secrets
import time

from . import config as config_mod
from . import markers

_TOKEN = re.compile(r"[a-z0-9]{4,}")
_SENT = re.compile(r"[^.!?\n]+")
_REQ_BULLET = re.compile(r"^[-*]\s+(?:([A-Za-z]+-\d+)\s*[:.\-]\s*)?(.+)$")
_REQ_ROW = re.compile(r"^([A-Za-z]+-\d+)\s*[,;]\s*(.+)$")


def _safe(value) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "default")).strip("-") or "default"


def _user_dir(user: str) -> pathlib.Path:
    directory = config_mod.DATA_DIR / "users" / _safe(user) / "docs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(str(text).lower()))


def _chunk_md(content: str) -> list[str]:
    parts = re.split(r"\n\s*\n", content or "")
    return [p.strip() for p in parts if p.strip()]


def _chunk_csv(content: str) -> list[str]:
    lines = [l for l in (content or "").splitlines() if l.strip()]
    if not lines:
        return []
    header, body = lines[0], lines[1:]
    if not body:
        return [header]
    chunks = []
    for i in range(0, len(body), 5):
        chunks.append("\n".join([header] + body[i:i + 5]))
    return chunks


def ingest(cfg: dict, user: str, name: str, kind: str, content: str) -> dict:
    doc_id = secrets.token_hex(8)
    kind = "csv" if str(kind).lower() == "csv" else "md"
    texts = _chunk_csv(content) if kind == "csv" else _chunk_md(content)
    chunks = []
    for index, text in enumerate(texts):
        marker = markers.sign(cfg, {"doc": doc_id, "chunk": index, "version": 1, "user": user, "span": [0, len(text)]}, text=text)
        chunks.append({"index": index, "text": text, "marker": marker})
    record = {
        "id": doc_id, "user": user, "name": name, "kind": kind, "version": 1,
        "created": time.time(), "content": content, "chunks": chunks,
    }
    (_user_dir(user) / f"{doc_id}.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return record


def list_docs(user: str) -> list[dict]:
    out = []
    for path in sorted(_user_dir(user).glob("*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"id": rec.get("id"), "name": rec.get("name"), "kind": rec.get("kind"),
                    "created": rec.get("created"), "chunks": len(rec.get("chunks") or [])})
    out.sort(key=lambda d: d.get("created") or 0, reverse=True)
    return out


def load_doc(user: str, doc_id: str) -> dict | None:
    if not re.match(r"^[0-9a-f]{6,32}$", str(doc_id)):
        return None
    path = _user_dir(user) / f"{doc_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def search(user: str, query: str, top: int = 5) -> list[dict]:
    q = _tokens(query)
    scored = []
    for meta in list_docs(user):
        rec = load_doc(user, meta["id"])
        if not rec:
            continue
        for chunk in rec.get("chunks") or []:
            score = len(q & _tokens(chunk.get("text", "")))
            if score:
                scored.append({"doc": rec["id"], "name": rec["name"], "chunk": chunk["index"],
                               "text": chunk["text"], "marker": chunk["marker"], "score": score})
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:max(1, int(top))]


def _norm(text) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _fidelity(claim_tokens: set, chunk_tokens: set, claim_norm: str, chunk_norm: str) -> str:
    if claim_norm and claim_norm in chunk_norm:
        return "verbatim"
    if not claim_tokens:
        return "empty"
    ratio = len(claim_tokens & chunk_tokens) / len(claim_tokens)
    if ratio >= 0.6:
        return "paraphrase"
    if len(claim_tokens & chunk_tokens) >= 2:
        return "derived"
    return "unsupported"


def verify_grounding(answer: str, chunks: list[dict]) -> dict:
    """Claim-level attribution with fidelity against marked chunks (Ф7.4/Ф7.6)."""
    claims: list[dict] = []
    for sentence in _SENT.findall(answer or ""):
        text = sentence.strip()
        if not text:
            continue
        claim_tokens = _tokens(text)
        best = None
        for chunk in (chunks or []):
            shared = claim_tokens & _tokens(chunk.get("text", ""))
            if best is None or len(shared) > best["score"]:
                best = {"score": len(shared), "chunk": chunk, "shared": shared}
        if not best or best["score"] == 0:
            claims.append({"text": text, "fidelity": "unsupported", "source": None,
                           "added": sorted(claim_tokens), "omitted": []})
            continue
        chunk = best["chunk"]
        chunk_tokens = _tokens(chunk.get("text", ""))
        claims.append({
            "text": text,
            "fidelity": _fidelity(claim_tokens, chunk_tokens, _norm(text), _norm(chunk.get("text", ""))),
            "source": {"doc": chunk.get("doc"), "chunk": chunk.get("chunk"), "marker": chunk.get("marker")},
            "added": sorted(claim_tokens - chunk_tokens),
            "omitted": sorted(chunk_tokens - claim_tokens),
        })
    supported = [c["text"] for c in claims if c["fidelity"] != "unsupported"]
    unsupported = [c["text"] for c in claims if c["fidelity"] == "unsupported"]
    return {"claims": claims, "supported": supported, "unsupported": unsupported, "grounded": not unsupported}


def extract_requirements(cfg: dict, user: str, doc_id: str) -> dict | None:
    record = load_doc(user, doc_id)
    if record is None:
        return None
    out = []
    counter = 0
    for line in (record.get("content") or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _REQ_ROW.match(stripped) if record.get("kind") == "csv" else _REQ_BULLET.match(stripped)
        if not match:
            continue
        counter += 1
        rid = match.group(1) or f"REQ-{counter}"
        out.append({
            "id": f"req://{doc_id}/{rid}", "type": "Requirement", "name": rid,
            "description": match.group(2).strip(), "evidence": [record.get("name")],
        })
    return {"requirements": out}
