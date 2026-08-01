#!/bin/bash
set -e

# Run DB migration helper
# PYTHONPATH=/app is required here: Python only adds the script's own
# directory (migrations/) to sys.path by default, not the working
# directory (/app) where config.py lives. Without this, the script fails
# immediately every boot with "ModuleNotFoundError: No module named
# 'config'" (harmless since it's wrapped in `|| true`, but it means this
# migration silently never actually runs).
PYTHONPATH=/app python migrations/001_add_repostrule_fields.py || true

# Start the bot
exec python bot.py
