"""Single model copy and concurrent requests for the SQLite deployment."""
bind = "127.0.0.1:8000"
workers = 1
worker_class = "gthread"
threads = 4
timeout = 240
graceful_timeout = 240
keepalive = 5
preload_app = False
accesslog = "-"
errorlog = "-"
capture_output = True
# Only the local Nginx proxy may supply forwarded scheme headers.
forwarded_allow_ips = "127.0.0.1"
