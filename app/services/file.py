"""File processing service for downloading, splitting PDFs, and uploading to external APIs."""
import os
import uuid
import fitz  # PyMuPDF
import httpx
import logging
from app.services.oss import oss_service
from typing import Dict, Any, List
from datetime import datetime, timezone
from app.core.config import settings
from urllib.parse import urlparse, unquote


logger = logging.getLogger(__name__)


class FileService:
    """Service for handling file operations including downloading, PDF splitting, and uploading."""

    def __init__(self):
        """Initialize FileService."""
        pass

    def download_file(self, url: str, dest_dir: str) -> tuple[bool, str]:
        """
        Download a file from *url* into *dest_dir*.

        Returns:
            Tuple of (success: bool, result: str).
            On success, result is the local file path.
            On failure, result is the error message.
        """
        try:
            # Top priority fetch OSS file via SDK
            parsed_url = urlparse(url)
            url_domain = parsed_url.netloc
            path = unquote(parsed_url.path)
            object_key = path.lstrip('/')
            filename = os.path.basename(path)
            local_path = os.path.join(dest_dir, filename)
            if settings.OSS_BUCKET_NAME in url_domain and settings.OSS_ENDPOINT in url_domain:
                file = oss_service.get_object_to_file(object_key, local_path)
                if file:
                    return True, local_path
            
            # Secondary fetch by public URL
            logger.info(f"[FileService] Downloading {url} -> {local_path}")
            with httpx.Client(timeout=120.0, follow_redirects=True) as client:
                with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with open(local_path, "wb") as fh:
                        for chunk in resp.iter_bytes(chunk_size=8192):
                            fh.write(chunk)

            logger.info(f"[FileService] Downloaded file: {local_path} ({os.path.getsize(local_path)} bytes)")
            return True, local_path

        except Exception as exc:
            error_msg = f"Unexpected error: {str(exc)}"
            logger.error(f"[FileService] Failed to download {url}: {error_msg}", exc_info=True)
            return False, error_msg

    def split_pdf(self, pdf_path: str, dest_dir: str) -> List[str]:
        """
        Split a multi-page PDF into single-page PDF files.

        Args:
            pdf_path: Path to the source PDF file.
            dest_dir: Directory where single-page files will be saved.

        Returns:
            List of paths to the generated single-page PDF files.
        """
        page_files: List[str] = []
        try:
            doc = fitz.open(pdf_path)
            base_name = os.path.splitext(os.path.basename(pdf_path))[0]

            logger.info(f"[FileService] Splitting PDF {pdf_path} into {len(doc)} page(s)")

            for page_idx in range(len(doc)):
                single_page_doc = fitz.open()  # new empty PDF
                single_page_doc.insert_pdf(doc, from_page=page_idx, to_page=page_idx)

                page_filename = f"{base_name}_page_{page_idx + 1}.pdf"
                page_path = os.path.join(dest_dir, page_filename)
                single_page_doc.save(page_path)
                single_page_doc.close()

                page_files.append(page_path)
                logger.info(f"[FileService] Created single-page PDF: {page_path}")

            doc.close()

        except Exception as exc:
            logger.error(f"[FileService] Failed to split PDF {pdf_path}: {exc}", exc_info=True)
            # Fall back to returning the original file if splitting fails
            return [pdf_path]

        return page_files

    def upload_to_qpmn(self, file_path: str, store_key: str) -> Dict[str, Any] | None:
        """
        Upload a file to the QPMN API file endpoint.

        Returns:
            Dict with file info (url, filename, etc.) on success, None on failure.
        """
        try:
            filename = os.path.basename(file_path)
            api_url = f"{settings.QPMN_API_URL}/fileLibrary/images"

            logger.info(f"[FileService] Uploading {filename} to QPMN API: {api_url}")

            headers = {}
            headers["Authorization"] = f"Basic {store_key}"

            with open(file_path, "rb") as fh:
                files = {"file": (filename, fh)}
                with httpx.Client(timeout=120.0) as client:
                    resp = client.post(api_url, headers=headers, files=files)
                    resp.raise_for_status()

            result = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            data = result.get("data",[])
            if len(data):
                file_url = data[0].get("url", None)

            logger.info(f"[FileService] Uploaded {filename} successfully. URL: {file_url}")

            return {
                "filename": filename,
                "url": file_url,
                "source": "qpmn_api",
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "response": data,
            }

        except Exception as exc:
            logger.error(f"[FileService] Failed to upload {file_path} to QPMN: {exc}", exc_info=True)
            return None

# Create singleton instance
file_service = FileService()