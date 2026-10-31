#!/bin/sh
# purge-orphans [--delete]: list (or delete) maildirs whose mailbox no longer exists.
# Deleting a mailbox in the admin panel keeps its mail on disk until this is run.
set -eu
DELETE=0
[ "${1:-}" = "--delete" ] && DELETE=1

found=0
for dir in /var/vmail/*/*; do
  [ -d "$dir" ] || continue
  user="$(basename "$dir")@$(basename "$(dirname "$dir")")"
  if ! doveadm user "$user" >/dev/null 2>&1; then
    found=$((found + 1))
    if [ "$DELETE" -eq 1 ]; then
      rm -rf "$dir"
      echo "deleted  $dir ($user)"
    else
      echo "orphaned $dir ($(du -sh "$dir" | cut -f1))"
    fi
  fi
done
rmdir /var/vmail/* 2>/dev/null || true

if [ "$found" -eq 0 ]; then
  echo "no orphaned maildirs"
elif [ "$DELETE" -eq 0 ]; then
  echo "$found orphaned maildir(s). Run with --delete to remove them."
fi
