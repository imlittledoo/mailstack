<?php
// MailStack overrides for Roundcube. Loaded after the image's generated config.

// Internal connections go to "dovecot"/"postfix", which do not match the public
// certificate name. The traffic never leaves the Docker network.
$mailstack_tls = [
    'ssl' => [
        'verify_peer'       => false,
        'verify_peer_name'  => false,
        'allow_self_signed' => true,
    ],
];
$config['imap_conn_options'] = $mailstack_tls;
$config['smtp_conn_options'] = $mailstack_tls;
$config['managesieve_conn_options'] = $mailstack_tls;

$config['smtp_user'] = '%u';
$config['smtp_pass'] = '%p';

$config['managesieve_host'] = 'tls://dovecot';
$config['managesieve_port'] = 4190;
$config['managesieve_vacation'] = 1;

// Moving mail to Junk trains Rspamd through Dovecot IMAPSieve.
$config['markasjunk_learning_driver'] = null;

$config['product_name'] = 'MailStack Webmail';
$config['request_path'] = '/webmail/';
$config['use_https'] = true;
$config['login_autocomplete'] = 2;
$config['session_lifetime'] = 60;
$config['ip_check'] = false;
$config['support_url'] = '/';
$config['create_default_folders'] = true;
