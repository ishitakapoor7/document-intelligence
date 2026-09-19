PY := .venv/bin/python

.PHONY: help doctor ingest review demo eval eval-ingest eval-ingest-save probe test clean-index

help:
	@echo "  make doctor       check the environment before anything else"
	@echo "  make ingest       parse, classify and extract corpus/"
	@echo "  make review       list documents waiting on a human"
	@echo "  make demo         the 2-minute walkthrough"
	@echo "  make eval         query-path eval, 7 cases  -> runs/report.md   (~3 min, ~\$$0.50)"
	@echo "  make eval-ingest  ingestion eval, 14 documents across 3 sets    (~8 min, ~\$$1.50)"
	@echo "  make probe        verifier probe, 10 runs per claim             (~2 min, ~\$$0.05)"
	@echo "  make test         unit tests                                    (offline, free)"
	@echo ""
	@echo "  eval-ingest-save  same, but overwrites the committed transcript"

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
