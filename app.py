"""Streamlit entrypoint: interactive mussel viewer + pose readout.

All rendering and interaction (drag-to-rotate, orientation and burial
sliders) happens inside the three.js component in the browser. This script
only reruns when an interaction ends and the component reports a new pose.
"""
from __future__ import annotations

import json
import os

import pandas as pd
import streamlit as st

import geometry
import mussel_loader
from mussel_viewer import mussel_viewer

_HERE = os.path.dirname(os.path.abspath(__file__))
RENDER_CONFIG_PATH = os.path.join(_HERE, "render_config.json")

DEFAULT_POSE = {"yaw": 0.0, "pitch": 0.0, "roll": 0.0, "height": 0.0}

st.set_page_config(page_title="Mussel 3D Visualizer", layout="wide")


@st.cache_resource(show_spinner="Converting the mussel model (first run only)…")
def ensure_assets() -> None:
    mussel_loader.ensure_assets()


def load_render_config() -> dict:
    with open(RENDER_CONFIG_PATH, "r") as f:
        return json.load(f)


ensure_assets()
render_config = load_render_config()

st.title("Mussel 3D Visualizer")

pose = mussel_viewer(
    render_config,
    initial_pose=DEFAULT_POSE,
    height=render_config.get("viewer_height", 640),
    key="mussel_viewer",
) or dict(DEFAULT_POSE)

st.subheader("Committed pose")
st.caption("Updated when you release the mouse or a slider.")

metric_cols = st.columns(5)
metric_cols[0].metric("Yaw", f"{pose['yaw']:.1f}°")
metric_cols[1].metric("Pitch", f"{pose['pitch']:.1f}°")
metric_cols[2].metric("Roll", f"{pose['roll']:.1f}°")
metric_cols[3].metric("Height", f"{pose['height']:.2f}")
metric_cols[4].metric("Approx. burial", f"{abs(pose['height']) * 100:.0f}%")

matrix_col, export_col = st.columns([2, 1])
with matrix_col:
    st.markdown("**Rotation matrix** (mussel frame → world, Z up)")
    st.caption("Column j is the mussel's j-axis expressed in world coordinates.")
    st.dataframe(
        pd.DataFrame(
            geometry.rotation_matrix(pose),
            index=["world X", "world Y", "world Z"],
            columns=["mussel x", "mussel y", "mussel z"],
        ).style.format("{:+.4f}"),
    )
with export_col:
    st.markdown("**Export**")
    st.download_button(
        "Download pose (JSON)",
        data=json.dumps(pose, indent=2),
        file_name="mussel_pose.json",
        mime="application/json",
    )
    with st.expander("Raw pose"):
        st.json(pose)
