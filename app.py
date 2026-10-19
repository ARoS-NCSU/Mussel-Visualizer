"""Streamlit entrypoint: camera image next to the interactive mussel viewer.

The image is shown on the left so the 3D view on the right can be posed to
match it. All rendering and interaction (drag-to-rotate, orientation and
burial sliders) happens inside the three.js component in the browser.

Two modes (``mode`` in render_config.json):

- **Zooniverse** (default, when the ``[zooniverse]`` secrets exist): the image
  is the next subject of the Bionic Mussels workflow, and the pose is
  submitted as a classification. Volunteers may sign in with Zooniverse.
- **Local**: the image is ``image_file`` and nothing is submitted.
"""
from __future__ import annotations

import base64
import datetime
import io
import json
import os

import requests
import streamlit as st
from PIL import Image

import zooniverse
from mussel_viewer import assets_exist, mussel_viewer

_HERE = os.path.dirname(os.path.abspath(__file__))
RENDER_CONFIG_PATH = os.path.join(_HERE, "render_config.json")

DEFAULT_POSE = {"yaw": 0.0, "pitch": 0.0, "roll": 0.0, "height": 0.0}
# Longest side of the image sent to the browser; camera frames are much larger.
DISPLAY_MAX_SIDE = 1920
# Longest side of the substrate texture; WebGL handles up to 4096 everywhere.
SUBSTRATE_MAX_SIDE = 2048

DEFAULT_PROJECT_SLUG = "coopsciscoop/bionic-mussels"
# Used when the workflow's task has no question or help text.
DEFAULT_QUESTION = "Rotate and sink the 3D mussel so it matches the mussel in the photo."
DEFAULT_HELP = (
    "Drag the 3D mussel to rotate it, or use the yaw, pitch and roll sliders for fine "
    "adjustments. Use the Height slider to set how deeply the mussel is buried. When the "
    "model matches the photo, press Submit."
)

st.set_page_config(page_title="Mussel 3D Visualizer", layout="wide")


@st.cache_resource(show_spinner="Converting the mussel model (first run only)…")
def ensure_assets() -> None:
    # The converted assets are committed, so deployments never convert. Only
    # import the loader (and usd-core) when they are missing: conversion
    # peaks at ~4 GB of RAM, more than Streamlit Community Cloud allows.
    if not assets_exist():
        import mussel_loader

        mussel_loader.convert()


def load_render_config() -> dict:
    with open(RENDER_CONFIG_PATH, "r") as f:
        return json.load(f)


def read_secrets(section: str) -> dict | None:
    """A ``st.secrets`` section, or None when it (or secrets.toml) is missing."""
    try:
        value = st.secrets.get(section)
    except Exception:  # no secrets.toml at all
        return None
    return dict(value) if value else None


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@st.cache_data(max_entries=8, show_spinner=False)
def load_image(path: str, mtime: float) -> Image.Image:
    # mtime is part of the cache key so an edited file on disk is reloaded.
    image = Image.open(path)
    image.load()
    image.thumbnail((DISPLAY_MAX_SIDE, DISPLAY_MAX_SIDE))
    return image


@st.cache_data(max_entries=32, ttl=3600, show_spinner=False)
def fetch_subject_image(url: str) -> tuple[Image.Image, tuple[int, int]]:
    """The subject image, downscaled for display, and its original size."""
    response = requests.get(url, timeout=zooniverse.TIMEOUT)
    response.raise_for_status()
    image = Image.open(io.BytesIO(response.content))
    image.load()
    natural_size = image.size
    image.thumbnail((DISPLAY_MAX_SIDE, DISPLAY_MAX_SIDE))
    return image, natural_size


@st.cache_data(max_entries=8, show_spinner=False)
def load_substrate_url(path: str, mtime: float) -> str:
    """The substrate image, downscaled, as a JPEG data URL for the viewer."""
    image = Image.open(path)
    image.thumbnail((SUBSTRATE_MAX_SIDE, SUBSTRATE_MAX_SIDE))
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def camera_for_subject(subject: dict | None, config: dict) -> dict:
    """The camera to pose against. Design hook: later this may read per-subject
    camera parameters from the subject's metadata."""
    return dict(config.get("camera", {}))


def viewer_config(config: dict, camera: dict) -> dict:
    """The render config for the viewer, with the substrate image resolved."""
    config = {**config, "camera": camera}
    if config.get("substrate_mode", "color") != "image":
        return config
    substrate_file = config.get("substrate_image")
    path = os.path.join(_HERE, substrate_file) if substrate_file else None
    if path and os.path.exists(path):
        config["substrate_image_url"] = load_substrate_url(path, os.path.getmtime(path))
    else:
        st.warning(
            f"`substrate_image` ({substrate_file or 'not set'}) was not found; "
            "using `substrate_color` instead.",
            title="Substrate image not found",
        )
    return config


ensure_assets()
render_config = load_render_config()

zoo_secrets = read_secrets("zooniverse")
zoo_mode = render_config.get("mode", "zooniverse") == "zooniverse" and zoo_secrets is not None


# ===========================================================================
# Local mode: the image from render_config.json, nothing is submitted.
# ===========================================================================
if not zoo_mode:
    st.title("Mussel 3D Visualizer")

    image_file = render_config.get("image_file")
    image_path = os.path.join(_HERE, image_file) if image_file else None

    image_col, viewer_col = st.columns(2)

    image = None
    with image_col:
        if image_path and os.path.exists(image_path):
            image = load_image(image_path, os.path.getmtime(image_path))
            st.image(image, caption=image_file, width="stretch", alt="Underwater camera image")
        elif image_file:
            st.warning(f"`{image_file}` was not found next to app.py.", title="Image not found")
        else:
            st.info("Set `image_file` in render_config.json to show a camera image.")

    with viewer_col:
        mussel_viewer(
            viewer_config(render_config, camera_for_subject(None, render_config)),
            initial_pose=DEFAULT_POSE,
            height=render_config.get("viewer_height", 640),
            aspect=image.width / image.height if image else None,
            key="mussel_viewer",
        )
    st.stop()


# ===========================================================================
# Zooniverse mode
# ===========================================================================
project_id = zoo_secrets["project_id"]
workflow_id = zoo_secrets["workflow_id"]
project_slug = zoo_secrets.get("project_slug", DEFAULT_PROJECT_SLUG)
oauth = read_secrets("zooniverse_oauth")

state = st.session_state
for key, default in {
    "zoo_token": None,
    "zoo_user": None,
    "subject": None,
    "seen_ids": [],
    "started_at": None,
    "workflow": None,
    "workflow_version": None,
    "last_result": None,  # {"toast": str, "talk_url": str | None, "talk_subject": str}
    "auth_message": None,
}.items():
    state.setdefault(key, default)


def sign_out() -> None:
    state.zoo_token = None
    state.zoo_user = None


def handle_unauthorized(err: zooniverse.ZooniverseError) -> bool:
    """On a 401 with a token, the token has expired: sign out and say so."""
    if err.status_code == 401 and state.zoo_token:
        sign_out()
        state.auth_message = "Your Zooniverse sign-in has expired. Please log in again."
        return True
    return False


def next_subject() -> None:
    """Forget the current subject so the next run fetches a new one."""
    state.subject = None


# --- OAuth callback: Zooniverse redirects back with ?code=… -----------------
login_error = None
if "code" in st.query_params:
    code = st.query_params["code"]
    # Clear first, so a refresh never re-uses the (single-use) code.
    st.query_params.clear()
    if oauth and not state.zoo_token:
        try:
            token_data = zooniverse.exchange_code(
                code, oauth["client_id"], oauth["client_secret"], oauth["redirect_uri"]
            )
            state.zoo_token = token_data["access_token"]
            user = zooniverse.get_me(state.zoo_token) or {}
            state.zoo_user = user.get("display_name") or user.get("login") or "volunteer"
            state.auth_message = None
            # Signed in, Panoptes serves subjects this volunteer hasn't classified.
            next_subject()
        except zooniverse.ZooniverseError as err:
            sign_out()
            login_error = str(err)
        except KeyError:
            sign_out()
            login_error = "Zooniverse did not return an access token."

# --- Header -----------------------------------------------------------------
title_col, account_col = st.columns([3, 1], vertical_alignment="center")
with title_col:
    st.title("Mussel 3D Visualizer")
with account_col:
    if state.zoo_token:
        with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center"):
            st.markdown(f"Signed in as **{state.zoo_user}**")
            if st.button("Sign out", icon=":material/logout:"):
                sign_out()
                st.rerun()
    elif oauth:
        login_link = zooniverse.login_url(oauth["client_id"], oauth["redirect_uri"])
        st.link_button("Log in with Zooniverse", login_link, icon=":material/login:", width="stretch")
        st.caption("You can also try it as a guest.")

if login_error:
    st.error(login_error, title="Zooniverse sign-in failed")
    if oauth:
        st.link_button("Try again", zooniverse.login_url(oauth["client_id"], oauth["redirect_uri"]))
if state.auth_message:
    st.warning(state.auth_message)

# --- Workflow (once per session) --------------------------------------------
if state.workflow is None:
    try:
        state.workflow = zooniverse.get_workflow(workflow_id, token=state.zoo_token)
    except zooniverse.ZooniverseError as err:
        if handle_unauthorized(err):
            st.rerun()
        st.error(str(err), title="Could not reach Zooniverse")
        st.button("Retry", icon=":material/refresh:")
        st.stop()
    state.workflow_version = str(state.workflow.get("version", ""))

task = (state.workflow.get("tasks") or {}).get(zooniverse.TASK_KEY) or {}
with st.expander("How to classify", icon=":material/help:"):
    st.markdown(task.get("question") or DEFAULT_QUESTION)
    st.markdown(task.get("help") or DEFAULT_HELP)

# --- Subject ----------------------------------------------------------------
if state.subject is None:
    try:
        subject = zooniverse.get_next_subject(workflow_id, token=state.zoo_token, exclude_ids=state.seen_ids)
    except zooniverse.ZooniverseError as err:
        if handle_unauthorized(err):
            st.rerun()
        st.error(str(err), title="Could not get the next image")
        st.button("Retry", icon=":material/refresh:")
        st.stop()
    if subject is None:
        st.success("You've seen all the available images, thank you!")
        st.stop()
    state.subject = subject
    state.seen_ids.append(str(subject["id"]))
    state.started_at = utc_now()

subject = state.subject
subject_id = str(subject["id"])

# The toast is shown once; the Talk link stays until the next submit or skip.
if state.last_result and state.last_result.get("toast"):
    st.toast(state.last_result["toast"], icon=":material/check_circle:")
    state.last_result["toast"] = None

image_col, viewer_col = st.columns(2)

image = natural_size = None
with image_col:
    image_url = zooniverse.subject_image_url(subject)
    try:
        if not image_url:
            raise ValueError("the subject has no image")
        image, natural_size = fetch_subject_image(image_url)
    except (requests.RequestException, OSError, ValueError) as err:
        st.warning(f"Could not load the image for subject {subject_id}: {err}", title="Image not available")
        if st.button("Skip", key="skip_broken", icon=":material/skip_next:"):
            state.last_result = None
            next_subject()
            st.rerun()
    if image is not None:
        st.image(image, caption=f"Subject {subject_id}", width="stretch", alt="Underwater camera image")

camera = camera_for_subject(subject, render_config)
with viewer_col:
    # Same key for every subject, so the model is never re-downloaded; the
    # pose token resets the pose when the subject changes.
    pose = mussel_viewer(
        viewer_config(render_config, camera),
        initial_pose=DEFAULT_POSE,
        height=render_config.get("viewer_height", 640),
        aspect=image.width / image.height if image else None,
        pose_token=subject_id,
        key="mussel_viewer",
    )

# A pose is only valid if it was committed while this subject was shown.
pose_ready = bool(pose) and pose.get("token") == subject_id and image is not None


def submit() -> bool:
    """Submit the current pose; on failure show why and keep the pose."""
    try:
        zooniverse.create_classification(
            project_id=project_id,
            workflow_id=workflow_id,
            workflow_version=state.workflow_version,
            subject_id=subject_id,
            pose=pose,
            started_at=state.started_at,
            finished_at=utc_now(),
            subject_dimensions=[{
                "naturalWidth": natural_size[0],
                "naturalHeight": natural_size[1],
                "clientWidth": None,
                "clientHeight": None,
            }],
            camera=camera,
            token=state.zoo_token,
        )
    except zooniverse.ZooniverseError as err:
        if handle_unauthorized(err):
            st.error(
                "Your Zooniverse sign-in has expired, so the classification was not submitted. "
                "Log in again, or press Submit to send it as a guest.",
                title="Not submitted",
            )
        else:
            st.error(str(err), title="Not submitted")
        return False
    return True


# --- Actions ----------------------------------------------------------------
with st.container(horizontal=True, vertical_alignment="center"):
    submit_clicked = st.button(
        "Submit", type="primary", icon=":material/send:", disabled=not pose_ready
    )
    talk_clicked = st.button(
        "Done & Talk", icon=":material/forum:", disabled=not pose_ready
    )
    skip_clicked = st.button("Skip", icon=":material/skip_next:")
    if not pose_ready and image is not None:
        st.caption("Rotate or adjust the mussel first.")

result = state.last_result
if result and result.get("talk_url"):
    st.link_button(
        f"Discuss subject {result['talk_subject']} on Talk",
        result["talk_url"],
        icon=":material/open_in_new:",
    )

if submit_clicked or talk_clicked:
    if submit():
        state.last_result = {
            "toast": "Classification submitted. Thank you!",
            "talk_url": zooniverse.talk_url(project_slug, subject_id) if talk_clicked else None,
            "talk_subject": subject_id,
        }
        next_subject()
        st.rerun()
elif skip_clicked:
    state.last_result = None
    next_subject()
    st.rerun()
