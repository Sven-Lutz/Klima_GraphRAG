"""Run the canonical local research web application."""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(description="GraphRAG_AL interactive research application")
    parser.add_argument("--fixture", action="store_true", help="Use controlled generated data")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--legacy", action="store_true", help="Open the earlier research workbench")
    parser.add_argument("--directory", default="generated/conversational-local")
    parser.add_argument(
        "--local-config", help="Existing local corpus/index/model JSON; read-only, no rebuild"
    )
    parser.add_argument("--answer-config", help="Explicit qualified local Ollama JSON; no fallback")
    args = parser.parse_args()
    if args.local_config and (args.fixture or args.legacy):
        parser.error("Select local corpus or fixture/legacy, never both")
    if args.fixture:
        os.environ["KLIMA_UI_FIXTURE"] = "1"
    import uvicorn

    if args.legacy:
        uvicorn.run("crawler.web.app:create_app", factory=True, host=args.host, port=args.port)
    else:
        from crawler.web.app import _default_service
        from crawler.web.conversation import create_conversation_app

        if args.host not in {"127.0.0.1", "localhost", "::1"}:
            parser.error("Conversational preview is loopback-only; external acceptance is separate")
        generation = None
        if args.answer_config:
            from crawler.web.conversation_provider import load_generation

            generation = load_generation(args.answer_config)
        if args.local_config:
            from crawler.web.workbench_runtime import create_local_workbench_service

            service = create_local_workbench_service(args.local_config)
        else:
            service = None if args.fixture else _default_service()
        app = create_conversation_app(service, args.directory, generation=generation)
        uvicorn.run(app, host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
