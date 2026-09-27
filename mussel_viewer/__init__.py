"""Streamlit custom component wrapping the three.js mussel viewer.

The frontend (``frontend/``) is plain HTML/JS with no build step. Streamlit
serves that directory as static files, so the GLB is fetched by URL once and
cached by the browser. Only small values cross the Python <-> JS boundary:
the render config and initial pose going in, and the committed pose coming
back out.
"""
from __future__ import annotations

import os

import streamlit.components.v1 as components

_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
_GLB_PATH = os.path.join(_FRONTEND_DIR, "assets", "mussel.glb")

_component = components.declare_component("mussel_viewer", path=_FRONTEND_DIR)


def mussel_viewer(
    render_config: dict,
    initial_pose: dict | None = None,
    height: int = 640,
    key: str | None = None,
) -> dict | None:
    """Render the interactive viewer and return the last committed pose.

    The pose is ``{"yaw", "pitch", "roll", "quaternion": {"w","x","y","z"},
    "height"}`` (angles in degrees, ZYX Euler convention; height in [-1, 0]).
    It is updated when a drag or slider interaction ends, and is ``None``
    until the first interaction.

    ``initial_pose`` is applied only when the viewer first loads; after that
    the viewer owns the pose, so Streamlit reruns never reset it.
    """
    asset_version = str(int(os.path.getmtime(_GLB_PATH))) if os.path.exists(_GLB_PATH) else ""
    return _component(
        config=render_config,
        initial_pose=initial_pose,
        height=height,
        asset_version=asset_version,
        key=key,
        default=None,
    )
