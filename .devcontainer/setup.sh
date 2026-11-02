#!/usr/bin/env bash
# Runs once when the codespace is created: OCR, Python packages, the user interface.
set -e
sudo apt-get update -y && sudo apt-get install -y --no-install-recommends tesseract-ocr
pip install --upgrade pip
pip install -e backend
cd frontend && npm ci && npm run build
