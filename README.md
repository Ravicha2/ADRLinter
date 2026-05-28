# ADRLinter

## Description

ADRLinter detects Architectural Decision Record (ADR) violations in Python repositories. It ingests source code and ADR documents into an Architectural Decision Graph (ADG), then uses CPT (Contradiction Path Traversal) detection to find conflicts between architectural constraints and actual code. Violations are resolved through a tiered system (explicit supersession, specificity, recency, or human review) and reported back via GitHub commit status.

Part of a research project at UNSW (University of New South Wales).

## Prerequisites

- Docker & Docker Compose
- uv (for local dev)

## Quick Start

```bash
# Copy env file and adjust if needed
cp .env.example .env

# Start services (fast restart)
./dev.sh

# Start with rebuild
./dev.sh --build
```

FastAPI: http://localhost:8000/docs
Neo4j Browser: http://localhost:7474 (neo4j / password)

## Local Development (without Docker)

```bash
cd app
uv sync
uv run uvicorn main:app --reload
```