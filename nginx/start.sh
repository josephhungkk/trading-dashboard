#!/bin/sh
# Phase 1 nginx start script — simpler than Dashboard_old's (no cert-reload watcher,
# since CF Tunnel handles TLS at edge; no certbot volumes needed on the VPS).
set -eu

exec /docker-entrypoint.sh nginx -g 'daemon off;'
