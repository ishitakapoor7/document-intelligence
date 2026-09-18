"""Score classification, OCR routing, extraction and grounding across both sets.

Deterministic: every judgement below is a comparison against hand-authored ground
truth. No model grades anything here.
"""
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


def evaluate(gold_path: Path, doc_dir: Path, label: str) -> tuple[Tally, list[str]]:
    gold = yaml.safe_load(gold_path.read_text())["documents"]
    tally, notes = Tally(), []

    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")

    for name, expected in gold.items():
        path = next(doc_dir.rglob(name), None)
        if path is None:
            notes.append(f"{name}: FILE MISSING")
            continue

        outcome = ingest_document(path, {})
        doc = outcome.document
        got = {f.name: f for f in (doc.fields if doc else [])}

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
        if expected.get("multi_record_schema_mismatch"):
            tally.add(name, "__multi_record__", "schema_mismatch",
                      "sheet holds 3 suppliers; schema describes 1; 2 silently discarded")
            print("  ~   MULTI-RECORD SCHEMA MISMATCH: 3 suppliers present, 1 representable")

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
    """The recognition ladder, end to end, with and without the vision fallback.

    The paired rows are the point: the same bytes, the same thresholds, and the only
    difference is whether the escalation was allowed to fire.
    """
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
            cost = d.extraction_cost_usd + d.ocr.fallback_cost_usd
            name = path.name if not allow else ""
            print(f"{name:<32}{'yes' if allow else 'no':<5}{d.document_type:<16}{tess:>6}{post:>7}"
                  f"{d.status:>30}{missing:>18}{cost:>9.4f}{d.total_latency_s:>6.1f}")
        print()
    print("  `post` is the vision model's SELF-REPORTED legibility - model-graded, and NOT")
    print("  comparable to the Tesseract column, which is a measured classifier score.")


if __name__ == "__main__":
    t1, _ = evaluate(GOLD_CONTROLLED, ROOT / "corpus", "CONTROLLED FIXTURES (written alongside the schemas)")
    t2, _ = evaluate(GOLD_GENERAL, ROOT / "eval" / "generalization", "SYNTHETIC ADVERSARIAL SET (written independently of the schemas, same author)")
    summarise(t1, "CONTROLLED")
    summarise(t2, "SYNTHETIC ADVERSARIAL")
    degraded_table([
        ROOT / "eval" / "scans" / "po_acme_001_L1_clean.pdf",
        ROOT / "eval" / "scans" / "po_acme_001_L2_medium.pdf",
        ROOT / "eval" / "scans" / "po_acme_001_L3_degraded.pdf",
        ROOT / "eval" / "generalization" / "G2_invoice_stamped_scan.pdf",
        ROOT / "eval" / "generalization" / "G4_po_amended_scan.pdf",
        ROOT / "eval" / "generalization" / "G6_receipt_thermal_scan.pdf",
    ])
