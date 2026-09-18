# OCR ladder - measured, not assumed

Tesseract 5.5.3 / leptonica 1.87.0, on `runs/_build/po_source.pdf` rendered and degraded
by `scripts/build_corpus.py` (seed 20260918). Regenerate with `python scripts/build_corpus.py`.

| Level | DPI | blur | noise | JPEG | rotate | mean conf | words | ground-truth fields readable |
|-------|-----|------|-------|------|--------|-----------|-------|------------------------------|
| L1 clean    | 300 | 0.0 | 0 | 95 | 0.0 | **95.1** | 63 | all 5 |
| L2 medium   | 120 | 1.0 | 5 | 30 | 0.8 | **76.9** | 58 | 4 of 5 - `vendor_name` garbled |
| L3 degraded | 110 | 1.2 | 8 | 25 | 1.0 | **50.4** | 46 | 3 of 5 - heavy corruption |

**MIN_OCR_CONFIDENCE = 65.0**, chosen to sit between the L2 and L3 observations. The demo copy
(`corpus/po_acme_001.pdf`, identical bytes to L1) sits at 95.1, far clear of it.

An earlier parameter set produced a ladder of 95.1 -> 44.4 -> **0.0**: at the bottom rung
Tesseract returned no words at all. That is a cliff, not a gradient, and it makes the most
dangerous failure mode untestable - OCR returning *plausible but wrong* text, which a
generic RAG pipeline embeds and answers from without complaint. The tuned L2 rung is
exactly that case: the PO number, issue date and committed amount all read correctly while
the vendor name is silently corrupted.
