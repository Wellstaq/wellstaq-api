"""Post gallery/video input contract."""
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.post import PostCreateRequest, PostUpdateRequest
from app.services.post.media import validate_post_media


def test_gallery_limit_and_distinct_images():
    urls = [f"https://assets.test.invalid/posts/org/{i}.jpg" for i in range(15)]
    assert PostCreateRequest(media_urls=urls, media_type="image").media_urls == urls
    validate_post_media(urls, "image")
    with pytest.raises(ValidationError):
        PostCreateRequest(media_urls=urls + ["https://assets.test.invalid/posts/org/16.jpg"], media_type="image")
    with pytest.raises(HTTPException):
        validate_post_media([urls[0], urls[0]], "image")


def test_single_video_and_mismatched_media():
    video = "https://assets.test.invalid/posts/org/clip.mp4"
    validate_post_media([video], "video")
    with pytest.raises(HTTPException):
        validate_post_media([video, video + "?copy=1"], "video")
    with pytest.raises(HTTPException):
        validate_post_media([video], "image")
    with pytest.raises(ValidationError):
        PostUpdateRequest(media_urls=[video], media_type="video")
