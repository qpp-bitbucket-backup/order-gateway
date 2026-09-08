"""
Test uploading a local file to Alibaba Cloud OSS with the current .env credentials.

Verifies the full round trip with the OSSService client (oss2 SDK):
put_object_from_file -> head_object (size check) -> pre-signed download URL
-> MD5 comparison -> optional cleanup. Useful after rotating
OSS_ACCESS_KEY_ID/SECRET or switching OSS_BUCKET_NAME.

Usage:
    python scripts/test_oss_upload.py                           # auto-generated small text file
    python scripts/test_oss_upload.py --file app/tmp/front.pdf  # upload a real local file
    python scripts/test_oss_upload.py --keep                    # keep the uploaded object
"""
import argparse
import hashlib
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("test_oss_upload")

import httpx
import oss2

from app.core.config import settings
from app.services.oss import oss_service


def _md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _retry(desc: str, func, attempts: int = 3, delay: float = 1.5):
    """Retry a call on oss2 RequestError (-2). Endpoint-security TLS MITM
    (e.g. Kaspersky) makes handshakes fail intermittently; a fresh
    connection usually goes through."""
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except oss2.exceptions.RequestError as exc:
            if attempt == attempts:
                raise
            logger.warning(f"[{desc}] network error on attempt {attempt}/{attempts}: {exc} — retrying")
            time.sleep(delay)


def _default_local_file(tmp_dir: str) -> str:
    """Create a small unique text file when --file is not given."""
    path = os.path.join(tmp_dir, "oss_upload_test.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"OSS upload test from order-gateway @ {datetime.now(timezone.utc).isoformat()}\n")
    return path


def test_upload(local_path: str, object_key: str, keep: bool) -> bool:
    bucket = oss_service.bucket
    local_size = os.path.getsize(local_path)
    local_md5 = _md5_file(local_path)
    logger.info(f"Uploading {local_path} ({local_size} bytes, md5={local_md5}) -> '{object_key}'")

    # 1) Upload (retried: PUT is idempotent per key)
    try:
        result = _retry("upload", lambda: bucket.put_object_from_file(object_key, local_path))
    except oss2.exceptions.OssError as exc:
        logger.error(f"[upload] OSS error: {exc.status} {exc.code} {exc.message} (request_id={exc.request_id})")
        return False
    if result.status != 200:
        logger.error(f"[upload] unexpected HTTP {result.status}")
        return False
    logger.info(f"[upload] OK (HTTP 200, request_id={result.request_id})")
    logger.info(f"[upload] url (public): {oss_service.get_object_url(object_key)}")
    logger.info(
        f"[upload] url (pre-signed, valid {settings.OSS_DOWNLOAD_EXPIRY_SECONDS}s): "
        f"{oss_service.generate_download_url(object_key)}"
    )

    ok = True
    try:
        # 2) Head: object exists and size matches
        head = _retry("head", lambda: bucket.head_object(object_key))
        size_ok = head.content_length == local_size
        logger.info(f"[head] size={head.content_length} expected={local_size} PASS={size_ok}")
        ok = ok and size_ok

        # 3) Pre-signed download URL + content hash comparison
        url = oss_service.generate_download_url(object_key)
        dl_status, dl_bytes = None, b""
        for attempt in range(1, 4):
            try:
                with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                    resp = client.get(url)
                dl_status, dl_bytes = resp.status_code, resp.content
                break
            except httpx.HTTPError as exc:
                logger.warning(f"[download] network error on attempt {attempt}/3: {exc} — retrying")
                time.sleep(1.5)
        dl_ok = dl_status == 200 and _md5_bytes(dl_bytes) == local_md5
        logger.info(
            f"[download] pre-signed GET status={dl_status} "
            f"bytes={len(dl_bytes)} PASS={dl_ok}"
        )
        ok = ok and dl_ok
    finally:
        # 4) Cleanup (delete unless --keep)
        if keep:
            logger.info(f"[cleanup] kept '{object_key}' — public URL: {oss_service.get_object_url(object_key)}")
        else:
            try:
                _retry("delete", lambda: bucket.delete_object(object_key))
                logger.info(f"[cleanup] deleted '{object_key}'")
            except oss2.exceptions.OssError as exc:
                logger.warning(f"[cleanup] could not delete '{object_key}': {exc.status} {exc.message}")

    return ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test local file upload to OSS (.env credentials)")
    parser.add_argument("--file", type=str, default=None, help="Local file to upload (default: auto-generated text file)")
    parser.add_argument("--key", type=str, default=None, help="OSS object key (default: oss-upload-test/<timestamp>)")
    parser.add_argument("--keep", action="store_true", help="Keep the uploaded object instead of deleting it")
    args = parser.parse_args()

    ak = settings.OSS_ACCESS_KEY_ID
    logger.info(
        f"OSS config: endpoint={settings.OSS_ENDPOINT} bucket={settings.OSS_BUCKET_NAME} "
        f"ak={ak[:6]}...{ak[-4:] if len(ak) > 10 else ''}"
    )

    with tempfile.TemporaryDirectory(prefix="oss_test_") as tmp_dir:
        local_path = args.file or _default_local_file(tmp_dir)
        if not os.path.isfile(local_path):
            logger.error(f"File not found: {local_path}")
            sys.exit(2)
        object_key = args.key or f"oss-upload-test/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{os.path.basename(local_path)}"
        passed = test_upload(local_path, object_key, args.keep)

    print()
    logger.info(f"=== Overall: {'PASS' if passed else 'FAIL'} ===")
    sys.exit(0 if passed else 1)
