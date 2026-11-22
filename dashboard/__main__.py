"""python -m dashboard serve | sync [--scan] [--setup] | login

    serve   the API + dashboard on http://0.0.0.0:8000 (syncs on schedule)
    sync    one sync now and exit (--scan adds the profile scan, --setup rescans every week for everyone)
    login   log in to Strava in a browser window and save the session to OPENSTRIDE_SESSION_FILE
"""
import argparse
import os
import sys

from openstride import OpenStrideError, login

from .config import from_env


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m dashboard")
    sub = p.add_subparsers(dest="command", required=True)
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    sv.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    sy = sub.add_parser("sync")
    sy.add_argument("--scan", action="store_true")
    sy.add_argument("--setup", action="store_true")
    sub.add_parser("login")
    args = p.parse_args(argv)
    try:
        s = from_env()
    except ValueError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    if args.command == "serve":
        import uvicorn

        from .api import create_app
        uvicorn.run(create_app(s), host=args.host, port=args.port)
        return 0
    if args.command == "login":
        print(f"Session saved to {login(s.session_file, channel=s.browser_channel or None)}")
        return 0
    from .sync import run_sync
    try:
        print(run_sync(s, scan=args.scan, setup=args.setup))
    except OpenStrideError as e:
        print(f"sync failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
