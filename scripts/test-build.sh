#!/usr/bin/env bash
# Shared by hosted image CI and the PC publisher. Stop on the first failed check.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
uv run --frozen pytest tests -q
