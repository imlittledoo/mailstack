#!/usr/bin/env bash
# Update MailStack (like mailcow's update.sh): pull the latest code, rebuild, restart.
# Database upgrades run automatically when the admin container starts.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
git pull --ff-only
./mailstack.sh update
