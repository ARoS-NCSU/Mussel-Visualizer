"""Only module that imports plotly. Builds a go.Figure from posed numpy
arrays and the render_config dict.

Note: an earlier version of this file split the mussel and substrate plane
into two separate, CSS-overlaid go.Figure objects, so that moving the
Height slider (which only needs to move the plane) wouldn't force the
browser to reprocess the much larger mussel mesh. That was reverted: Plotly
gl3d (3D WebGL) scenes do not reliably composite a transparent background —
confirmed directly by reading back canvas pixels — so the "plane on top of
mussel" overlay rendered as opaque and hid the mussel entirely. Both traces
have to live in one scene for correct depth/occlusion.
"""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go

# Plotly's scene.camera.eye is expressed in a frame normalized to the data's
# auto-fit range (with aspectmode='data'), not literal world units, so there
# is no exact closed-form "meters -> eye units" conversion. This constant is
# tuned empirically for a reasonably framed default view; render_config's
# "distance_scale" multiplies it for fine-tuning without code changes.
DEFAULT_EYE_MAGNITUDE = 2.2

UIREVISION = "mussel-viewer"  # constant so manual orbit/zoom survives reruns


def _camera_dict(camera_config: dict) -> dict:
    elevation = np.radians(camera_config.get("elevation_deg", 25.0))
    azimuth = np.radians(camera_config.get("azimuth_deg", 45.0))
    distance_scale = camera_config.get("distance_scale", 1.0)
    mag = DEFAULT_EYE_MAGNITUDE * distance_scale

    eye = (
        mag * np.cos(elevation) * np.cos(azimuth),
        mag * np.cos(elevation) * np.sin(azimuth),
        mag * np.sin(elevation),
    )
    target = camera_config.get("target") or (0.0, 0.0, 0.0)
    return dict(
        eye=dict(x=float(eye[0]), y=float(eye[1]), z=float(eye[2])),
        center=dict(x=float(target[0]), y=float(target[1]), z=float(target[2])),
        up=dict(x=0, y=0, z=1),
    )


def _to_hex_colors(rgb_uint8: np.ndarray) -> list[str]:
    return [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in rgb_uint8]


def plane_half_extent_from(plane_vertices: np.ndarray) -> float:
    return float(np.max(np.abs(plane_vertices[:, :2]))) if len(plane_vertices) else 1.0


def build_figure(
    mussel_vertices: np.ndarray,
    mussel_faces: np.ndarray,
    mussel_colors: np.ndarray,
    plane_vertices: np.ndarray,
    plane_faces: np.ndarray,
    render_config: dict,
) -> go.Figure:
    substrate_color = render_config.get("substrate_color", "#C2B280")
    background_color = render_config.get("background_color", "#FFFFFF")

    mussel_trace = go.Mesh3d(
        x=mussel_vertices[:, 0],
        y=mussel_vertices[:, 1],
        z=mussel_vertices[:, 2],
        i=mussel_faces[:, 0],
        j=mussel_faces[:, 1],
        k=mussel_faces[:, 2],
        vertexcolor=_to_hex_colors(mussel_colors),
        flatshading=False,
        lighting=dict(ambient=0.55, diffuse=0.75, specular=0.15, roughness=0.9),
        name="mussel",
        showscale=False,
    )

    substrate_trace = go.Mesh3d(
        x=plane_vertices[:, 0],
        y=plane_vertices[:, 1],
        z=plane_vertices[:, 2],
        i=plane_faces[:, 0],
        j=plane_faces[:, 1],
        k=plane_faces[:, 2],
        color=substrate_color,
        flatshading=True,
        lighting=dict(ambient=0.9, diffuse=0.3, specular=0.0),
        name="substrate",
        showscale=False,
        hoverinfo="skip",
    )

    # Fix the axis ranges to a constant, equal-span extent (derived from the
    # substrate plane, which is sized from the mesh's rotation-invariant
    # bounding_radius and never changes with the sliders). This keeps the
    # frame stable across reruns (needed for uirevision to preserve manual
    # orbit/zoom) and gives all three axes the same numeric span.
    #
    # aspectmode='manual' + aspectratio=(1,1,1) is what actually makes a unit
    # of X, Y, and Z the same visual length. aspectmode='data' instead
    # derives box proportions from the *trace data's* own extent — since the
    # mussel is thin/flat relative to the wide plane, that squashed Z.
    axis_range = [-plane_half_extent_from(plane_vertices), plane_half_extent_from(plane_vertices)]

    fig = go.Figure(data=[substrate_trace, mussel_trace])
    fig.update_layout(
        scene=dict(
            aspectmode="manual",
            aspectratio=dict(x=1, y=1, z=1),
            camera=_camera_dict(render_config.get("camera", {})),
            bgcolor=background_color,
            xaxis=dict(visible=False, range=axis_range),
            yaxis=dict(visible=False, range=axis_range),
            zaxis=dict(visible=False, range=axis_range),
            dragmode=False,  # disable click-drag orbit; view is fixed to render_config.json
            uirevision=UIREVISION,
        ),
        paper_bgcolor=background_color,
        margin=dict(l=0, r=0, t=0, b=0),
        uirevision=UIREVISION,
        showlegend=False,
    )
    return fig
