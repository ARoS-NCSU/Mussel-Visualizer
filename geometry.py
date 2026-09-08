"""Stage 2: pure-numpy pose transforms. No rendering-library imports here,
so this module can be reused unchanged if the renderer is swapped later.

Orientation (yaw/pitch/roll) and burial (height) are deliberately kept
independent: rotating the mussel requires moving all of its vertices, which
is the expensive part to resend/reprocess in the browser. Burial depth is
instead expressed as a z-offset on the (4-vertex) substrate plane, so a
height-only change never touches the mussel's vertex data at all.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation


def rotate_and_rest(
    vertices: np.ndarray,
    rest_centroid: np.ndarray,
    yaw_deg: float,
    pitch_deg: float,
    roll_deg: float,
):
    """Rotate the mesh about its own rest-pose centroid and settle it so its
    lowest point sits at z=0 (i.e. resting on top of a substrate at z=0,
    the height=0 case). Returns (posed_vertices, z_range) where z_range is
    the mesh's z-extent after rotation — needed by plane_z_from_height to
    reproduce the same relative burial depth by moving the plane instead.
    """
    centered = vertices - rest_centroid
    rot = Rotation.from_euler("ZYX", [yaw_deg, pitch_deg, roll_deg], degrees=True)
    rotated = rot.apply(centered)

    z_min = rotated[:, 2].min()
    z_max = rotated[:, 2].max()

    posed = rotated.copy()
    posed[:, 2] -= z_min
    return posed, float(z_max - z_min)


def plane_z_from_height(height: float, z_range: float) -> float:
    """z-position for the substrate plane that reproduces the same relative
    burial depth as translating the mussel would (see apply_pose's old
    docstring), without touching the mussel mesh at all.

    height=0  -> plane at z=0 (mussel, resting with its base at z=0, is
                 fully above the substrate).
    height=-1 -> plane at z=z_range (mussel's highest point, fully buried).
    """
    return -height * z_range


def apply_pose(
    vertices: np.ndarray,
    rest_centroid: np.ndarray,
    yaw_deg: float,
    pitch_deg: float,
    roll_deg: float,
    height: float,
) -> np.ndarray:
    """Equivalent single-mesh-moves version of the pose transform, kept for
    reference/testing. The live app uses rotate_and_rest + plane_z_from_height
    instead so that height changes don't require moving the mussel.
    """
    posed, z_range = rotate_and_rest(vertices, rest_centroid, yaw_deg, pitch_deg, roll_deg)
    posed = posed.copy()
    posed[:, 2] += height * z_range
    return posed


def make_substrate_plane(bounding_radius: float, size_scale: float = 2.2, z: float = 0.0):
    """A flat quad (2 triangles) centered at world origin, at height z."""
    s = size_scale * bounding_radius
    vertices = np.array(
        [[-s, -s, z], [s, -s, z], [s, s, z], [-s, s, z]],
        dtype=np.float32,
    )
    faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
    return vertices, faces
