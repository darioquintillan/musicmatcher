#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
if [ ! -x .venv/bin/musicmatcher ]; then
    .venv/bin/python -m pip install -e .
fi
exec .venv/bin/python -m musicmatcher web
