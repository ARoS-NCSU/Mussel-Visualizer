"""Streamlit entrypoint: camera image next to the interactive mussel viewer.

The reference image (``image_file`` in render_config.json) is shown on the
left so the 3D view on the right can be posed to match it. All rendering and
interaction (drag-to-rotate, orientation and burial sliders) happens inside
the three.js component in the browser.
"""
from __future__ import annotations

import base64
import io
import json
import os

import streamlit as st
from PIL import Image

from mussel_viewer import assets_exist, mussel_viewer

_HERE = os.path.dirname(os.path.abspath(__file__))
RENDER_CONFIG_PATH = os.path.join(_HERE, "render_config.json")

DEFAULT_POSE = {"yaw": 0.0, "pitch": 0.0, "roll": 0.0, "height": 0.0}
# Longest side of the image sent to the browser; camera frames are much larger.
DISPLAY_MAX_SIDE = 1920
# Longest side of the substrate texture; WebGL handles up to 4096 everywhere.
SUBSTRATE_MAX_SIDE = 2048

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


@st.cache_data(max_entries=8, show_spinner=False)
def load_image(path: str, mtime: float) -> Image.Image:
    # mtime is part of the cache key so an edited file on disk is reloaded.
    image = Image.open(path)
    image.load()
    image.thumbnail((DISPLAY_MAX_SIDE, DISPLAY_MAX_SIDE))
    return image


@st.cache_data(max_entries=8, show_spinner=False)
def load_substrate_url(path: str, mtime: float) -> str:
    """The substrate image, downscaled, as a JPEG data URL for the viewer."""
    image = Image.open(path)
    image.thumbnail((SUBSTRATE_MAX_SIDE, SUBSTRATE_MAX_SIDE))
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def viewer_config(config: dict) -> dict:
    """The render config for the viewer, with the substrate image resolved."""
    config = dict(config)
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
        viewer_config(render_config),
        initial_pose=DEFAULT_POSE,
        height=render_config.get("viewer_height", 640),
        aspect=image.width / image.height if image else None,
        key="mussel_viewer",
    )
