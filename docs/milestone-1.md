# Milestone 1: Application Foundation

Milestone 1 establishes the DocuMind repository and local development baseline. The backend exposes only `GET /health`, and the frontend renders a static Next.js application shell. The health endpoint confirms that the API process responds; it does not perform dependency checks.

The automated test checks the health route's HTTP status and JSON response. Docker Compose starts only these two application services. No persistence, queue, document ingestion, AI/model integration, authentication, or analytics capabilities are part of this milestone.

Use the commands in the root [README](../README.md) to run the applications and test locally. Keep later capabilities scoped to their approved milestones and update this document when the foundation changes.
