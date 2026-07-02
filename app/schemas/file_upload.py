from pydantic import BaseModel, Field


class FileUploadUrlsResponse(BaseModel):
    """Schema for file upload URLs response."""
    upload: str = Field(..., description="URL to upload the file to (e.g., Amazon S3)")
    fetch: str = Field(..., description="URL to be used in the order JSON for file retrieval")
