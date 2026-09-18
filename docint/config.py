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

OCR_RENDER_DPI = 300

# --------------------------------------------------------------------------- #
# Extraction schemas - plain data, so adding a document type is a config change
# --------------------------------------------------------------------------- #

FieldKind = Literal["identifier", "string", "date", "currency", "number"]


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: FieldKind
    description: str


FIELD_SPECS: dict[str, tuple[FieldSpec, ...]] = {
    "invoice": (
        FieldSpec("invoice_number", "identifier", "the invoice's own number, e.g. INV-2026-0117"),
        FieldSpec("vendor_name",    "string",     "the company that issued the invoice"),
        FieldSpec("invoice_date",   "date",       "date of issue, ISO YYYY-MM-DD"),
        FieldSpec("po_reference",   "identifier", "purchase order this invoice bills against"),
        FieldSpec("quantity",       "number",     "units billed on the line item"),
        FieldSpec("unit_price",     "currency",   "price per unit actually billed"),
        FieldSpec("total_amount",   "currency",   "total amount due"),
    ),
    "purchase_order": (
        FieldSpec("po_number",        "identifier", "the PO's own number, e.g. PO-2026-0043"),
        FieldSpec("vendor_name",      "string",     "the supplier the PO is issued to"),
        FieldSpec("issue_date",       "date",       "date the PO was issued, ISO YYYY-MM-DD"),
        FieldSpec("buyer_entity",     "string",     "the organisation issuing the PO"),
        FieldSpec("committed_amount", "currency",   "total amount committed / not-to-exceed"),
    ),
    "vendor_record": (
        FieldSpec("vendor_id",               "identifier", "internal vendor ID, e.g. V-1042"),
        FieldSpec("vendor_name",             "string",     "the vendor's company name"),
        FieldSpec("contracted_unit_price",   "currency",   "agreed contract price per unit"),
        FieldSpec("contract_effective_date", "date",       "contract start date, ISO YYYY-MM-DD"),
        FieldSpec("payment_terms",           "string",     "e.g. Net 30"),
    ),
}

DOCUMENT_TYPES = tuple(FIELD_SPECS) + ("unknown",)

# Lexical priors: a cheap deterministic second opinion on classification, and the
# same map drives optional document-type scoping at query time.
TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "invoice":        ("invoice", "bill to", "amount due", "remit", "inv-"),
    "purchase_order": ("purchase order", "po number", "not-to-exceed", "committed", "po-"),
    "vendor_record":  ("vendor", "contracted", "master record", "payment terms", "sku"),
}

# --------------------------------------------------------------------------- #
# Access control
# --------------------------------------------------------------------------- #
# One tag per document (attribute on the resource); a principal holds the set they
# are cleared for; the decision is `doc.access_tag in principal.access_tags`.
# RBAC at the profile layer, single-attribute ABAC at the resource layer. There is
# no identity provider - `--as <profile>` stands in for what would be a JWT claim.

DEFAULT_ACCESS_TAG = "procurement"
ACCESS_TAG_BY_FILENAME: dict[str, str] = {
    # Tagged `general` on purpose: it makes the access-denied eval case a real test.
    # If every document were `procurement`, the auditor would retrieve nothing at all
    # and the case would pass whether or not filtering actually worked. This way the
    # auditor CAN see the contracted rate but still cannot reach the invoice, so the
    # system must refuse on partial evidence rather than on an empty result set.
    "vendor_records.xlsx": "general",
}

ACCESS_PROFILES: dict[str, frozenset[str]] = {
    "procurement_analyst": frozenset({"procurement", "general"}),
    "external_auditor":    frozenset({"general"}),
}


def access_tag_for(filename: str) -> str:
    return ACCESS_TAG_BY_FILENAME.get(filename, DEFAULT_ACCESS_TAG)
