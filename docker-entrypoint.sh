#!/bin/sh
set -e

# Fix permissions on data directory if running as root
# This handles the case where a volume is mounted with different ownership
if [ "$(id -u)" = "0" ]; then
    # Running as root - fix permissions and drop to appuser
    chown -R appuser:appuser /app/data 2>/dev/null || true
    exec gosu appuser "$@"
else
    # Already running as non-root (appuser)
    # Just ensure directory exists
    mkdir -p /app/data 2>/dev/null || true
    exec "$@"
fi
