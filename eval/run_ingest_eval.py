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

        # cross-row consistency, where the gold file asks for it
        if expected.get("consistency_requirement") and got:
            chunks = {f.chunk_id for f in got.values()}
            consistent = len(chunks) == 1
            tally.add(name, "__consistency__", "pass" if consistent else "fail",
                      f"fields drawn from {len(chunks)} chunk(s)")
            print(f"  {'OK ' if consistent else 'FAIL'} all fields from one chunk "
                  f"({len(chunks)} distinct chunk(s) cited)")

        # field-level grounding: every value must cite a chunk that exists
        ids = {c.chunk_id for c in outcome.chunks}
        ungrounded = [f.name for f in got.values() if f.chunk_id not in ids]
        if ungrounded:
            tally.add(name, "__grounding__", "fail", f"ungrounded: {ungrounded}")
            print(f"  FAIL grounding: {ungrounded} cite chunks not in this document")

    return tally, notes


def summarise(tally: Tally, label: str) -> None:
    fields = [r for r in tally.rows if not r[1].startswith("__")]
    types = [r for r in tally.rows if r[1] == "__type__"]
    scored = [r for r in fields if r[2] != "schema_mismatch"]

    print(f"\n{'-' * 78}\n{label} SUMMARY")
    t_pass = sum(1 for r in types if r[2] == "pass")
    print(f"  classification     {t_pass}/{len(types)}")
    if scored:
        f_pass = sum(1 for r in scored if r[2] == "pass")
        print(f"  field extraction   {f_pass}/{len(scored)}  (excludes schema mismatches)")
    hall = tally.count("hallucination")
    print(f"  hallucinations     {hall}   <-- values invented where the document has none")
    mism = tally.count("schema_mismatch")
    if mism:
        print(f"  schema mismatches  {mism}   (document well-formed, configured schema does not fit)")

    failures = [r for r in tally.rows if r[2] in ("fail", "miss", "hallucination")]
    if failures:
        print(f"\n  FAILURES ({len(failures)}):")
        for doc, field, verdict, detail in failures:
            print(f"    [{verdict:<13}] {doc} :: {field}  {detail}")


if __name__ == "__main__":
    t1, _ = evaluate(GOLD_CONTROLLED, ROOT / "corpus", "CONTROLLED FIXTURES (written alongside the schemas)")
    t2, _ = evaluate(GOLD_GENERAL, ROOT / "eval" / "generalization", "GENERALIZATION SET (written independently of the schemas)")
    summarise(t1, "CONTROLLED")
    summarise(t2, "GENERALIZATION")
