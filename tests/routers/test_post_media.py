"""Post gallery and video API contract."""
import importlib

import pytest

from tests.conftest import authenticate_user, make_org_with_owner


@pytest.mark.asyncio
async def test_post_gallery_limit_and_video(client, db_session, monkeypatch):
    org, user = await make_org_with_owner(db_session)
    headers, _ = await authenticate_user(db_session, user, device_fingerprint=f"post-media-{user.id}")
    service = importlib.import_module("app.services.post.create_post")
    monkeypatch.setattr(service, "confirm_upload", lambda **_: True)
    images = [f"https://assets.test.invalid/posts/{org.id}/{i}.jpg" for i in range(15)]
    endpoint = f"/organizations/{org.id}/posts"

    gallery = await client.post(endpoint, headers=headers, json={"media_type": "image", "media_urls": images})
    assert gallery.status_code == 201, gallery.text
    assert gallery.json()["media_urls"] == images
    assert gallery.json()["media_url"] == images[0]
    fetched = await client.get(f"{endpoint}/{gallery.json()['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["media_urls"] == images

    oversized = await client.post(endpoint, headers=headers, json={"media_type": "image", "media_urls": images + [images[-1] + "2"]})
    assert oversized.status_code == 422

    video_url = f"https://assets.test.invalid/posts/{org.id}/clip.mp4"
    video = await client.post(endpoint, headers=headers, json={"media_type": "video", "media_url": video_url})
    assert video.status_code == 201, video.text
    assert video.json()["media_urls"] == [video_url]
