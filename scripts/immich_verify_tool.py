#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import requests


DEFAULT_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif", ".bmp", ".tif", ".tiff",
    ".mov", ".mp4", ".m4v", ".avi", ".mkv", ".webm", ".3gp",
    ".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2",
}


@dataclass
class FileResult:
    path: str
    status: str
    http_status: int | None
    detail: str | None
    bytes: int
    sha1: str
    elapsed_sec: float


def normalize_api_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.endswith("/api"):
        url += "/api"
    return url


def sha1_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sha1_hex_to_base64(sha1_hex: str) -> str:
    return base64.b64encode(bytes.fromhex(sha1_hex)).decode("ascii")


def search_existing_assets(
    *,
    session: requests.Session,
    api_url: str,
    api_key: str,
    sha1_hex: str,
    timeout: int,
) -> tuple[bool, str | None]:
    headers = {
        "x-api-key": api_key,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    payload = {
        "checksum": sha1_hex_to_base64(sha1_hex),
        "isTrashed": False,
        "page": 1,
        "size": 10,
    }

    try:
        resp = session.post(
            f"{api_url}/search/metadata",
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as e:
        return False, f"search_error: {e}"

    if resp.status_code != 200:
        detail = None
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text[:500] if resp.text else None
        return False, f"search_http_{resp.status_code}: {detail}"

    try:
        body = resp.json()
    except Exception:
        return False, "search_invalid_json"

    assets = body.get("assets")
    if isinstance(assets, dict):
        items = assets.get("items", []) or []
    elif isinstance(assets, list):
        items = assets
    else:
        items = []

    if not items:
        return False, None

    first = items[0]
    asset_id = first.get("id") if isinstance(first, dict) else None
    return True, str(asset_id) if asset_id else "match_found"


def iter_media_files(root: Path, recursive: bool, exts: set[str]) -> list[Path]:
    files: list[Path] = []
    iterator = root.rglob("*") if recursive else root.glob("*")
    for p in iterator:
        if p.is_file() and p.suffix.lower() in exts:
            files.append(p)
    return sorted(files)


def upload_one(
    *,
    session: requests.Session,
    api_url: str,
    api_key: str,
    file_path: Path,
    dry_run: bool,
    timeout: int,
    root: Path,
) -> FileResult:
    t0 = time.perf_counter()
    file_size = file_path.stat().st_size
    sha1 = sha1_file(file_path)
    rel_path = str(file_path.relative_to(root))
    mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"

    exists, existing_detail = search_existing_assets(
        session=session,
        api_url=api_url,
        api_key=api_key,
        sha1_hex=sha1,
        timeout=timeout,
    )

    if exists:
        return FileResult(
            path=rel_path,
            status="already_exists",
            http_status=200,
            detail=existing_detail,
            bytes=file_size,
            sha1=sha1,
            elapsed_sec=round(time.perf_counter() - t0, 3),
        )

    if dry_run:
        return FileResult(
            path=rel_path,
            status="would_upload",
            http_status=None,
            detail="dry-run",
            bytes=file_size,
            sha1=sha1,
            elapsed_sec=round(time.perf_counter() - t0, 3),
        )

    headers = {
        "x-api-key": api_key,
        "Accept": "application/json",
    }

    # These form fields are intentionally minimal and conservative.
    # Immich will deduplicate on the server side using hashing.
    data = {
        "deviceAssetId": sha1,
        "deviceId": "python-local-uploader",
        "fileCreatedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(file_path.stat().st_mtime)),
        "fileModifiedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(file_path.stat().st_mtime)),
        "isFavorite": "false",
        "isArchived": "false",
    }

    files = {
        "assetData": (file_path.name, file_path.open("rb"), mime),
    }

    try:
        resp = session.post(
            f"{api_url}/assets",
            headers=headers,
            data=data,
            files=files,
            timeout=timeout,
        )
    except requests.RequestException as e:
        return FileResult(
            path=rel_path,
            status="request_error",
            http_status=None,
            detail=str(e),
            bytes=file_size,
            sha1=sha1,
            elapsed_sec=round(time.perf_counter() - t0, 3),
        )
    finally:
        files["assetData"][1].close()

    detail: str | None = None
    try:
        payload = resp.json()
        if isinstance(payload, dict):
            detail = payload.get("message") or payload.get("error") or json.dumps(payload, ensure_ascii=False)
        else:
            detail = json.dumps(payload, ensure_ascii=False)
    except Exception:
        detail = resp.text[:500] if resp.text else None

    # Treat 200/201 with duplicate-ish hints as success states.
    if resp.status_code == 201:
        status = "uploaded"
    elif resp.status_code == 200:
        lowered = (detail or "").lower()
        if "duplicate" in lowered:
            status = "duplicate"
        else:
            status = "ok_200"
    else:
        status = "failed"

    return FileResult(
        path=rel_path,
        status=status,
        http_status=resp.status_code,
        detail=detail,
        bytes=file_size,
        sha1=sha1,
        elapsed_sec=round(time.perf_counter() - t0, 3),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Immich に対してローカルファイルを確認・アップロードする純Pythonツール")
    parser.add_argument("path", help="対象フォルダ")
    parser.add_argument("--url", required=True, help="Immich URL。/api なしでも可")
    parser.add_argument("--api-key", required=True, help="Immich API key")
    parser.add_argument("--recursive", action="store_true", default=True, help="再帰的に探索する")
    parser.add_argument("--no-recursive", dest="recursive", action="store_false", help="再帰探索しない")
    parser.add_argument("--dry-run", action="store_true", help="実アップロードせず対象確認だけ行う")
    parser.add_argument("--timeout", type=int, default=120, help="1ファイルあたりのHTTPタイムアウト秒")
    parser.add_argument("--sleep", type=float, default=0.0, help="各ファイル送信の間に挟む秒数")
    parser.add_argument("--report", default="", help="JSONレポート保存先")
    parser.add_argument("--only", nargs="*", default=[], help="拡張子を限定。例: --only .heic .mov")
    args = parser.parse_args()

    root = Path(args.path).expanduser().resolve()
    if not root.exists():
        print(f"パスが存在しません: {root}", file=sys.stderr)
        return 1
    if not root.is_dir():
        print(f"フォルダを指定してください: {root}", file=sys.stderr)
        return 1

    api_url = normalize_api_url(args.url)
    exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in (args.only or [])} or DEFAULT_EXTENSIONS
    files = iter_media_files(root, recursive=args.recursive, exts=exts)

    if not files:
        print("対象ファイルが見つかりませんでした。", file=sys.stderr)
        return 1

    report_path = Path(args.report).expanduser().resolve() if args.report else (
        Path.cwd() / f"immich_python_report_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )

    print(f"対象フォルダ: {root}")
    print(f"対象件数: {len(files)}")
    print(f"API URL: {api_url}")
    print("開始します...\n")

    session = requests.Session()
    results: list[FileResult] = []

    counts: dict[str, int] = {}
    for idx, file_path in enumerate(files, start=1):
        result = upload_one(
            session=session,
            api_url=api_url,
            api_key=args.api_key,
            file_path=file_path,
            dry_run=args.dry_run,
            timeout=args.timeout,
            root=root,
        )
        results.append(result)
        counts[result.status] = counts.get(result.status, 0) + 1

        print(f"[{idx}/{len(files)}] {result.status:13} {result.path}")
        if result.status in {"failed", "request_error", "already_exists"} and result.detail:
            print(f"    detail: {result.detail}")

        if args.sleep > 0 and idx != len(files):
            time.sleep(args.sleep)

    payload = {
        "root": str(root),
        "api_url": api_url,
        "dry_run": args.dry_run,
        "counts": counts,
        "results": [asdict(r) for r in results],
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n完了")
    print(json.dumps(counts, ensure_ascii=False, indent=2))
    print(f"レポート: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
