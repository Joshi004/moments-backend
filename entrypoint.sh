#!/bin/bash
set -e

# Run database migrations unless explicitly disabled.
# The API container leaves this at the default (true) and owns all migrations.
# The worker container sets RUN_MIGRATIONS=false so it never migrates independently.
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    echo "Running database migrations..."
    alembic upgrade head
    echo "Migrations complete."
fi

# Replace this shell process with the container's CMD (uvicorn or run_worker.py),
# preserving PID 1 and correct signal handling.
exec "$@"
