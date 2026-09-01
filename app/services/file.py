"""File processing service for downloading, splitting PDFs, and uploading to external APIs."""
import os
import uuid
import fitz  # PyMuPDF
import httpx
import logging
from app.services.oss import oss_service
from app.services.pdf_utils import pdf_processor
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from app.core.config import settings
from app.core.sentry_alerts import ALERTS, capture_integration_alert
from urllib.parse import urlparse, unquote


logger = logging.getLogger(__name__)

_FILE_ALERTS = ALERTS["file"]


def _capture_file_alert(alert_key: str, **format_args) -> None:
    """Capture a design-file download/upload failure to Sentry."""
    capture_integration_alert(_FILE_ALERTS[alert_key], format_args=format_args, component="qpmn_api")


# QPMN sometimes returns stage-host URLs for uploaded files; rewrite those
# prefixes so the persisted URLs point at the serving host.
_QPMN_URL_REWRITES = [
    ("https://uat.popprint.cn/stage/file/file/", "https://uat.popprint.cn/file/file/"),
]


def _rewrite_qpmn_file_url(url: str) -> str:
    """Rewrite known QPMN stage URL prefixes to their serving equivalents."""
    for stage_prefix, serving_prefix in _QPMN_URL_REWRITES:
        if url.startswith(stage_prefix):
            rewritten = serving_prefix + url[len(stage_prefix):]
            logger.info(f"[FileService] Rewrote QPMN file URL {url} -> {rewritten}")
            return rewritten
    return url


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
            _capture_file_alert("DOWNLOAD_FAILED", url=url, error_msg=error_msg)
            return False, error_msg

    def split_pdf(self, pdf_path: str, dest_dir: str) -> List[str]:
        """
        Split a multi-page PDF into single-page PDF files.
        If MODIFY_PDF_RESOLUTION is enabled, resize each page before splitting.
        If PDF_CONVERT_TO_PDFX is enabled, declare each page as PDF/X
        (QPMN rejects uploads that are not a PDF/X standard).

        Args:
            pdf_path: Path to the source PDF file.
            dest_dir: Directory where single-page files will be saved.

        Returns:
            List of paths to the generated single-page PDF files.
        """
        page_files: List[str] = []
        try:
            # Resize PDF if MODIFY_PDF_RESOLUTION is enabled
            if settings.MODIFY_PDF_RESOLUTION:
                logger.info(f"[FileService] Resizing PDF {pdf_path} to {settings.PDF_TARGET_WIDTH}x{settings.PDF_TARGET_HEIGHT} points")
                resized_bytes = pdf_processor.resize_pdf(
                    pdf_path,
                    width=settings.PDF_TARGET_WIDTH,
                    height=settings.PDF_TARGET_HEIGHT,
                    fit=True
                )
                # Save resized PDF to a temp file and use it for splitting
                base_name = os.path.splitext(os.path.basename(pdf_path))[0]
                resized_path = os.path.join(dest_dir, f"{base_name}_resized.pdf")
                with open(resized_path, "wb") as f:
                    f.write(resized_bytes)
                pdf_path = resized_path
                logger.info(f"[FileService] Saved resized PDF: {resized_path}")

            doc = fitz.open(pdf_path)
            base_name = os.path.splitext(os.path.basename(pdf_path))[0]

            logger.info(f"[FileService] Splitting PDF {pdf_path} into {len(doc)} page(s)")

            for page_idx in range(len(doc)):
                single_page_doc = fitz.open()  # new empty PDF
                single_page_doc.insert_pdf(doc, from_page=page_idx, to_page=page_idx)

                if settings.PDF_CONVERT_TO_PDFX and not settings.CONVERT_TO_PNG:
                    # (skipped when CONVERT_TO_PNG is on — the PDF/X
                    # declaration is pointless on pages rendered to PNG)
                    if pdf_processor.apply_pdfx(single_page_doc, settings.PDF_X_STANDARD):
                        logger.info(
                            f"[FileService] Declared page {page_idx + 1} as {settings.PDF_X_STANDARD}"
                        )
                    else:
                        logger.warning(
                            "[FileService] sRGB ICC profile not found, "
                            f"page {page_idx + 1} kept without PDF/X declaration"
                        )

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
            return []

        return page_files

    def pdf_to_png(self, pdf_path: str, dest_dir: str) -> Optional[str]:
        """
        Render a single-page PDF to a PNG file at ``PDF_TO_PNG_DPI``.

        Used when CONVERT_TO_PNG is enabled: the split single-page design
        PDFs are uploaded to QPMN as PNG images instead of PDFs.

        Args:
            pdf_path: Path to a (single-page) PDF file.
            dest_dir: Directory where the PNG will be saved.

        Returns:
            Path to the generated PNG, or None on failure.
        """
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        png_path = os.path.join(dest_dir, f"{base_name}.png")
        try:
            doc = fitz.open(pdf_path)
            try:
                pix = doc[0].get_pixmap(dpi=settings.PDF_TO_PNG_DPI)
                pix.save(png_path)
            finally:
                doc.close()
            logger.info(
                f"[FileService] Converted {os.path.basename(pdf_path)} -> {png_path} "
                f"({settings.PDF_TO_PNG_DPI} dpi, {os.path.getsize(png_path)} bytes)"
            )
            return png_path
        except Exception as exc:
            logger.error(f"[FileService] Failed to convert {pdf_path} to PNG: {exc}", exc_info=True)
            return None

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
                if file_url:
                    file_url = _rewrite_qpmn_file_url(file_url)
                logger.info(f"[FileService] Uploaded {filename} successfully. URL: {file_url}")
            else:
                logger.error(f"[FileService] Uploaded {filename} failed. URL: {result}")
                _capture_file_alert("UPLOAD_EMPTY_RESPONSE", filename=filename, status_code=resp.status_code, result=result)
                return None
            return {
                "filename": filename,
                "url": file_url,
                "source": "qpmn_api",
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "response": data,
            }

        except Exception as exc:
            logger.error(f"[FileService] Failed to upload {file_path} to QPMN: {exc}", exc_info=True)
            _capture_file_alert("UPLOAD_FAILED", file_path=file_path, exc=exc)
            return None

# Create singleton instance
file_service = FileService()