"""Run the canonical local research web application."""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(description="GraphRAG_AL interactive research application")
    parser.add_argument("--fixture", action="store_true", help="Use controlled generated data")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.fixture:
        os.environ["KLIMA_UI_FIXTURE"] = "1"
    import uvicorn

    uvicorn.run("crawler.web.app:create_app", factory=True, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
