PYTHON ?= .venv/bin/python

.PHONY: install dev worker check test eval demo up down smoke vectors
install:
	python3.13 -m venv .venv
	$(PYTHON) -m pip install -r requirements.lock
	$(PYTHON) -m pip install --no-deps -e .
dev:
	$(PYTHON) -m scripts.dev
vectors:
	docker compose --env-file /dev/null -f compose.qdrant.yaml up -d
test:
	$(PYTHON) -m pytest
eval:
	$(PYTHON) -m eval.run --split dev --output reports/dev.json
	$(PYTHON) -m eval.run --split holdout
demo:
	$(PYTHON) -m scripts.build_demo
	$(PYTHON) -m http.server 8011 --bind 127.0.0.1 --directory demo/dist
up:
	docker compose up --build -d
down:
	docker compose down
smoke:
	$(PYTHON) -m scripts.smoke --url http://127.0.0.1:8010

worker:
	$(PYTHON) -m scripts.worker
check:
	$(PYTHON) -m scripts.check_backend
