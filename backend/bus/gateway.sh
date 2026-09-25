#!/usr/bin/env bash
# Start the RescueGrid twin gateway on :8097 (needs the bus :8096, Kenil's Q&A :8095 and the shared Neo4j :7688).
# Serves the live twin build (mockfrontend/edgeAI/frontend/dist-live) at / ; from a laptop: ssh -L 8097:127.0.0.1:8097 hp2@<nano>
cd "$(dirname "$0")" && exec .venv/bin/uvicorn gateway:app --host 127.0.0.1 --port 8097 "$@"
