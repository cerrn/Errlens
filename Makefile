PYTHON ?= python3

.PHONY: install test backend frontend fixture types demo-repo warm live-test

install:            ## Install backend and frontend dependencies
	cd backend && $(PYTHON) -m pip install -r requirements-dev.txt
	cd frontend && npm install

test:               ## Run backend tests
	cd backend && $(PYTHON) -m pytest -q

backend:            ## API on http://localhost:8000
	cd backend && $(PYTHON) -m uvicorn app.main:app --reload --port 8000

fixture:            ## API in fixture mode (no keys needed)
	cd backend && ERRLENS_FIXTURE_MODE=true $(PYTHON) -m uvicorn app.main:app --reload --port 8000

frontend:           ## UI on http://localhost:3000
	cd frontend && npm run dev

types:              ## models.py -> frontend/lib/types.ts
	cd frontend && npm run gen:types

demo-repo:          ## Build the demo repository in ../errlens-demo
	$(PYTHON) demo/build_demo_repo.py ../errlens-demo

live-test:          ## Run the 3 demo scenarios against the real LLM from backend/.env
	cd backend && $(PYTHON) scripts/live_llm_test.py -v

warm:               ## Run the demo scenarios / warm the cache: make warm API=... REPO=...
	cd backend && $(PYTHON) scripts/demo_runner.py --api $(API) --repo $(REPO)
