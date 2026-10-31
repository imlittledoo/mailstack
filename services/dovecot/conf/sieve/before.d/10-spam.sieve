require ["fileinto", "mailbox"];

# Rspamd marks probable spam with "X-Spam: Yes" (action "add header").
if header :contains "X-Spam" "Yes" {
  fileinto :create "Junk";
  stop;
}
