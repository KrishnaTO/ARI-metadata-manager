#!/usr/bin/env python3
"""Launcher for the ARI Metadata Manager.

Usage:
    python run.py [--port 8001] [--file path/to/ontology.owl]
"""
import argparse
import os
import sys
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ARI Metadata Manager")
    # PORT (documented in .env.example) is the default; --port still wins.
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8001),
                        help="Port to serve on (default: $PORT, else 8001)")
    parser.add_argument("--file",
                        default=str(HERE / "ontologies" / "ari_t1d.owl"),
                        help="Path to OWL ontology file")
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't open browser automatically")
    args = parser.parse_args()

    os.environ["ARI_ONTOLOGY_FILE"] = args.file

    import uvicorn
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        webbrowser.open(url)
    print(f"Starting ARI Metadata Manager at {url}")
    print(f"  Ontology: {args.file}")
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, reload=False)
