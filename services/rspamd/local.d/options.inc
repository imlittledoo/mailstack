# Only loopback is "local". Docker's userland proxy makes remote clients appear
# from private addresses, so the default private ranges must not be trusted.
local_addrs = "127.0.0.0/8, ::1";
dns {
  timeout = 2s;
  retransmits = 3;
}
