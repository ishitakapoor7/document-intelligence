PY := .venv/bin/python

.PHONY: help doctor ingest ask review demo eval eval-ingest eval-ingest-save probe test clean-index

help:
	@echo "  make doctor       check the environment before anything else"
	@echo "  make ingest       parse, classify and extract corpus/"
	@echo "  make ask Q=\"...\"  answer a question  [PROFILE=procurement_analyst|external_auditor]"
	@echo "  make review       list documents waiting on a human"
	@echo "  make demo         the 2-minute walkthrough"
	@echo "  make eval         query-path eval, 7 cases  -> runs/report.md   (~3 min, ~\$$0.50)"
	@echo "  make eval-ingest  ingestion eval, 14 documents across 3 sets    (~7 min, ~\$$1.20)"
	@echo "                    scope it: --sets holdouts --only gkdb        (~20s,  ~\$$0.15)"
	@echo "  make probe        verifier probe, 10 runs per claim             (~2 min, ~\$$0.05)"
	@echo "  make test         unit tests                                    (offline, free)"
	@echo ""
	@echo "  eval-ingest-save  same, but overwrites the committed transcript"

doctor:
	@$(PY) -m docint.cli doctor

ingest:
	@$(PY) -m docint.cli ingest corpus/

# make ask Q="did Acme bill above contract?" PROFILE=external_auditor
#
# PROFILE, not AS: `AS` is a built-in make variable (the assembler, default "as"),
# so `AS ?= procurement_analyst` is silently ignored and the command runs as
# `--as as`. It fails loudly at argparse, but only after you have typed a question.
Q ?= Did Acme bill us above their contracted rate on INV-2026-0117, and does the invoice stay within what PO-2026-0043 committed?
PROFILE ?= procurement_analyst
ask:
	@$(PY) -m docint.cli ask "$(Q)" --as $(PROFILE)

demo:
	@$(PY) scripts/demo.py

eval:
	@$(PY) eval/harness.py

eval-ingest:
	@$(PY) eval/run_ingest_eval.py

# Separate target because it rewrites a committed artifact. Regenerating the
# transcript should be a decision, not a side effect of looking at the numbers.
eval-ingest-save:
	@$(PY) eval/run_ingest_eval.py 2>&1 | tee eval/ingest_eval_output.txt

probe:
	@$(PY) eval/verifier_probe.py

test:
	@$(PY) -m pytest tests/ -q

# Forget everything and re-ingest from scratch. The manifest is the idempotence
# record, so deleting it is the only way to make `ingest` do real work twice.
clean-index:
	@rm -rf runs/chroma runs/manifest.json
	@echo "index and manifest cleared - the next ingest will re-run every model call"

review:
	@$(PY) -m docint.cli review
