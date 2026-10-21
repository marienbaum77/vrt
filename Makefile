PY ?= python3
export PYTHONPATH := .

.PHONY: install test dataset calibrate analyze experiments plots demo docker-up docker-demo docker-down

install:            ## runtime + dev dependencies and Chromium
	pip install -r requirements-dev.txt
	$(PY) -m playwright install --with-deps chromium

test:               ## unit + integration tests
	$(PY) -m pytest -q

dataset:            ## build the labelled reference dataset (~100 scenes, several minutes)
	$(PY) -m core dataset generate
	$(PY) -m core dataset extras holdout
	$(PY) -m core dataset extras sensitivity

calibrate:          ## choose thresholds on the calibration split
	$(PY) -m core experiments calibrate

analyze:            ## evaluate on the held-out test split, write results/
	$(PY) -m core experiments analyze

experiments:        ## everything: calibrate, analyze, capture stability, load test, figures
	$(PY) -m core experiments all

plots:
	$(PY) -m core experiments plots

demo:               ## baseline -> regression -> CI gate -> approve, without Docker
	./scripts/demo.sh

docker-up:          ## demo-site, mock-ci, vrt-api, prometheus
	docker compose up -d --build

docker-demo:        ## baseline on /v1, then test /v2 against the mock CI
	docker compose run --rm -e VRT_BASE_URL=http://demo-site/v1 runner vrt baseline -c examples/vrt.docker.yaml
	-docker compose run --rm -e VRT_BASE_URL=http://demo-site/v2 runner

docker-down:
	docker compose down -v
