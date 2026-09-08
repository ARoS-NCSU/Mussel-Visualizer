"""Streamlit entrypoint: mussel orientation/burial sliders + 3D view."""
from __future__ import annotations

import json
import os

import streamlit as st

import geometry
import mussel_loader
import render_plotly

_HERE = os.path.dirname(os.path.abspath(__file__))
RENDER_CONFIG_PATH = os.path.join(_HERE, "render_config.json")

st.set_page_config(page_title="Mussel 3D Visualizer", layout="wide")


@st.cache_resource
def get_mesh() -> mussel_loader.MeshData:
    return mussel_loader.load_mesh()


def load_render_config() -> dict:
    with open(RENDER_CONFIG_PATH, "r") as f:
        return json.load(f)


mesh_data = get_mesh()
render_config = load_render_config()

st.title("Mussel 3D Visualizer")

with st.sidebar:
    st.header("Mussel orientation")
    yaw = st.slider("Yaw (deg)", -180.0, 180.0, 0.0, step=1.0)
    pitch = st.slider("Pitch (deg)", -180.0, 180.0, 0.0, step=1.0)
    roll = st.slider("Roll (deg)", -180.0, 180.0, 0.0, step=1.0)

    st.header("Burial")
    height = st.slider(
        "Height",
        -1.0,
        0.0,
        0.0,
        step=0.01,
        help="0 = resting on top of the substrate, -1 = fully buried",
    )

# Only recompute the mussel's rotation when orientation actually changed;
# a height-only rerun reuses the cached posed vertices. This is a modest
# CPU saving, not a fix for the browser-side WebGL rebuild cost (both
# traces still live in one figure/scene — see render_plotly.py for why).
orientation_key = (yaw, pitch, roll)
if st.session_state.get("_orientation_key") != orientation_key:
    posed_vertices, z_range = geometry.rotate_and_rest(
        mesh_data.vertices, mesh_data.rest_centroid, yaw, pitch, roll
    )
    st.session_state["_orientation_key"] = orientation_key
    st.session_state["_posed_vertices"] = posed_vertices
    st.session_state["_z_range"] = z_range
else:
    posed_vertices = st.session_state["_posed_vertices"]
    z_range = st.session_state["_z_range"]

plane_z = geometry.plane_z_from_height(height, z_range)
plane_vertices, plane_faces = geometry.make_substrate_plane(
    mesh_data.bounding_radius, render_config.get("substrate_size_scale", 2.2), z=plane_z
)

fig = render_plotly.build_figure(
    posed_vertices,
    mesh_data.faces,
    mesh_data.vertex_colors,
    plane_vertices,
    plane_faces,
    render_config,
)

view_col, params_col = st.columns([3, 1])
with view_col:
    st.plotly_chart(
        fig,
        use_container_width=True,
        theme=None,
        key="mussel_plot",
        config={"displayModeBar": False, "scrollZoom": False, "doubleClick": False},
    )
with params_col:
    st.subheader("Current parameters")
    st.metric("Yaw", f"{yaw:.1f}°")
    st.metric("Pitch", f"{pitch:.1f}°")
    st.metric("Roll", f"{roll:.1f}°")
    st.metric("Height", f"{height:.2f}")
    st.metric("Approx. burial", f"{abs(height) * 100:.0f}%")
