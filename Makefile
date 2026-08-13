.PHONY: dev up down migrate revision test lint fmt build deploy

up:            ## start local postgres
	docker compose up -d db

down:
	docker compose down

dev: up        ## run the API with reload
	cd backend && uvicorn app.main:app --reload --port 8000

web:
	cd frontend && npm run dev

migrate:
	cd backend && alembic upgrade head

revision:      ## make m="add sessions table"
	cd backend && alembic revision --autogenerate -m "$(m)"

test:
	cd backend && pytest -q

lint:
	cd backend && ruff check . && mypy app
	cd frontend && npm run lint

fmt:
	cd backend && ruff format . && ruff check --fix .

build:
	docker build -t augur-api:dev backend

deploy:
	helm upgrade --install augur deploy/chart -n augur --create-namespace
