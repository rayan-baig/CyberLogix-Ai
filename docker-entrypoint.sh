#!/bin/sh
# Start CyberLogix as the unprivileged `cyberlogix` user, whatever owns the disk.
#
# A host's persistent disk is often mounted owned by root. The application
# runs as `cyberlogix`, so on such a disk it could not create its database and
# crashed on start ("unable to open database file"). This runs first, as root,
# only to give the data folders to `cyberlogix`, and then hands over: from
# `exec setpriv` on, nothing runs as root.
set -e

if [ "$(id -u)" = "0" ]; then
  for dir in "$(dirname "${CYBERLOGIX_DB_PATH:-/app/data/cyberlogix.db}")" "${CYBERLOGIX_BACKUP_DIR:-}"; do
    [ -n "$dir" ] || continue
    mkdir -p "$dir"
    chown -R cyberlogix:cyberlogix "$dir"
  done
  exec setpriv --reuid=cyberlogix --regid=cyberlogix --init-groups "$@"
fi

# Already unprivileged (a host that runs the container as a set user).
exec "$@"
