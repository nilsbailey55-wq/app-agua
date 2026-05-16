#!/bin/bash
# Agua Argentina — start backend
cd "$(dirname "$0")/backend"
uvicorn main:app --reload --port 8000
