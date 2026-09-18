"""Thresholds, schemas, access rules and model IDs. All tuning lives here."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# Explicit path: find_dotenv() walks the caller's stack frame and fails when the
# entry point has no frame (e.g. `python - <<EOF`).
load_dotenv(dotenv_path=ROOT / ".env")

CORPUS_DIR = ROOT / "corpus"
RUNS_DIR = ROOT / "runs"
MANIFEST_PATH = RUNS_DIR / "manifest.json"
CHROMA_DIR = RUNS_DIR / "chroma"

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
MODEL_CLASSIFY = "claude-haiku-4-5"   # 4-way choice over a short excerpt
MODEL_EXTRACT = "claude-opus-5"       # where the trust guarantees live
MODEL_VERIFY = "claude-haiku-4-5"     # isolated per-claim citation check
MODEL_ANSWER = "claude-opus-5"

# --------------------------------------------------------------------------- #
# Thresholds
# --------------------------------------------------------------------------- #

# Two OCR thresholds, both MEASURED through the real pipeline (eval/ocr_ladder.md).
# The same purchase order at three scan qualities gives 95.1 / 62.4 / 43.4.
#
# Below this, a document is routed to human review instead of extracted from: the
# text is too poor to assert values off. Sits between the L2 and L3 observations.
MIN_OCR_CONFIDENCE = 55.0

# Above the review threshold but below this, values ARE extracted but every field
# is surfaced as low-confidence rather than asserted flatly. Sits between L1 and L2.
LOW_CONFIDENCE_FIELD = 75.0

# Below this self-reported classification confidence, a document becomes `unknown`
# rather than being forced into a label it does not fit.
MIN_CLASSIFY_CONFIDENCE = 0.60

# A PDF page with fewer than this many extractable characters is treated as a
# scan and sent through OCR. The demo invoice page has 507; the scanned PO has 0.
MIN_TEXT_LAYER_CHARS = 100

# Retrieval. top_k is generous because the corpus is small - at three documents this
# returns most of it, which is why retrieval quality is not yet a variable here.
RETRIEVAL_TOP_K = 8
MIN_RETRIEVAL_SCORE = 0.25

OCR_RENDER_DPI = 300

# Below this Tesseract confidence the page is escalated to Claude vision for a second
# transcription. Set at the field-flag threshold: if values would be surfaced as
# untrusted anyway, a better read is worth one call.
MIN_OCR_FOR_VISION_FALLBACK = 75.0
MODEL_VISION = "claude-opus-5"

# USD per million tokens, for the cost column in the degraded-case table.
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
# Extraction schemas - plain data, so adding a document type is a config change
# --------------------------------------------------------------------------- #

FieldKind = Literal["identifier", "string", "date", "currency", "number", "line_items"]


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: FieldKind
    description: str
    required: bool = False   # absent required field -> human review, type preserved


# `quantity` and `unit_price` are deliberately NOT scalar invoice fields. They were,
# and on a six-line invoice the pipeline silently returned line 1 of 6 with nothing
# marking it partial - a schema limitation invisible on single-line fixtures. Repeating
# data now lives in line_items[]; scalars describe the document, not its rows.
FIELD_SPECS: dict[str, tuple[FieldSpec, ...]] = {
    "invoice": (
        FieldSpec("invoice_number", "identifier", "the invoice's own number, e.g. INV-2026-0117", required=True),
        FieldSpec("vendor_name",    "string",     "the company that issued the invoice", required=True),
        FieldSpec("invoice_date",   "date",       "date of issue, ISO YYYY-MM-DD", required=True),
        # optional: many legitimate invoices carry no PO reference at all
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

# Lexical priors: a cheap deterministic second opinion on classification, and the
# same map drives optional document-type scoping at query time.
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
# One tag per document (attribute on the resource); a principal holds the set they
# are cleared for; the decision is `doc.access_tag in principal.access_tags`.
# RBAC at the profile layer, single-attribute ABAC at the resource layer. There is
# no identity provider - `--as <profile>` stands in for what would be a JWT claim.

# Tags are derived from the DIRECTORY a document sits in, mirroring how folder-level
# ACLs actually work in a document management system:
#
#     corpus/procurement/invoice_acme_001.pdf   -> procurement
#     corpus/general/vendor_records.xlsx        -> general
#
# An earlier version mapped literal filenames to tags. That was demo scaffolding
# wearing a config's clothes: it could not survive a renamed file, let alone a real
# corpus. Directory-derived tags are still a stand-in - in production this comes from
# the DMS, a folder ACL or a sensitivity label - but it is a stand-in for the right
# thing, and adding a document requires no code change.
DEFAULT_ACCESS_TAG = "procurement"

ACCESS_PROFILES: dict[str, frozenset[str]] = {
    "procurement_analyst": frozenset({"procurement", "general"}),
    "external_auditor":    frozenset({"general"}),
}


def known_access_tags() -> frozenset[str]:
    return frozenset().union(*ACCESS_PROFILES.values())


def access_tag_for(path) -> str:
    """Derive a document's access tag from the directory it lives in."""
    from pathlib import Path
    parent = Path(path).parent.name
    return parent if parent in known_access_tags() else DEFAULT_ACCESS_TAG
