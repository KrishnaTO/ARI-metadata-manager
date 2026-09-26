"""Launch the PR mapping reviewer: ``python -m pr_review [--port 8002]``."""
import argparse
import webbrowser

import uvicorn

parser = argparse.ArgumentParser(description="ARI PR mapping review")
parser.add_argument("--port", type=int, default=8002)
parser.add_argument("--no-browser", action="store_true")
args = parser.parse_args()

url = f"http://127.0.0.1:{args.port}"
if not args.no_browser:
    webbrowser.open(url)
print(f"ARI PR mapping review at {url}")
uvicorn.run("pr_review.server:app", host="127.0.0.1", port=args.port)
