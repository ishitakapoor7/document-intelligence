"""The structured record: what extraction produces, persisted so something can use it.

One JSON file per source document, mirroring the corpus layout and versioned by the
document's content hash. Every scalar field carries the chunk it was read from, so a
record is auditable exactly as an answer is: `docint show <chunk_id>` resolves any
value back to the page or cell it came from.

A record is written whatever the outcome, including for documents routed to review. A
record saying a required field could not be read is the useful artifact there; omitting
it would leave a downstream consumer unable to distinguish "not extracted yet" from
"extracted, and the value is not in the document".
"""
from __future__ import annotations

from pathlib import Path

from docint.config import RUNS_DIR
from docint.models import Document

RECORDS_DIR = RUNS_DIR / "records"


def record_path(source_id: str) -> Path:
    # lstrip: source_id is absolute for a file outside ROOT, and `dir / "/abs"`
    # discards dir entirely.
    return RECORDS_DIR / (source_id.lstrip("/") + ".json")


def save(document: Document) -> Path:
    path = record_path(document.source_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document.model_dump_json(indent=2))
    return path


def load(source_id: str) -> Document | None:
    path = record_path(source_id)
    return Document.model_validate_json(path.read_text()) if path.exists() else None


def load_by_document_id() -> dict[str, Document]:
    """Every persisted record, keyed by document_id - the id chunk metadata carries.

    A record whose schema has drifted is skipped rather than raising: a stale file on
    disk must not be able to take down the query path.
    """
    if not RECORDS_DIR.exists():
        return {}
    records: dict[str, Document] = {}
    for path in RECORDS_DIR.rglob("*.json"):
        try:
            document = Document.model_validate_json(path.read_text())
        except ValueError:
            continue
        records[document.document_id] = document
    return records


def render(document: Document) -> str:
    """The record as the answer model sees it: typed values, each with its chunk_id."""
    head = (f"[record: {document.filename}] type={document.document_type} "
            f"status={document.status}")
    lines = [head]
    for field in document.fields:
        flag = "  LOW-CONFIDENCE" if field.low_confidence else ""
        lines.append(f"  {field.name} = {field.value}  (read from {field.chunk_id}){flag}")
    for item in document.line_items:
        lines.append(f"  line_item: {item.description} qty={item.quantity} "
                     f"unit_price={item.unit_price} amount={item.amount}"
                     + (f"  (read from {item.chunk_id})" if item.chunk_id else ""))
    if document.missing_required_fields:
        lines.append(f"  NOT PRESENT IN THIS DOCUMENT: "
                     f"{', '.join(document.missing_required_fields)}")
    return "\n".join(lines)
