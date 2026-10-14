#!/usr/bin/env python3
"""Static file server for the office-sim prototype.

The same as ``python3 -m http.server`` except that it tells the browser never to
cache anything.

That matters more than it sounds. This is a prototype under constant edit, and
the stock server sends no ``Cache-Control`` header at all, so browsers fall back
to heuristic caching and can hold on to a module that has since changed. The
failure that causes is badly disguised: if a cached module imports a file that
has since been renamed or deleted, the import 404s, the whole module graph fails
and *no* JavaScript runs. The page then renders its static HTML and nothing
else — no scenario buttons, no agents, an empty 3D view — which reads like the
application has catastrophically broken rather than like a stale file. It has
cost two debugging sessions already.

Usage:
    tools/serve.py [port] [directory]

Defaults to port 8731 serving the current directory, matching the README.
"""

from __future__ import annotations

import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 8731


class NoCacheHandler(SimpleHTTPRequestHandler):
    """Serves files, and asks the browser not to keep any of them."""

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        # One line per request is noise in a journal; errors still surface
        # through log_error, which does not route through here.
        pass


def main(argv: list[str]) -> int:
    port = int(argv[1]) if len(argv) > 1 else DEFAULT_PORT
    directory = argv[2] if len(argv) > 2 else "."

    handler = partial(NoCacheHandler, directory=directory)
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        print(f"office-sim: serving {directory} on http://localhost:{port}/", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
