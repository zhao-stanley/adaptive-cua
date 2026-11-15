"""Serve a local web page at http://127.0.0.1:8000: serve [PATH]. PATH is a directory or an HTML file."""

import argparse
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT = Path(__file__).parent / "demo"
PORT = 8000


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT, help="Directory or HTML file (default: demo).")
    args = parser.parse_args()
    if not args.path.exists():
        parser.error(f"{args.path} does not exist.")
    root = args.path if args.path.is_dir() else args.path.parent
    page = "" if args.path.is_dir() or args.path.name == "index.html" else args.path.name
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(root))
    with ThreadingHTTPServer(("127.0.0.1", PORT), handler) as server:
        print(f"Serving http://127.0.0.1:{PORT}/{page}  (Ctrl+C to stop)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
