from pydantic import BaseModel


class UploadRequestSchema(BaseModel):
    domain: str  # 'posts' | 'stories' | 'chat' | 'avatars'
    content_type: str


class UploadResponse(BaseModel):
    upload_url: str
    object_key: str
    media_url: str
    content_type: str