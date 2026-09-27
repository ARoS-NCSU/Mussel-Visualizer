"""Pose math for poses reported by the viewer.

The viewer does all per-frame math in the browser; these helpers are for
using a committed pose on the Python side (display, logging, analysis).
Euler convention everywhere: intrinsic ZYX, i.e.
R = Rz(yaw) @ Ry(pitch) @ Rx(roll), matching three.js Euler order "ZYX".
"""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation


def rotation_from_pose(pose: dict) -> Rotation:
    """Rotation from a viewer pose, preferring the (gimbal-lock-free) quaternion."""
    q = pose.get("quaternion")
    if q:
        return Rotation.from_quat([q["x"], q["y"], q["z"], q["w"]])  # scipy is scalar-last
    return Rotation.from_euler("ZYX", [pose["yaw"], pose["pitch"], pose["roll"]], degrees=True)


def rotation_matrix(pose: dict) -> np.ndarray:
    return rotation_from_pose(pose).as_matrix()


def euler_zyx_deg(pose: dict) -> tuple[float, float, float]:
    """(yaw, pitch, roll) in degrees, recomputed from the quaternion."""
    yaw, pitch, roll = rotation_from_pose(pose).as_euler("ZYX", degrees=True)
    return float(yaw), float(pitch), float(roll)
