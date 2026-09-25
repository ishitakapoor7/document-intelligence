"""Score classification, OCR routing, extraction and grounding across all three
document sets. Every judgement is a comparison against hand-authored gold; no model
grades anything here."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docint.config import ROOT
from docint.ingest import ingest_document

GOLD_CONTROLLED = ROOT / "eval" / "gold.yaml"
GOLD_GENERAL = ROOT / "eval" / "generalization_gold.yaml"
GOLD_HOLDOUT = ROOT / "eval" / "holdout_gold.yaml"


def norm(v) -> str:
    return str(v).strip().lower().rstrip(".")


def matches(pred, expected) -> bool:
    if pred is None or expected is None:
        return pred is expected
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            return abs(float(pred) - float(expected)) <= max(0.01, 0.005 * abs(float(expected)))
        except (TypeError, ValueError):
            return False
    if isinstance(expected, str) and len(expected) == 10 and expected[4] == "-":
        try:
            return date.fromisoformat(str(pred)[:10]) == date.fromisoformat(expected)
        except ValueError:
            return False
    return norm(pred) == norm(expected)


class Tally:
    def __init__(self) -> None:
        self.rows: list[tuple] = []

    def add(self, doc: str, field: str, verdict: str, detail: str = "") -> None:
        self.rows.append((doc, field, verdict, detail))

    def count(self, verdict: str) -> int:
        return sum(1 for r in self.rows if r[2] == verdict)


# Printed at the end: a cost you only discover on the invoice is not actionable.
SPEND: list[float] = []


def evaluate(gold_path: Path, doc_dir: Path, label: str,
             only: str | None = None) -> tuple[Tally, list[str]]:
    gold = yaml.safe_load(gold_path.read_text())["documents"]
    tally, notes = Tally(), []

    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")

    for name, expected in gold.items():
        if only and only.lower() not in name.lower():
            continue
        path = next(doc_dir.rglob(name), None)
        if path is None:
            notes.append(f"{name}: FILE MISSING")
            continue

        outcome = ingest_document(path, {})
        if outcome.document is not None:
            SPEND.append(outcome.document.classification_cost_usd
                         + outcome.document.extraction_cost_usd
                         + outcome.document.ocr.fallback_cost_usd)
        doc = outcome.document
        got = {f.name: f for f in (doc.fields if doc else [])}

        # A packet the taxonomy cannot label has more than one defensible answer.
        # Where gold says so, any listed label passes and the document-level verdict
        # is carried by the schema-mismatch row.
        any_types = expected.get("expect_document_type_any_of")
        if any_types:
            exp_type = " | ".join(any_types)
            type_ok = doc is not None and doc.document_type in any_types
        else:
            exp_type = expected.get("expect_document_type") or expected.get("document_type")
            type_ok = doc is not None and doc.document_type == exp_type
        tally.add(name, "__type__", "pass" if type_ok else "fail",
                  f"got {doc.document_type if doc else 'n/a'}, expected {exp_type}")

        conf = f"{doc.ocr_mean_confidence:.1f}" if doc and doc.ocr_mean_confidence is not None else "digital"
        print(f"\n{name}")
        print(f"  type       {'OK ' if type_ok else 'FAIL'} got={doc.document_type if doc else '-':<16}"
              f" expected={exp_type:<16} conf={doc.classification_confidence:.2f}" if doc else "")
        print(f"  ocr        {conf:<8} file_type={doc.file_type if doc else '-':<12} status={outcome.status}")

        for field, spec in (expected.get("fields") or {}).items():
            f = got.get(field)
            pred = f.value if f else None

            if "schema_mismatch" in spec:
                tally.add(name, field, "schema_mismatch", spec["schema_mismatch"])
                print(f"    ~   {field:<24} {str(pred):<30} SCHEMA MISMATCH: {spec['schema_mismatch']}")
            elif "must_be_null" in spec:
                ok = pred is None
                tally.add(name, field, "pass" if ok else "hallucination",
                          "" if ok else f"invented {pred!r}")
                print(f"    {'OK ' if ok else 'HALLUCINATION'} {field:<24} "
                      f"{str(pred):<30} {'correctly absent' if ok else 'INVENTED A VALUE'}")
            elif "any_of" in spec:
                ok = any(matches(pred, o) for o in spec["any_of"])
                tally.add(name, field, "pass" if ok else "fail", f"got {pred!r}")
                print(f"    {'OK ' if ok else 'FAIL'} {field:<24} {str(pred):<30} "
                      f"(any of {spec['any_of']})")
            else:
                ok = matches(pred, spec["value"])
                tally.add(name, field, "pass" if ok else ("miss" if pred is None else "fail"),
                          f"got {pred!r}, expected {spec['value']!r}")
                mark = "OK " if ok else ("MISS" if pred is None else "FAIL")
                print(f"    {mark} {field:<24} {str(pred):<30} expected={spec['value']!r}")

        # line items - the repeating data the scalar schema cannot hold
        li_spec = expected.get("line_items")
        if li_spec:
            got_items = doc.line_items if doc else []
            count_ok = len(got_items) == li_spec["count"]
            tally.add(name, "__line_items__", "pass" if count_ok else "fail",
                      f"got {len(got_items)}, expected {li_spec['count']}")
            print(f"  {'OK ' if count_ok else 'FAIL'} line_items  {len(got_items)} extracted, "
                  f"{li_spec['count']} expected")
            for want in li_spec.get("items", []):
                needle = want["description_contains"].lower()
                hit = next((li for li in got_items if needle in (li.description or "").lower()), None)
                if hit is None:
                    tally.add(name, f"line:{needle}", "miss", "line not extracted")
                    print(f"      MISS  {needle}")
                    continue
                bad = [k for k in ("quantity", "unit_price", "amount")
                       if k in want and not matches(getattr(hit, k), want[k])]
                tally.add(name, f"line:{needle}", "pass" if not bad else "fail",
                          f"wrong: {bad}" if bad else "")
                print(f"      {'OK  ' if not bad else 'FAIL'} {needle:<14} "
                      f"qty={hit.quantity} unit={hit.unit_price} amt={hit.amount}"
                      + (f"   WRONG: {bad}" if bad else ""))

        # a schema that cannot represent the document is recorded as such, not as a pass
        mrm = expected.get("multi_record_schema_mismatch")
        if mrm:
            # Description comes from gold: hardcoding it here printed "3 suppliers"
            # under a 7-page accounts-payable packet.
            detail = mrm if isinstance(mrm, str) else "several records, one representable"
            tally.add(name, "__multi_record__", "schema_mismatch", detail.strip())
            print(f"  ~   MULTI-RECORD SCHEMA MISMATCH: {detail.strip()}")

        if expected.get("consistency_requirement") and got:
            chunks = {f.chunk_id for f in got.values()}
            consistent = len(chunks) == 1
            tally.add(name, "__consistency__", "pass" if consistent else "fail",
                      f"fields drawn from {len(chunks)} chunk(s)")
            print(f"  {'OK ' if consistent else 'FAIL'} internal consistency: all fields from "
                  f"{len(chunks)} chunk(s) (necessary, not sufficient)")

        # field-level grounding: every value must cite a chunk in this document
        ids = {c.chunk_id for c in outcome.chunks}
        ungrounded = [f.name for f in got.values() if f.chunk_id not in ids]
        if ungrounded:
            tally.add(name, "__grounding__", "fail", f"ungrounded: {ungrounded}")
            print(f"  FAIL grounding: {ungrounded} cite chunks not in this document")

    return tally, notes


def summarise(tally: Tally, label: str) -> None:
    types = [r for r in tally.rows if r[1] == "__type__"]
    scalars = [r for r in tally.rows if not r[1].startswith("__") and not r[1].startswith("line:")]
    lines = [r for r in tally.rows if r[1].startswith("line:") or r[1] == "__line_items__"]
    scored_scalars = [r for r in scalars if r[2] != "schema_mismatch"]

    print(f"\n{'-' * 78}\n{label} SUMMARY")
    t_pass = sum(1 for r in types if r[2] == "pass")
    print(f"  classification                {t_pass}/{len(types)}")
    if scored_scalars:
        f_pass = sum(1 for r in scored_scalars if r[2] == "pass")
        print(f"  schema-representable scalars  {f_pass}/{len(scored_scalars)}")
        print(f"     ^ this is NOT document accuracy. It counts only single-valued fields the")
        print(f"       configured schema can hold; repeating and multi-record data is below.")
    if lines:
        l_pass = sum(1 for r in lines if r[2] == "pass")
        print(f"  line items                    {l_pass}/{len(lines)}")

    docs = {r[0] for r in tally.rows}
    complete = 0
    for d in docs:
        rows = [r for r in tally.rows if r[0] == d]
        if all(r[2] == "pass" for r in rows):
            complete += 1
    print(f"  DOCUMENT COMPLETENESS         {complete}/{len(docs)}  "
          f"(documents with nothing wrong, missing or unrepresentable)")

    hall = tally.count("hallucination")
    print(f"  hallucinations                {hall}")
    mism = tally.count("schema_mismatch")
    if mism:
        print(f"  schema mismatches             {mism}  (document well-formed; schema cannot hold it)")

    failures = [r for r in tally.rows if r[2] in ("fail", "miss", "hallucination")]
    if failures:
        print(f"\n  FAILURES ({len(failures)}):")
        for doc, field, verdict, detail in failures:
            print(f"    [{verdict:<13}] {doc} :: {field}  {detail}")


def degraded_table(paths: list[Path]) -> None:
    """The recognition ladder with and without the vision fallback. Same bytes, same
    thresholds; the only variable is whether escalation was allowed to fire."""
    print(f"\n{'=' * 116}\nDEGRADED-CASE LADDER\n{'=' * 116}")
    hdr = (f"{'document':<32}{'fb?':<5}{'class':<16}{'tess':>6}{'post':>7}"
           f"{'decision':>30}{'missing':>18}{'cost':>9}{'lat':>6}")
    print(hdr); print("-" * len(hdr))
    for path in paths:
        for allow in (False, True):
            out = ingest_document(path, {}, vision_fallback=allow)
            d = out.document
            if d is None:
                continue
            tess = f"{d.ocr.tesseract_confidence:.1f}" if d.ocr.tesseract_confidence is not None else "-"
            post = f"{d.ocr.post_fallback_confidence:.1f}" if d.ocr.post_fallback_confidence is not None else "-"
            missing = ",".join(d.missing_required_fields) or "-"
            cost = d.classification_cost_usd + d.extraction_cost_usd + d.ocr.fallback_cost_usd
            name = path.name if not allow else ""
            print(f"{name:<32}{'yes' if allow else 'no':<5}{d.document_type:<16}{tess:>6}{post:>7}"
                  f"{d.status:>30}{missing:>18}{cost:>9.4f}{d.total_latency_s:>6.1f}")
        print()
    print("  `post` is the vision model's SELF-REPORTED legibility - model-graded, and NOT")
    print("  comparable to the Tesseract column, which is a measured classifier score.")


# Every run spends real money - each document goes through the live model path, which
# is the point. Sets are selectable and the ladder is opt-in.
SETS = {
    "controlled":  (GOLD_CONTROLLED, ROOT / "corpus",
                    "CONTROLLED FIXTURES (written alongside the schemas)", "CONTROLLED"),
    "synthetic":   (GOLD_GENERAL, ROOT / "eval" / "generalization",
                    "SYNTHETIC ADVERSARIAL SET (written independently of the schemas, "
                    "same author)", "SYNTHETIC ADVERSARIAL"),
    "holdouts":    (GOLD_HOLDOUT, ROOT / "eval" / "holdouts",
                    "EXTERNAL HOLDOUTS (real documents, different author, gold written "
                    "before the run)", "EXTERNAL HOLDOUT"),
}

LADDER = [
    ROOT / "eval" / "scans" / "po_acme_001_L1_clean.pdf",
    ROOT / "eval" / "scans" / "po_acme_001_L2_medium.pdf",
    ROOT / "eval" / "scans" / "po_acme_001_L3_degraded.pdf",
    ROOT / "eval" / "generalization" / "G2_invoice_stamped_scan.pdf",
    ROOT / "eval" / "generalization" / "G4_po_amended_scan.pdf",
    ROOT / "eval" / "generalization" / "G6_receipt_thermal_scan.pdf",
]


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(
        description="Ingestion eval. Every run makes live model calls and costs money.")
    ap.add_argument("--sets", nargs="+", choices=[*SETS, "all"], default=["all"],
                    help="which document sets to score (default: all)")
    ap.add_argument("--ladder", action="store_true",
                    help="also run the scan-degradation table: 6 documents twice each, "
                         "with and without the vision fallback (~$0.37)")
    ap.add_argument("--only", metavar="SUBSTRING",
                    help="score just the documents whose filename contains this")
    args = ap.parse_args()

    chosen = list(SETS) if "all" in args.sets else args.sets
    results = []
    for key in chosen:
        gold_path, doc_dir, heading, label = SETS[key]
        tally, _ = evaluate(gold_path, doc_dir, heading, only=args.only)
        results.append((tally, label))
    for tally, label in results:
        summarise(tally, label)

    if args.ladder:
        degraded_table(LADDER)

    if SPEND:
        print(f"\n  model spend for this run: ${sum(SPEND):.2f} "
              f"over {len(SPEND)} document(s)")


if __name__ == "__main__":
    main()
