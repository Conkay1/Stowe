"""Loopback bind address for the local Stowe server.

Importing this module has no side effects. ``run.py`` probes for a free port
at import and, on Python older than 3.10, may re-exec into another
interpreter, so tests read the bind host from here instead.
"""

# No authentication on the API. Loopback only — do not bind a public or LAN interface.
BIND_HOST = "127.0.0.1"
