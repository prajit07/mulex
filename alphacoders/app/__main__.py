"""Launch the local evaluator workbench from the repository root."""

import argparse

import uvicorn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, proxy_headers=False)


if __name__ == "__main__":
    main()
