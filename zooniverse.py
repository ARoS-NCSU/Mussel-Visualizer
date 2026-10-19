"""Zooniverse (Panoptes) HTTP client for the Bionic Mussels workflow.

Plain ``requests`` calls, no Streamlit: every function takes its inputs
explicitly so it can be unit-tested without a network or a running app.
The volunteer OAuth flow follows the Solar Jet Hunter app
(github.com/somusset/SJH_IFE_time-distance).
"""
from __future__ import annotations

import urllib.parse
from typing import Iterable

import requests

PANOPTES_URL = "https://panoptes.zooniverse.org"
ZOONIVERSE_URL = "https://www.zooniverse.org"
API_HEADERS = {
    "Accept": "application/vnd.api+json; version=1",
    "Content-Type": "application/json",
}
OAUTH_SCOPE = "public user subject classification"
TIMEOUT = 15  # seconds, on every request

TASK_KEY = "T0"
# T0 is a single-choice question; its only answer is "Pose matched". The
# workflow export turns the value into that answer's label, so it must be the
# answer index: the pose itself goes in the classification metadata.
POSE_MATCHED_ANSWER = 0
USER_AGENT = "Mussel-Visualizer/1.0 (Streamlit)"
SOURCE = "mussel-visualizer"
POSE_CONVENTION = "intrinsic ZYX Euler (deg); quaternion is authoritative; height in [-1, 0]"
ERROR_BODY_CHARS = 500


class ZooniverseError(Exception):
    """A failed Panoptes request. ``status_code`` is None for network errors."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _headers(token: str | None = None) -> dict:
    headers = dict(API_HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _request(method: str, path: str, *, what: str, **kwargs) -> dict:
    """Send a request and return its JSON, raising ZooniverseError on failure.

    Messages carry the response body (truncated) but never the request
    headers or form data, so tokens and secrets cannot leak into them.
    """
    try:
        response = requests.request(method, f"{PANOPTES_URL}{path}", timeout=TIMEOUT, **kwargs)
    except requests.RequestException as err:
        raise ZooniverseError(f"Could not {what}: {type(err).__name__}") from None
    if not response.ok:
        body = (response.text or "").strip()[:ERROR_BODY_CHARS]
        raise ZooniverseError(
            f"Could not {what} (HTTP {response.status_code}): {body}", response.status_code
        )
    try:
        return response.json()
    except ValueError:
        raise ZooniverseError(f"Could not {what}: the response was not JSON", response.status_code) from None


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------
def login_url(client_id: str, redirect_uri: str) -> str:
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": OAUTH_SCOPE,
    })
    return f"{PANOPTES_URL}/oauth/authorize?{params}"


def exchange_code(code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    """Trade an authorization code for a token (``access_token``, ``expires_in``, …)."""
    return _request(
        "POST",
        "/oauth/token",
        what="sign in with Zooniverse",
        headers={"Accept": "application/json"},
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        },
    )


def get_me(token: str) -> dict | None:
    users = _request("GET", "/api/me", what="get the signed-in user", headers=_headers(token)).get("users", [])
    return users[0] if users else None


# ---------------------------------------------------------------------------
# Workflow and subjects
# ---------------------------------------------------------------------------
def get_workflow(workflow_id, token: str | None = None) -> dict:
    workflows = _request(
        "GET", f"/api/workflows/{workflow_id}", what="get the workflow", headers=_headers(token)
    ).get("workflows", [])
    if not workflows:
        raise ZooniverseError(f"Workflow {workflow_id} was not found", 404)
    return workflows[0]


def get_next_subject(workflow_id, token: str | None = None, exclude_ids: Iterable = ()) -> dict | None:
    """The next queued subject not in ``exclude_ids``, or None.

    Signed in, Panoptes queues subjects the volunteer hasn't classified;
    as a guest it returns random ones, so ``exclude_ids`` avoids repeats.
    """
    excluded = {str(i) for i in exclude_ids}
    subjects = _request(
        "GET",
        "/api/subjects/queued",
        what="get the next image",
        headers=_headers(token),
        params={"workflow_id": workflow_id, "page_size": 10},
    ).get("subjects", [])
    return next((s for s in subjects if str(s.get("id")) not in excluded), None)


def subject_image_url(subject: dict) -> str | None:
    """The URL of the subject's first image location."""
    for location in subject.get("locations", []):
        for mime, url in location.items():
            if mime.startswith("image/"):
                return url
    return None


def talk_url(project_slug: str, subject_id) -> str:
    return f"{ZOONIVERSE_URL}/projects/{project_slug}/talk/subjects/{subject_id}"


# ---------------------------------------------------------------------------
# Classifications
# ---------------------------------------------------------------------------
def classification_payload(
    *,
    project_id,
    workflow_id,
    workflow_version: str,
    subject_id,
    pose: dict,
    started_at: str,
    finished_at: str,
    subject_dimensions: list,
    camera: dict | None = None,
) -> dict:
    pose_value = {
        "quaternion": pose["quaternion"],
        "yaw": pose["yaw"],
        "pitch": pose["pitch"],
        "roll": pose["roll"],
        "height": pose["height"],
    }
    return {
        "classifications": {
            "annotations": [{"task": TASK_KEY, "value": POSE_MATCHED_ANSWER}],
            "metadata": {
                "workflow_version": str(workflow_version),
                "started_at": started_at,
                "finished_at": finished_at,
                "user_language": "en",
                "utc_offset": "0",
                "user_agent": USER_AGENT,
                "source": SOURCE,
                "subject_dimensions": subject_dimensions,
                "pose": pose_value,
                "pose_convention": POSE_CONVENTION,
                "camera": camera or {},
            },
            "links": {
                "project": str(project_id),
                "workflow": str(workflow_id),
                "subjects": [str(subject_id)],
            },
            "completed": True,
        }
    }


def create_classification(
    *,
    project_id,
    workflow_id,
    workflow_version: str,
    subject_id,
    pose: dict,
    started_at: str,
    finished_at: str,
    subject_dimensions: list,
    camera: dict | None = None,
    token: str | None = None,
) -> dict:
    """POST the pose as a classification; returns the created classification."""
    payload = classification_payload(
        project_id=project_id,
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        subject_id=subject_id,
        pose=pose,
        started_at=started_at,
        finished_at=finished_at,
        subject_dimensions=subject_dimensions,
        camera=camera,
    )
    response = _request(
        "POST", "/api/classifications", what="submit the classification", headers=_headers(token), json=payload
    )
    classifications = response.get("classifications", [])
    return classifications[0] if classifications else response
