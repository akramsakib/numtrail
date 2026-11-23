# numtrail — build, test, run
#
#   make install     editable install with dev extras
#   make test        python suite (offline) + edge worker tests
#   make serve       run the web app on :8000
#   make build       sdist + wheel into dist/, then verify the wheel
#   make docker      build and run the container
#   make check        everything CI does, locally

PY ?= python3
PORT ?= 8000

.PHONY: help install test test-py test-worker serve build docker check clean

help:
	@grep -E '^#   ' Makefile | sed 's/^#   //'

install:
	$(PY) -m pip install -e ".[dev]"

test: test-py test-worker

test-py:
	$(PY) -m pytest -q

test-worker:
	node --check numtrail/web/worker.js
	node tests/worker.test.mjs

serve:
	$(PY) -m numtrail.web.server --port $(PORT)

build:
	$(PY) -m pip install --quiet build
	rm -rf dist build
	$(PY) -m build
	@echo "--- verifying the wheel in a clean venv ---"
	$(PY) -m venv /tmp/numtrail-venv
	/tmp/numtrail-venv/bin/pip install --quiet dist/*.whl
	/tmp/numtrail-venv/bin/numtrail --version
	/tmp/numtrail-venv/bin/numtrail --list-modules | head -3
	cd /tmp && /tmp/numtrail-venv/bin/python -c "import pathlib, numtrail; s = Path = pathlib.Path(numtrail.__file__).parent/'web'/'static'; assert (s/'index.html').is_file(), 'UI missing'; assert (s/'catalog.json').is_file(), 'catalogue missing'; print('installed package:', numtrail.__file__); print('ships the UI:', s)"

docker:
	docker build -t numtrail .
	docker run --rm -p 8000:8000 numtrail

check:
	$(PY) -m numtrail.catalog --write
	git diff --exit-code numtrail/web/static/catalog.json || \
		(echo "catalog.json was stale — commit the regenerated file"; false)
	$(PY) -m pytest -q
	node --check numtrail/web/worker.js
	node tests/worker.test.mjs

clean:
	rm -rf dist build *.egg-info .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
