"""Indexing and access-filtered retrieval.

LlamaIndex owns this layer and nothing else in the codebase touches it. It never
calls an LLM (`Settings.llm = None`) - retrieval here is embeddings and metadata
filters, so the only non-determinism in the query path is the answer generation
itself.

The access filter is the security control, and it is applied INSIDE the store: the
`where` clause reaches Chroma, so chunks the requester may not see are never returned
to this process at all. Filtering after retrieval would mean unauthorised text had
already been loaded into memory, one forgotten line away from a prompt.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import chromadb
from fastembed import TextEmbedding
from llama_index.core import Settings, StorageContext, VectorStoreIndex
from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.llms import MockLLM
from llama_index.core.schema import NodeRelationship, RelatedNodeInfo, TextNode
from llama_index.core.vector_stores import (
    FilterCondition,
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)
from llama_index.vector_stores.chroma import ChromaVectorStore

from docint.config import CHROMA_DIR, TYPE_KEYWORDS
from docint.models import Chunk, Document, SourceLocation


class FastEmbedAdapter(BaseEmbedding):
    """Minimal BaseEmbedding over fastembed.

    Exists because llama-index-embeddings-fastembed pins Python <3.13. Fifteen lines
    here beat installing a second interpreter, and it keeps torch out of the tree -
    fastembed runs on onnxruntime, so a fresh clone installs in seconds.
    """

    _model: Any = None

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", **kwargs: Any) -> None:
        super().__init__(model_name=model_name, **kwargs)
        self._model = TextEmbedding(model_name=model_name)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self._model.embed(texts)]

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._embed([text])[0]

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._embed([query])[0]

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._get_query_embedding(query)


_configured = False


def _configure() -> None:
    """Set the embedding model without ever READING Settings.embed_model.

    Reading that property triggers LlamaIndex's lazy default, which resolves to
    OpenAI and raises ImportError before our assignment is ever reached. The flag
    avoids touching the getter at all.
    """
    global _configured
    if _configured:
        return
    Settings.embed_model = FastEmbedAdapter()
    # LlamaIndex must never make a model call. `Settings.llm = None` achieves that,
    # but its resolver then prints "LLM is explicitly disabled. Using MockLLM." to
    # stdout on every retrieval - above the answer, reading like a warning. Handing it
    # the MockLLM directly is the same guarantee, stated rather than inferred.
    Settings.llm = MockLLM()
    _configured = True


def to_text_node(chunk: Chunk) -> TextNode:
    """Chunk -> node. Metadata is flat because Chroma only stores scalars.

    `access_tag` is excluded from both the embedded text and the LLM-visible text: it
    must be filterable without ever being readable by the model, which could otherwise
    learn that other tags exist.
    """
    metadata = {
        "document_id": chunk.document_id,
        "document_type": chunk.document_type,
        "file_type": chunk.file_type,
        "filename": chunk.filename,
        "access_tag": chunk.access_tag,
        "location": chunk.source_location.render(),
        "location_kind": chunk.source_location.kind,
        "page": chunk.source_location.page if chunk.source_location.page is not None else -1,
        "sheet": chunk.source_location.sheet or "",
        "cell_range": chunk.source_location.cell_range or "",
        "ocr_confidence": chunk.ocr_confidence if chunk.ocr_confidence is not None else -1.0,
    }
    excluded = ["access_tag", "document_id", "location_kind", "page", "sheet",
                "cell_range", "ocr_confidence", "file_type"]
    return TextNode(
        id_=chunk.chunk_id,
        text=chunk.text,
        metadata=metadata,
        excluded_embed_metadata_keys=excluded,
        excluded_llm_metadata_keys=excluded,
        relationships={NodeRelationship.SOURCE: RelatedNodeInfo(node_id=chunk.document_id)},
    )


def open_index(persist_dir: Path | None = None) -> VectorStoreIndex:
    _configure()
    directory = persist_dir or CHROMA_DIR
    directory.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(directory))
    store = ChromaVectorStore(chroma_collection=client.get_or_create_collection("docint"))
    return VectorStoreIndex.from_vector_store(
        store, storage_context=StorageContext.from_defaults(vector_store=store))


def upsert(index: VectorStoreIndex, chunks: list[Chunk]) -> int:
    """Insert or replace by chunk_id, so re-ingesting the same bytes is a no-op."""
    if not chunks:
        return 0
    nodes = [to_text_node(c) for c in chunks]
    index.vector_store.client.delete(ids=[n.id_ for n in nodes])
    index.insert_nodes(nodes)
    return len(nodes)


def scope_document_types(question: str) -> list[str]:
    """Deterministic lexical routing, reusing the classification keyword map.

    Returns every type the question plausibly targets, or [] for no scoping. No LLM,
    so retrieval stays deterministic.

    Matching is word-boundary, not substring. With plain substring matching "does the
    PO cover the invoice total" scoped to `invoice` ALONE - the purchase-order keywords
    are "po number" and "po-", neither of which appears - and the filter would then have
    excluded the very document needed to answer. On a cross-document question an
    over-narrow scope is far more damaging than no scope at all, so this errs towards
    including a type rather than excluding one.
    """
    lowered = question.lower()
    matched = []
    for doc_type, keywords in TYPE_KEYWORDS.items():
        if any(re.search(rf"(?<![a-z0-9]){re.escape(k)}", lowered) for k in keywords):
            matched.append(doc_type)
    return sorted(matched)


def build_filters(access_tags: frozenset[str], document_types: list[str] | None) -> MetadataFilters:
    """ACL filter, optionally ANDed with a query-derived type filter.

    The access term is always present and always the outer AND. A query-derived filter
    can only ever NARROW the candidate set - there is no code path by which it replaces,
    relaxes or ORs with the access term.
    """
    access = MetadataFilters(
        condition=FilterCondition.OR,
        filters=[MetadataFilter(key="access_tag", value=t, operator=FilterOperator.EQ)
                 for t in sorted(access_tags)],
    )
    if not document_types:
        return access
    types = MetadataFilters(
        condition=FilterCondition.OR,
        filters=[MetadataFilter(key="document_type", value=t, operator=FilterOperator.EQ)
                 for t in document_types],
    )
    return MetadataFilters(condition=FilterCondition.AND, filters=[access, types])


def get_chunks(chunk_ids: list[str], index: VectorStoreIndex | None = None) -> dict[str, dict]:
    """Resolve chunk IDs back to their text and metadata, straight out of the store.

    This is what makes a citation checkable by hand: the ID printed under an answer
    is the primary key of the thing it came from, so `show` is a lookup rather than
    a search. No access filter is applied here - the caller supplies one, because the
    eval harness legitimately reads chunks no single principal can see.
    """
    if not chunk_ids:
        return {}
    index = index or open_index()
    got = index.vector_store.client.get(ids=chunk_ids, include=["documents", "metadatas"])
    return {cid: {"text": text, **meta}
            for cid, text, meta in zip(got["ids"], got["documents"], got["metadatas"])}
