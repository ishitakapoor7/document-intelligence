"""Thresholds, schemas, access rules and model IDs. All tuning lives here."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# Explicit path: find_dotenv() walks the caller's stack frame, which is absent when
# the entry point has none (e.g. `python - <<EOF`).
load_dotenv(dotenv_path=ROOT / ".env")

CORPUS_DIR = ROOT / "corpus"
RUNS_DIR = ROOT / "runs"
MANIFEST_PATH = RUNS_DIR / "manifest.json"
CHROMA_DIR = RUNS_DIR / "chroma"

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
MODEL_CLASSIFY = "claude-haiku-4-5"
MODEL_EXTRACT = "claude-opus-5"
MODEL_VERIFY = "claude-haiku-4-5"
MODEL_ANSWER = "claude-opus-5"
MODEL_VISION = "claude-opus-5"

# --------------------------------------------------------------------------- #
# Thresholds
# --------------------------------------------------------------------------- #
# OCR thresholds are measured, not guessed: the same purchase order at three scan
# qualities reads 95.1 / 62.4 / 43.4. See eval/ocr_ladder.md.

MIN_OCR_CONFIDENCE = 55.0      # below: human review, extraction skipped
LOW_CONFIDENCE_FIELD = 75.0    # below: extract, but flag every field
MIN_CLASSIFY_CONFIDENCE = 0.60  # below: `unknown` rather than a forced label

# Below this Tesseract confidence, escalate the page to vision. Set at the field-flag
# threshold: if values would be surfaced as untrusted anyway, a better read is worth
# one call.
MIN_OCR_FOR_VISION_FALLBACK = 75.0

# A PDF page yielding fewer characters than this is treated as a scan.
MIN_TEXT_LAYER_CHARS = 100

# A text layer is not evidence that a page was born digital - archived documents are
# usually scans somebody else already OCR'd, and that inherited text carries no
# confidence and no provenance. A page mostly covered by a raster image is a scan
# whatever text rides along with it, which is structural rather than a guess.
RASTER_PAGE_COVERAGE = 0.80

OCR_RENDER_DPI = 300

# top_k is generous because the corpus is small; at three documents it returns most
# of it, so retrieval quality is not yet a variable.
RETRIEVAL_TOP_K = 8
MIN_RETRIEVAL_SCORE = 0.25

# USD per million tokens.
PRICING = {
    "claude-opus-5":  {"input": 5.00, "output": 25.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}


def usd_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    p = PRICING.get(model)
    if not p:
        return 0.0
    return (input_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000


# --------------------------------------------------------------------------- #
# Extraction schemas - plain data, so a new document type is a config change
# --------------------------------------------------------------------------- #

FieldKind = Literal["identifier", "string", "date", "currency", "number", "line_items"]


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: FieldKind
    description: str
    required: bool = False   # absent required field -> human review, type preserved


# Scalars describe the document; repeating rows live in line_items[]. Holding
# quantity or unit_price as a scalar silently returns row 1 of N on a multi-line
# invoice, with nothing marking it partial.
FIELD_SPECS: dict[str, tuple[FieldSpec, ...]] = {
    "invoice": (
        FieldSpec("invoice_number", "identifier", "the invoice's own number, e.g. INV-2026-0117", required=True),
        FieldSpec("vendor_name",    "string",     "the company that issued the invoice", required=True),
        FieldSpec("invoice_date",   "date",       "date of issue, ISO YYYY-MM-DD", required=True),
        # optional: many legitimate invoices carry no PO reference
        FieldSpec("po_reference",   "identifier", "purchase order this invoice bills against, if any"),
        FieldSpec("total_amount",   "currency",   "total amount due", required=True),
        FieldSpec("line_items",     "line_items", "every billed line on the invoice"),
    ),
    "purchase_order": (
        FieldSpec("po_number",        "identifier", "the PO's own number, e.g. PO-2026-0043", required=True),
        FieldSpec("vendor_name",      "string",     "the supplier the PO is issued to", required=True),
        FieldSpec("issue_date",       "date",       "date the PO was issued, ISO YYYY-MM-DD", required=True),
        FieldSpec("buyer_entity",     "string",     "the organisation issuing the PO"),
        FieldSpec("committed_amount", "currency",   "total committed / not-to-exceed / maximum commitment", required=True),
        FieldSpec("line_items",       "line_items", "every ordered line on the purchase order"),
    ),
    "vendor_record": (
        FieldSpec("vendor_id",               "identifier", "internal vendor ID, e.g. V-1042", required=True),
        FieldSpec("vendor_name",             "string",     "the vendor's company name", required=True),
        FieldSpec("contracted_unit_price",   "currency",   "agreed contract price per unit", required=True),
        FieldSpec("contract_effective_date", "date",       "contract start date, ISO YYYY-MM-DD"),
        FieldSpec("payment_terms",           "string",     "e.g. Net 30"),
    ),
}


def required_fields(document_type: str) -> tuple[str, ...]:
    return tuple(f.name for f in FIELD_SPECS.get(document_type, ()) if f.required)


def scalar_fields(document_type: str) -> tuple[FieldSpec, ...]:
    return tuple(f for f in FIELD_SPECS.get(document_type, ()) if f.kind != "line_items")


DOCUMENT_TYPES = tuple(FIELD_SPECS) + ("unknown",)

# A cheap deterministic second opinion on classification; the same map drives
# document-type scoping at query time.
TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "invoice":        ("invoice", "bill to", "amount due", "remit", "inv-", "invoiced"),
    "purchase_order": ("purchase order", "po ", "po-", "po number", "not-to-exceed",
                       "committed", "commitment", "blanket"),
    "vendor_record":  ("vendor", "supplier", "contracted", "contract rate", "master record",
                       "payment terms", "sku", "agreed rate"),
}

# --------------------------------------------------------------------------- #
# Access control
# --------------------------------------------------------------------------- #
# One tag per document; a principal holds the set they are cleared for; the decision
# is `doc.access_tag in principal.access_tags`. RBAC at the profile layer,
# single-attribute ABAC at the resource layer. No identity provider - `--as
# <profile>` stands in for a JWT claim.
#
# Tags come from the directory a document sits in, mirroring folder-level ACLs:
#     corpus/procurement/invoice_acme_001.pdf -> procurement
# In production this would come from the DMS or a sensitivity label. Either way
# adding a document needs no code change.
DEFAULT_ACCESS_TAG = "procurement"

ACCESS_PROFILES: dict[str, frozenset[str]] = {
    "procurement_analyst": frozenset({"procurement", "general"}),
    "external_auditor":    frozenset({"general"}),
}


def known_access_tags() -> frozenset[str]:
    return frozenset().union(*ACCESS_PROFILES.values())


def access_tag_for(path) -> str:
    """Derive a document's access tag from the directory it lives in."""
    parent = Path(path).parent.name
    return parent if parent in known_access_tags() else DEFAULT_ACCESS_TAG
