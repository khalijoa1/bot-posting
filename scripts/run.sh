#!/bin/bash
set -e

# Run DB migration helper
python migrations/001_add_repostrule_fields.py || true

# Start the bot
exec python bot.py
