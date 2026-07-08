from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form, status, Depends
from fastapi.responses import Response
from sqlmodel import Session, select
import uuid
from datetime import datetime,timezone, timedelta

from app.core.database import get_session
from app.models.file_upload import FileUpload
from app.schemas.file_upload import FileUploadUrlsResponse
from app.core.auth_oneflow import verify_oneflow_auth, get_client_store_id
from app.services.oss import oss_service
from app.services.pdf_utils import pdf_processor, parse_dimension, PAGE_SIZES

router = APIRouter(
    prefix="/api/file",
    tags=["File Upload"],
    dependencies=[Depends(verify_oneflow_auth)],
)


@router.get("/getpreupload", response_model=FileUploadUrlsResponse)
def get_file_upload_urls(
    mime_type: str = Query(..., description="The MIME type of the file to be uploaded (e.g., application/pdf)"),
    session: Session = Depends(get_session),
    store_id: str = Depends(get_client_store_id),
):
    """
    Get File Upload URLs - Retrieves pre-signed URLs for uploading a local file to Alibaba Cloud OSS.

    Returns two URLs:
    - upload: Pre-signed URL to upload the file to OSS (expires in 1 hour)
    - fetch: Pre-signed URL to download the file from OSS (expires in 24 hours)

    The file will be stored in the OSS bucket with a unique key based on store_id and file_id.
    """
    try:
        # Generate unique file ID
        file_id = str(uuid.uuid4())

        # Create object key with store_id prefix for organization
        # Format: {store_id}/{file_id}.{extension}
        # Extract extension from mime_type if possible
        extension_map = {
            'application/pdf': 'pdf',
            'image/jpeg': 'jpg',
            'image/png': 'png',
            'image/gif': 'gif',
            'text/plain': 'txt',
        }
        extension = extension_map.get(mime_type, 'bin')
        object_key = f"{store_id}/{file_id}.{extension}" if store_id else f"uploads/{file_id}.{extension}"

        # Generate OSS pre-signed URLs
        upload_url = oss_service.generate_upload_url(object_key, mime_type)
        fetch_url = oss_service.generate_download_url(object_key)

        # Create file upload record
        file_upload = FileUpload(
            file_id=file_id,
            mime_type=mime_type,
            upload_url=upload_url,
            fetch_url=fetch_url,
            status="pending",
            store_id=store_id
        )

        session.add(file_upload)
        session.commit()

        return FileUploadUrlsResponse(
            upload=upload_url,
            fetch=fetch_url
        )

    except Exception as e:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate upload URLs: {str(e)}"
        )
