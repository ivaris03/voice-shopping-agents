"""Supported Uvicorn launcher for the Voice Shopping API."""

import argparse
import asyncio
import sys


def windows_selector_loop_factory() -> asyncio.AbstractEventLoop:
    """Create the event loop required by async psycopg on Windows."""
    return asyncio.SelectorEventLoop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Voice Shopping API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        "voice_shopping_api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        loop=(
            "voice_shopping_api.server:windows_selector_loop_factory"
            if sys.platform == "win32"
            else "auto"
        ),
    )


if __name__ == "__main__":
    main()
