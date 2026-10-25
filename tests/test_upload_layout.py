"""Where uploaded files end up on disk, with and without PER_CAMERA_DIRS."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import server

DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
REFRESH_GRANT = "refresh_token"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "UPLOAD_DIR", tmp_path)
    for state in (server.device_codes, server.tokens, server.assets):
        state.clear()
    server.upload_log.clear()
    # No context manager: skips the lifespan, which would start the dashboard.
    return TestClient(server.app)


def pair(client, client_id):
    """Run the device-code flow and return the access token."""
    code = client.post("/v2/auth/device/code", data={"client_id": client_id}).json()
    token = client.post(
        "/v2/auth/token",
        data={"grant_type": DEVICE_CODE_GRANT, "device_code": code["device_code"]},
    ).json()
    return token["access_token"]


def upload(client, access_token, name="DSCF0001.JPG", body=b"jpeg-bytes"):
    headers = {"Authorization": f"Bearer {access_token}"}
    asset = client.post(
        "/v2/devices/assets",
        json={"name": name, "filesize": len(body)},
        headers=headers,
    ).json()
    resp = client.put(f"/upload/{asset['id']}?part=1", content=body)
    assert resp.status_code == 200


def today():
    return datetime.now().strftime("%Y-%m-%d")


def test_default_layout_is_by_date_only(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PER_CAMERA_DIRS", False)

    upload(client, pair(client, "4BL00195"))

    assert (tmp_path / today() / "DSCF0001.JPG").read_bytes() == b"jpeg-bytes"


def test_per_camera_dirs_separate_cameras(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PER_CAMERA_DIRS", True)

    upload(client, pair(client, "cam-a"), name="A.JPG")
    upload(client, pair(client, "cam-b"), name="B.JPG")

    assert (tmp_path / "cam-a" / today() / "A.JPG").is_file()
    assert (tmp_path / "cam-b" / today() / "B.JPG").is_file()
    assert server.upload_log[0]["directory"] == f"cam-b/{today()}"


def test_per_camera_dirs_after_token_refresh(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PER_CAMERA_DIRS", True)

    refreshed = client.post(
        "/v2/auth/token",
        data={"grant_type": REFRESH_GRANT, "refresh_token": "r", "client_id": "cam-a"},
    ).json()
    upload(client, refreshed["access_token"])

    assert (tmp_path / "cam-a" / today() / "DSCF0001.JPG").is_file()


def test_per_camera_dirs_unknown_token_goes_to_unknown(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PER_CAMERA_DIRS", True)

    upload(client, "not-a-real-token")

    assert (tmp_path / "unknown" / today() / "DSCF0001.JPG").is_file()


@pytest.mark.parametrize(
    "client_id, expected",
    [
        ("../../etc", "etc"),
        ("a/b\\c", "abc"),
        ("..", "unknown"),
        ("", "unknown"),
        ("камера", "unknown"),
        ("x" * 100, "x" * 64),
    ],
)
def test_client_id_is_sanitised_to_a_single_path_component(
    client, tmp_path, monkeypatch, client_id, expected
):
    monkeypatch.setattr(server, "PER_CAMERA_DIRS", True)

    upload(client, pair(client, client_id))

    assert (tmp_path / expected / today() / "DSCF0001.JPG").is_file()
    assert [p.name for p in tmp_path.iterdir() if p.name != ".parts"] == [expected]
