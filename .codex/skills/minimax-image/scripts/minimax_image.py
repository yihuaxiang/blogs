#!/usr/bin/env python3
"""Generate images with MiniMax image models via the MiniMax API."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import sys
from typing import Any
from typing import List
from typing import Optional
from typing import Tuple
from urllib import error
from urllib import request

DEFAULT_MODEL = "image-01"
DEFAULT_ASPECT_RATIO = "1:1"
DEFAULT_TIMEOUT = 120
DEFAULT_OUTPUT = "output/minimax-image/output.jpeg"
API_ENDPOINT = "https://api.minimaxi.com/v1/image_generation"
API_KEY_ENV_VAR = "MINIMAX_API_KEY"

SUPPORTED_ASPECT_RATIOS = (
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
)


def _die(message: str, *, details: Optional[str] = None, exit_code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    if details:
        print(details, file=sys.stderr)
    raise SystemExit(exit_code)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate images with MiniMax image models via the HTTP API."
    )
    prompt_group = parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt", help="Prompt text")
    prompt_group.add_argument("--prompt-file", help="Path to a UTF-8 prompt file")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model name. Default: {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--aspect-ratio",
        default=DEFAULT_ASPECT_RATIO,
        choices=SUPPORTED_ASPECT_RATIOS,
        help=f"Image aspect ratio. Default: {DEFAULT_ASPECT_RATIO}",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=1,
        help="Number of images to request. Default: 1",
    )
    parser.add_argument(
        "--subject-reference",
        action="append",
        help="Subject reference for image-to-image. Format: type,url (e.g., character,https://example.com/img.jpg)",
    )
    parser.add_argument(
        "--response-format",
        choices=("base64", "url"),
        default="base64",
        help="Response format. Default: base64",
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds")
    parser.add_argument(
        "--out",
        default=DEFAULT_OUTPUT,
        help=f"Output file for one image. Default: {DEFAULT_OUTPUT}",
    )
    parser.add_argument("--out-dir", help="Output directory for multi-image responses")
    parser.add_argument("--save-response", help="Optional path for the raw JSON response")
    parser.add_argument("--force", action="store_true", help="Overwrite existing output files")
    parser.add_argument("--dry-run", action="store_true", help="Print the request payload without calling the API")
    args = parser.parse_args()

    if args.n < 1:
        _die("--n must be at least 1")
    if args.timeout < 1:
        _die("--timeout must be at least 1")

    return args


def _read_prompt(args: argparse.Namespace) -> str:
    if args.prompt:
        return args.prompt.strip()

    path = Path(args.prompt_file)
    if not path.is_file():
        _die(f"Prompt file not found: {path}")
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        _die(f"Prompt file is empty: {path}")
    return content


def _parse_subject_reference(value: str) -> Tuple[str, str]:
    parts = value.split(",", 1)
    if len(parts) != 2:
        _die(f"Invalid subject reference format: {value}", details="Expected: type,url")
    ref_type, url = parts[0].strip(), parts[1].strip()
    if ref_type not in ("character", "subject"):
        _die(f"Invalid subject reference type: {ref_type}", details="Supported types: character, subject")
    return ref_type, url


def _resolve_api_key() -> str:
    value = os.getenv(API_KEY_ENV_VAR)
    if value:
        return value
    _die(
        "Missing API key.",
        details=f"Set {API_KEY_ENV_VAR} before calling the live API.",
    )
    return ""


def _build_payload(args: argparse.Namespace, prompt: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": args.model,
        "prompt": prompt,
        "aspect_ratio": args.aspect_ratio,
        "response_format": args.response_format,
    }

    if args.n > 1:
        payload["n"] = args.n

    if args.subject_reference:
        refs = []
        for ref in args.subject_reference:
            ref_type, url = _parse_subject_reference(ref)
            refs.append({"type": ref_type, "image_file": url})
        payload["subject_reference"] = refs

    return payload


def _build_output_paths(args: argparse.Namespace, count: int) -> List[Path]:
    if args.out_dir:
        out_dir = Path(args.out_dir)
        paths = [out_dir / f"image_{index}.jpeg" for index in range(1, count + 1)]
    else:
        out_path = Path(args.out)
        if not out_path.suffix:
            out_path = out_path.with_suffix(".jpeg")
        if count == 1:
            paths = [out_path]
        else:
            paths = [
                out_path.with_name(f"{out_path.stem}-{index}{out_path.suffix}")
                for index in range(1, count + 1)
            ]

    for path in paths:
        if path.exists() and not args.force:
            _die(f"Output already exists: {path}", details="Use --force to overwrite it.")
    return paths


def _save_json(path_str: str, payload: dict[str, Any]) -> None:
    path = Path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _post_json(endpoint: str, api_key: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=timeout) as response:
            data = response.read().decode("utf-8")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        _die(f"API request failed with HTTP {exc.code}", details=raw)
    except error.URLError as exc:
        _die("API request failed before getting a response.", details=str(exc.reason))

    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        _die("API returned invalid JSON.", details=str(exc))
    return {}


def _extract_images(payload: dict[str, Any], response_format: str) -> List[Any]:
    if payload.get("base_resp", {}).get("status_code") != 0:
        msg = payload.get("base_resp", {}).get("status_msg", "Unknown error")
        _die(f"API returned error: {msg}", details=json.dumps(payload, ensure_ascii=False, indent=2))

    data = payload.get("data", {})
    if response_format == "base64":
        images = data.get("image_base64", [])
        if isinstance(images, str):
            return [images]
        return images if images else []
    else:
        images = data.get("image_url", [])
        if isinstance(images, str):
            return [images]
        return images if images else []


def _download(url: str, destination: Path, timeout: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    req = request.Request(url, method="GET")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            content = response.read()
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        _die(f"Failed to download generated image with HTTP {exc.code}", details=raw)
    except error.URLError as exc:
        _die("Failed to download generated image.", details=str(exc.reason))

    destination.write_bytes(content)


def _save_base64(data: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        decoded = base64.b64decode(data)
    except Exception as exc:
        _die("Failed to decode base64 image data.", details=str(exc))
    destination.write_bytes(decoded)


def main() -> None:
    args = _parse_args()
    prompt = _read_prompt(args)
    payload = _build_payload(args, prompt)

    if args.dry_run:
        preview = {
            "endpoint": API_ENDPOINT,
            "payload": payload,
        }
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return

    api_key = _resolve_api_key()
    response_payload = _post_json(API_ENDPOINT, api_key, payload, args.timeout)

    if args.save_response:
        _save_json(args.save_response, response_payload)

    images = _extract_images(response_payload, args.response_format)
    if not images:
        _die(
            "The API response did not contain any images.",
            details=json.dumps(response_payload, ensure_ascii=False, indent=2),
        )

    output_paths = _build_output_paths(args, len(images))

    for img_data, path in zip(images, output_paths):
        if args.response_format == "base64":
            _save_base64(img_data, path)
        else:
            _download(img_data, path, args.timeout)
        print(path)


if __name__ == "__main__":
    main()
