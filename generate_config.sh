#!/usr/bin/env bash
# Interactive first-time setup (like mailcow's generate_config.sh). Writes .env.
exec "$(dirname "$(readlink -f "$0")")/mailstack.sh" init "$@"
