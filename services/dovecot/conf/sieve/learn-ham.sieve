require ["vnd.dovecot.pipe", "copy", "imapsieve", "environment", "variables"];

# Deleting spam (Junk -> Trash) is not a "this is ham" signal.
if environment :matches "imap.mailbox" "*" {
  set "mailbox" "${1}";
}
if string "${mailbox}" "Trash" {
  stop;
}

pipe :copy "rspamd-learn" ["ham"];
