"""Command-line client: upload a proposal file (PDF / .txt / .md) and print the evaluation."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from pathlib import Path
from typing import Any

import httpx

EVALUATE_FILE_PATH = "/api/v1/proposals/evaluate/file"


def evaluate_file(
    client: httpx.Client,
    path: str | Path,
    title: str | None = None,
    skills: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST a proposal file to the service and return the JSON response."""
    path = Path(path)
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    data = {}
    if title:
        data["title"] = title
    if skills:
        data["skills"] = ",".join(skills)
    if metadata:
        data["metadata"] = json.dumps(metadata)
    with path.open("rb") as f:
        response = client.post(EVALUATE_FILE_PATH, files={"file": (path.name, f, content_type)}, data=data)
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise RuntimeError(f"Evaluation failed ({response.status_code}): {detail}")
    return response.json()


def format_result(body: dict[str, Any]) -> str:
    result = body["result"]
    lines = [
        f"Recommendation: {result['recommendation']}",
        f"Overall score:  {result['overall_score']}",
        f"Weighted score: {result.get('weighted_score')}",
        f"Model:          {body['provider']} / {body['model']}",
        "",
        result["summary"],
    ]
    for item in result["skill_evaluations"]:
        lines += ["", f"## {item['skill']}  (score {item['score']}, weight {item['weight']})"]
        lines += [f"  + {s}" for s in item["strengths"]]
        lines += [f"  - {w}" for w in item["weaknesses"]]
        if item["comments"]:
            lines.append(f"  {item['comments']}")
    if result["questions_for_applicant"]:
        lines += ["", "Questions for the applicant:"]
        lines += [f"  ? {q}" for q in result["questions_for_applicant"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a proposal file (PDF, .txt, .md) with the Proposal Reviewer")
    parser.add_argument("file", type=Path, help="Proposal file to upload")
    parser.add_argument("--url", default=os.environ.get("PROPOSAL_REVIEWER_URL", "http://localhost:8000"))
    parser.add_argument(
        "--api-key", default=os.environ.get("PROPOSAL_REVIEWER_API_KEY"), help="Sent as X-API-Key (if the server needs one)"
    )
    parser.add_argument("--title", help="Proposal title (default: file name)")
    parser.add_argument("--skills", help="Comma-separated skill names (default: server defaults)")
    parser.add_argument("--metadata", help='JSON object with extra context, e.g. \'{"requested_hours": 48}\'')
    parser.add_argument("--timeout", type=float, default=900, help="Request timeout in seconds (default: 900)")
    parser.add_argument("--json", action="store_true", help="Print the raw JSON response")
    args = parser.parse_args(argv)

    if not args.file.is_file():
        parser.error(f"File not found: {args.file}")
    try:
        metadata = json.loads(args.metadata) if args.metadata else None
    except json.JSONDecodeError as e:
        parser.error(f"--metadata is not valid JSON: {e}")
    skills = [s.strip() for s in args.skills.split(",") if s.strip()] if args.skills else None
    headers = {"X-API-Key": args.api_key} if args.api_key else {}

    try:
        with httpx.Client(base_url=args.url, headers=headers, timeout=args.timeout) as client:
            body = evaluate_file(client, args.file, args.title, skills, metadata)
    except (RuntimeError, httpx.HTTPError) as e:
        print(e, file=sys.stderr)
        return 1

    print(json.dumps(body, indent=2, ensure_ascii=False) if args.json else format_result(body))
    return 0


if __name__ == "__main__":
    sys.exit(main())
