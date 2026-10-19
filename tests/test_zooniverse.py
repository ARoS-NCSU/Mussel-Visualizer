"""Unit tests for zooniverse.py. ``requests`` is mocked: no network access."""
from __future__ import annotations

import os
import sys
import urllib.parse
from unittest import mock

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import zooniverse  # noqa: E402

TOKEN = "secret-token-123"
POSE = {
    "yaw": 12.5,
    "pitch": -3.0,
    "roll": 40.0,
    "quaternion": {"w": 0.93, "x": 0.34, "y": -0.07, "z": 0.10},
    "height": -0.25,
    "token": "999",
}


def fake_response(status=200, json_body=None, text=""):
    response = mock.Mock()
    response.status_code = status
    response.ok = 200 <= status < 300
    response.json.return_value = json_body if json_body is not None else {}
    response.text = text
    return response


@pytest.fixture
def http():
    with mock.patch.object(zooniverse.requests, "request") as request:
        request.return_value = fake_response()
        yield request


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------
def test_login_url_encodes_all_parameters():
    url = zooniverse.login_url("my id", "https://example.org/app/?x=1")
    parsed = urllib.parse.urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == f"{zooniverse.PANOPTES_URL}/oauth/authorize"
    assert " " not in url
    assert urllib.parse.parse_qs(parsed.query) == {
        "response_type": ["code"],
        "client_id": ["my id"],
        "redirect_uri": ["https://example.org/app/?x=1"],
        "scope": ["public user subject classification"],
    }


def test_exchange_code_posts_form(http):
    http.return_value = fake_response(json_body={"access_token": TOKEN, "expires_in": 7200})
    token = zooniverse.exchange_code("abc", "cid", "csecret", "http://localhost:8501")
    assert token["access_token"] == TOKEN
    method, url = http.call_args.args
    assert (method, url) == ("POST", f"{zooniverse.PANOPTES_URL}/oauth/token")
    assert http.call_args.kwargs["data"]["grant_type"] == "authorization_code"
    assert http.call_args.kwargs["timeout"] == zooniverse.TIMEOUT


def test_get_me_returns_first_user_or_none(http):
    http.return_value = fake_response(json_body={"users": [{"login": "vol"}]})
    assert zooniverse.get_me(TOKEN) == {"login": "vol"}
    assert http.call_args.kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"
    http.return_value = fake_response(json_body={"users": []})
    assert zooniverse.get_me(TOKEN) is None


# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------
def test_get_next_subject_skips_excluded_ids(http):
    http.return_value = fake_response(json_body={"subjects": [{"id": "1"}, {"id": "2"}, {"id": "3"}]})
    assert zooniverse.get_next_subject(31559, exclude_ids=["1", 2]) == {"id": "3"}
    assert http.call_args.kwargs["params"] == {"workflow_id": 31559, "page_size": 10}
    assert "Authorization" not in http.call_args.kwargs["headers"]


def test_get_next_subject_returns_none_when_all_seen_or_empty(http):
    http.return_value = fake_response(json_body={"subjects": [{"id": "1"}]})
    assert zooniverse.get_next_subject(31559, exclude_ids=["1"]) is None
    http.return_value = fake_response(json_body={"subjects": []})
    assert zooniverse.get_next_subject(31559) is None


def test_subject_image_url_picks_first_image():
    subject = {
        "locations": [
            {"application/json": "https://x/meta.json"},
            {"image/jpeg": "https://x/a.jpeg"},
            {"image/png": "https://x/b.png"},
        ]
    }
    assert zooniverse.subject_image_url(subject) == "https://x/a.jpeg"
    assert zooniverse.subject_image_url({"locations": [{"video/mp4": "https://x/v.mp4"}]}) is None
    assert zooniverse.subject_image_url({}) is None


def test_talk_url():
    assert (
        zooniverse.talk_url("coopsciscoop/bionic-mussels", 117025476)
        == "https://www.zooniverse.org/projects/coopsciscoop/bionic-mussels/talk/subjects/117025476"
    )


# ---------------------------------------------------------------------------
# Classifications
# ---------------------------------------------------------------------------
CLASSIFICATION_ARGS = dict(
    project_id=31635,
    workflow_id=31559,
    workflow_version="2.5",
    subject_id=117025476,
    pose=POSE,
    started_at="2026-10-19T12:00:00+00:00",
    finished_at="2026-10-19T12:01:00+00:00",
    subject_dimensions=[{"naturalWidth": 1920, "naturalHeight": 1080, "clientWidth": None, "clientHeight": None}],
    camera={"elevation_deg": 25.0, "azimuth_deg": 45.0, "distance_scale": 1.0},
)


def test_create_classification_payload(http):
    http.return_value = fake_response(201, json_body={"classifications": [{"id": "42"}]})
    result = zooniverse.create_classification(**CLASSIFICATION_ARGS)
    assert result == {"id": "42"}
    method, url = http.call_args.args
    assert (method, url) == ("POST", f"{zooniverse.PANOPTES_URL}/api/classifications")
    assert http.call_args.kwargs["json"] == {
        "classifications": {
            "annotations": [{"task": "T0", "value": 0}],
            "metadata": {
                "workflow_version": "2.5",
                "started_at": "2026-10-19T12:00:00+00:00",
                "finished_at": "2026-10-19T12:01:00+00:00",
                "user_language": "en",
                "utc_offset": "0",
                "user_agent": "Mussel-Visualizer/1.0 (Streamlit)",
                "source": "mussel-visualizer",
                "subject_dimensions": [
                    {"naturalWidth": 1920, "naturalHeight": 1080, "clientWidth": None, "clientHeight": None}
                ],
                "pose": {
                    "quaternion": {"w": 0.93, "x": 0.34, "y": -0.07, "z": 0.10},
                    "yaw": 12.5,
                    "pitch": -3.0,
                    "roll": 40.0,
                    "height": -0.25,
                },
                "pose_convention": "intrinsic ZYX Euler (deg); quaternion is authoritative; height in [-1, 0]",
                "camera": {"elevation_deg": 25.0, "azimuth_deg": 45.0, "distance_scale": 1.0},
            },
            "links": {"project": "31635", "workflow": "31559", "subjects": ["117025476"]},
            "completed": True,
        }
    }


def test_create_classification_auth_header_only_with_token(http):
    http.return_value = fake_response(201, json_body={"classifications": [{"id": "42"}]})
    zooniverse.create_classification(**CLASSIFICATION_ARGS)
    assert "Authorization" not in http.call_args.kwargs["headers"]
    zooniverse.create_classification(**CLASSIFICATION_ARGS, token=TOKEN)
    assert http.call_args.kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
def test_non_2xx_raises_without_leaking_token(http):
    http.return_value = fake_response(401, text="Unauthorized " + "x" * 2000)
    with pytest.raises(zooniverse.ZooniverseError) as excinfo:
        zooniverse.create_classification(**CLASSIFICATION_ARGS, token=TOKEN)
    assert excinfo.value.status_code == 401
    message = str(excinfo.value)
    assert "HTTP 401" in message and "Unauthorized" in message
    assert TOKEN not in message
    assert len(message) < zooniverse.ERROR_BODY_CHARS + 100


def test_oauth_failure_does_not_leak_secret(http):
    http.return_value = fake_response(400, text='{"error":"invalid_grant"}')
    with pytest.raises(zooniverse.ZooniverseError) as excinfo:
        zooniverse.exchange_code("abc", "cid", "csecret", "http://localhost:8501")
    assert "csecret" not in str(excinfo.value) and "abc" not in str(excinfo.value)


def test_network_error_raises_zooniverse_error(http):
    http.side_effect = requests.ConnectionError(f"failed with Bearer {TOKEN}")
    with pytest.raises(zooniverse.ZooniverseError) as excinfo:
        zooniverse.get_me(TOKEN)
    assert excinfo.value.status_code is None
    assert TOKEN not in str(excinfo.value)
