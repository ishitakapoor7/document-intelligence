PY := .venv/bin/python

.PHONY: help doctor ingest demo eval eval-ingest probe test clean-index

help:
	@echo "  make doctor       check the environment before anything else"
	@echo "  make ingest       parse, classify and extract corpus/"
	@echo "  make demo         the 2-minute walkthrough"
	@echo "  make eval         query-path eval    -> runs/report.md"
	@echo "  make eval-ingest  ingestion eval across both document sets"
	@echo "  make probe        verifier probe, 10 runs per claim"
	@echo "  make test         unit tests"

doctor:
	@$(PY) -m docint.cli doctor

ingest:
	@$(PY) -m docint.cli ingest corpus/

demo:
	@$(PY) scripts/demo.py

eval:
	@$(PY) eval/harness.py

eval-ingest:
	@$(PY) eval/run_ingest_eval.py

probe:
	@$(PY) eval/verifier_probe.py

test:
	@$(PY) -m pytest tests/ -q

# Forget everything and re-ingest from scratch. The manifest is the idempotence
# record, so deleting it is the only way to make `ingest` do real work twice.
clean-index:
	@rm -rf runs/chroma runs/manifest.json
	@echo "index and manifest cleared - the next ingest will re-run every model call"
