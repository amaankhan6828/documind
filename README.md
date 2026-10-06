# DocuMind

DocuMind is an enterprise multimodal document intelligence and reasoning platform. It is intended to help users retrieve and reason over documents with traceable, page-level evidence.

## Milestone 1 scope

This milestone establishes the initial project foundation: a FastAPI backend with `GET /health`, a minimal Next.js and TypeScript application shell, one backend health test, local setup instructions, and a small Docker Compose setup for the two applications. It does not include document processing, data stores, AI features, authentication, or other later milestones.

## Prerequisites

- Python 3.11 or newer
- Node.js 20.9 or newer and npm
- Docker Compose (optional, for container startup)

## Local setup

### Backend

From the repository root, create and activate a virtual environment, then install the backend requirements:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r backend/requirements-dev.txt
uvicorn backend.app.main:app --reload
```

The API is available at `http://localhost:8000`; check `http://localhost:8000/health` for `{"status":"ok"}`.

Run the test from the repository root:

```bash
pytest
```

### Frontend

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`.

### Docker Compose

Copy `.env.example` to `.env` if you want a local environment file, then start the application services:

```bash
docker compose up --build
```

Compose starts only the backend and frontend. No databases, queues, or vector/graph stores are needed for this milestone.
