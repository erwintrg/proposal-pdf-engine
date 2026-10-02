PY ?= .venv/bin/python

.PHONY: setup demo test scan

setup:
	python3 -m venv .venv
	$(PY) -m pip install -r requirements-dev.txt
	$(PY) -m playwright install chromium

demo:
	$(PY) demo.py

test:
	$(PY) -m pytest -q

scan:
	gitleaks dir . --no-banner
	gitleaks git . --no-banner
