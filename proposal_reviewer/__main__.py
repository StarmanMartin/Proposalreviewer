import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Proposal Reviewer web service")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--config", help="Path to config.yaml (default: $PROPOSAL_REVIEWER_CONFIG or ./config.yaml)")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes (development)")
    args = parser.parse_args()
    if args.config:
        os.environ["PROPOSAL_REVIEWER_CONFIG"] = args.config
    uvicorn.run("proposal_reviewer.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
