"""Grounded answers over personal documents (Ф7.6).

The architect asks a question; the answer is generated **from the retrieved
marked chunks**, not from the model's own knowledge. Every sentence is then
attributed back to a chunk, so anything the model added on top of the documents
is marked `unsupported` and shown as unconfirmed instead of silently trusted.

This is the last step of the RAG track: Ф7.1 stores documents, Ф7.2 finds the
relevant chunks, Ф7.3/Ф7.4 verify the markers and the claims — Ф7.6 answers.
"""
from __future__ import annotations

from . import docs as docs_mod

PROMPT = """You answer questions about a system STRICTLY from the documents below.

Rules:
1. Use ONLY the numbered chunks. Do not add anything they do not say.
2. Cite the chunk you used for every sentence, as `[n]`.
3. If the documents do not answer the question, say so plainly.
4. Be concise: 1-4 sentences.

## Question
{query}

## Document chunks
{chunks}
"""


def build_prompt(query: str, chunks: list[dict]) -> str:
    body = "\n\n".join(
        f"[{i}] {chunk.get('doc') or 'document'} (chunk {chunk.get('chunk')}):\n{chunk.get('text') or ''}"
        for i, chunk in enumerate(chunks or [], start=1))
    return PROMPT.format(query=str(query or "").strip(), chunks=body)


def answer(cfg: dict, user: str, query: str, top: int = 5) -> dict:
    """Retrieve, generate and verify. Raises ValueError when nothing matches."""
    question = str(query or "").strip()
    if not question:
        raise ValueError("a question is required")
    chunks = docs_mod.search(user, question, int(top or 5))
    if not chunks:
        raise ValueError("no documents match the question — upload the requirements first")

    from agent.core import call_llm  # stdlib-only client, shared with the runner

    llm = dict((cfg or {}).get("llm") or {})
    message = call_llm(llm, [{"role": "user", "content": build_prompt(question, chunks)}])
    text = str(message.get("content") or "").strip()
    grounding = docs_mod.verify_grounding(text, chunks)
    return {
        "query": question,
        "answer": text,
        "chunks": chunks,
        "grounding": grounding,
        "model": llm.get("model"),
        "reasoning": message.get("reasoning"),
    }
