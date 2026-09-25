#!/usr/bin/env bash
# Start the RescueGrid event bus on :8096 (needs the shared Neo4j on :7688 and Kenil's package at /home/hp2/kenil/rescuegrid-reasoning).
cd "$(dirname "$0")" && exec .venv/bin/uvicorn event_bus:app --host 127.0.0.1 --port 8096 "$@"
