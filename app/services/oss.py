"""Alibaba Cloud OSS service for file upload/download."""
from venv import logger
import oss2
from typing import Optional
from datetime import timedelta
from app.core.config import settings


class OSSService:
    """Service for interacting with Alibaba Cloud OSS."""
    
    def __init__(self):
        """Initialize OSS client."""
        # Create Auth instance
        self.auth = oss2.Auth(
            settings.OSS_ACCESS_KEY_ID,
            settings.OSS_ACCESS_KEY_SECRET
        )
        
        # Create Bucket instance
        self.bucket = oss2.Bucket(
            self.auth,
            f"https://{settings.OSS_ENDPOINT}",
            settings.OSS_BUCKET_NAME
        )
    
    def generate_upload_url(self, object_key: str, mime_type: str) -> str:
        """
        Generate pre-signed URL for uploading a file to OSS.
        
        Args:
            object_key: The object key (path) in OSS bucket
            mime_type: MIME type of the file
            
        Returns:
            Pre-signed upload URL
        """
        # Generate pre-signed URL for PUT operation
        url = self.bucket.sign_url(
            'PUT',
            object_key,
            settings.OSS_UPLOAD_EXPIRY_SECONDS,
            headers={'Content-Type': mime_type}
        )
        return url
    
    def generate_download_url(self, object_key: str) -> str:
        """
        Generate pre-signed URL for downloading a file from OSS.
        
        Args:
            object_key: The object key (path) in OSS bucket
            
        Returns:
            Pre-signed download URL
        """
        # Generate pre-signed URL for GET operation
        url = self.bucket.sign_url(
            'GET',
            object_key,
            settings.OSS_DOWNLOAD_EXPIRY_SECONDS
        )
        return url
    
    def get_object_url(self, object_key: str) -> str:
        """
        Get the public URL for an object (if bucket is public).
        
        Args:
            object_key: The object key (path) in OSS bucket
            
        Returns:
            Public URL for the object
        """
        return f"https://{settings.OSS_BUCKET_NAME}.{settings.OSS_ENDPOINT}/{object_key}"

    def get_object_to_file(self, object_key:str, local_file_path:str) -> bool:
        try:
            self.bucket.get_object_to_file(object_key, local_file_path)
            return local_file_path
        except Exception as e:
            logger.warning(f"[OSS] Failed to download file {object_key}")
            return False
# Create singleton instance
oss_service = OSSService()
