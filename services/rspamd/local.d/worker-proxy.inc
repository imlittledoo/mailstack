bind_socket = "*:11332";
milter = yes;
timeout = 120s;
allow_file_and_shm_inputs = false;
upstream "local" {
  default = yes;
  self_scan = yes;
}
