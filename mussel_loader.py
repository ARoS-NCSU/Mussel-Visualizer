"""Convert the mussel USDC model into web-friendly assets for the viewer.

Outputs (written into the viewer component's static directory, so the
browser fetches them once and caches them):

- ``mussel.glb``: full-resolution, textured glTF binary. Vertices are
  expressed relative to the mussel's rest-pose center, so rotating the glTF
  node rotates the mussel about its own center.
- ``mussel_meta.json``: the convex-hull vertices (same frame as the GLB)
  plus a few scalars. The viewer transforms only these few hundred points to
  find the mussel's lowest/highest point after a rotation, which is what the
  "rest on the substrate" and burial-depth logic need.

Only this module imports ``pxr``. The generated assets are committed to
the repo, so the app (and deployments such as Streamlit Community Cloud)
never needs to convert: conversion peaks at ~4 GB of RAM. Run
``python mussel_loader.py`` after changing the model or conversion settings,
and commit the regenerated files. ``app.py`` also runs it if they are
missing.
"""
from __future__ import annotations

import io
import json
import os
import struct
import sys

import numpy as np
from PIL import Image
from pxr import Usd, UsdGeom, UsdShade
from scipy.spatial import ConvexHull

_HERE = os.path.dirname(os.path.abspath(__file__))
USD_PATH = os.path.join(_HERE, "Model01", "Musselv1.usdc")
ASSETS_DIR = os.path.join(_HERE, "mussel_viewer", "frontend", "assets")
GLB_PATH = os.path.join(ASSETS_DIR, "mussel.glb")
META_PATH = os.path.join(ASSETS_DIR, "mussel_meta.json")
MUSSEL_ROOT_PATH = "/root/Mussel"

# USD's st origin is bottom-left, glTF's is top-left. If the texture ever
# looks vertically mirrored in the viewer, flip this.
FLIP_V = True

# Longest side of the texture embedded in the GLB. JPEG keeps the download
# small; the source PNG is 2048px.
TEXTURE_MAX_SIZE = 2048
TEXTURE_JPEG_QUALITY = 90

# Normals are compared after rounding to this many steps per unit, so that
# corners sharing a point, a UV and (nearly) the same normal become one vertex.
_NORMAL_QUANT = 1024

# Directions used to thin the convex hull shipped to the viewer (see
# _support_points). More directions = more exact resting/burial height.
SUPPORT_DIRECTIONS = 8000

_FALLBACK_COLOR = (0.63, 0.63, 0.63)


def _find_mesh_prims(stage: Usd.Stage) -> list[Usd.Prim]:
    root = stage.GetPrimAtPath(MUSSEL_ROOT_PATH)
    if not root.IsValid():
        print(
            f"[mussel_loader] WARNING: {MUSSEL_ROOT_PATH} not found, "
            "scanning entire stage for meshes",
            file=sys.stderr,
        )
        root = stage.GetPseudoRoot()
    return [p for p in Usd.PrimRange(root) if p.IsA(UsdGeom.Mesh)]


def _fan_triangulate(face_vertex_counts) -> np.ndarray:
    """Vectorized fan triangulation of (possibly n-gon) polygons.

    Returns triangles as indices into the *face-vertex (corner)* arrays, so
    the same triangles can index points, face-varying UVs and normals.

    Note: fan triangulation is only guaranteed correct for convex polygons;
    concave n-gons could triangulate with minor visual artifacts. Acceptable
    for this scan/CAD-style mesh.
    """
    counts = np.asarray(face_vertex_counts, dtype=np.int64)
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    n_tris = np.maximum(counts - 2, 0)
    total = int(n_tris.sum())
    if total == 0:
        return np.zeros((0, 3), dtype=np.int64)

    face_id = np.repeat(np.arange(len(counts)), n_tris)
    group_start = np.repeat(np.cumsum(n_tris) - n_tris, n_tris)
    local_t = np.arange(total) - group_start

    face_offset = starts[face_id]
    return np.stack([face_offset, face_offset + local_t + 1, face_offset + local_t + 2], axis=1)


def _world_matrix(prim: Usd.Prim) -> np.ndarray:
    xformable = UsdGeom.Xformable(prim)
    mat4 = xformable.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    return np.array(mat4, dtype=np.float64)  # row-vector convention: p' = [p,1] @ m


def _local_to_world(points: np.ndarray, m: np.ndarray) -> np.ndarray:
    homo = np.concatenate([points, np.ones((points.shape[0], 1))], axis=1)
    return (homo @ m)[:, :3]


def _normals_to_world(normals: np.ndarray, m: np.ndarray) -> np.ndarray:
    # Row-vector convention: normals transform by the inverse-transpose.
    n = normals @ np.linalg.inv(m[:3, :3]).T
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def _resolve_diffuse_source(prim: Usd.Prim):
    """Returns ('texture', resolved_path) or ('color', (r,g,b) in 0..1) or (None, None)."""
    binding_api = UsdShade.MaterialBindingAPI(prim)
    material, _rel = binding_api.ComputeBoundMaterial()
    if not material:
        return None, None

    surface_shader, _out_name, _out_type = material.ComputeSurfaceSource()
    if not surface_shader:
        return None, None

    diffuse_input = surface_shader.GetInput("diffuseColor")
    if diffuse_input is None:
        return None, None

    if diffuse_input.HasConnectedSource():
        sources, _invalid = diffuse_input.GetConnectedSources()
        if not sources:
            return None, None
        src_prim = sources[0].source.GetPrim()
        tex_shader = UsdShade.Shader(src_prim)
        if tex_shader.GetIdAttr().Get() == "UsdUVTexture":
            file_input = tex_shader.GetInput("file")
            asset_path = file_input.Get() if file_input else None
            if asset_path is not None:
                resolved = asset_path.resolvedPath or asset_path.path
                return "texture", resolved
        return None, None

    const_val = diffuse_input.Get()
    if const_val is not None:
        return "color", (float(const_val[0]), float(const_val[1]), float(const_val[2]))
    return None, None


def _extract_prim(prim: Usd.Prim, y_up: bool) -> dict:
    """Triangulated, de-duplicated vertex data for one USD mesh, in world space."""
    mesh = UsdGeom.Mesh(prim)
    m = _world_matrix(prim)
    points = _local_to_world(np.array(mesh.GetPointsAttr().Get(), dtype=np.float64), m)
    fvi = np.asarray(mesh.GetFaceVertexIndicesAttr().Get(), dtype=np.int64)
    corner_tris = _fan_triangulate(mesh.GetFaceVertexCountsAttr().Get())
    if mesh.GetOrientationAttr().Get() == UsdGeom.Tokens.leftHanded:
        corner_tris = corner_tris[:, ::-1]
    n_corners = len(fvi)

    # Per-corner UV (as an index into a UV table, so seams split vertices).
    st = UsdGeom.PrimvarsAPI(prim).GetPrimvar("st")
    if st and st.HasValue():
        uv_table = np.array(st.Get(), dtype=np.float64)
        interp = st.GetInterpolation()
        idx = np.asarray(st.GetIndices(), dtype=np.int64) if st.IsIndexed() else None
        if interp == UsdGeom.Tokens.faceVarying:
            corner_uv = idx if idx is not None else np.arange(n_corners)
        elif interp in (UsdGeom.Tokens.vertex, UsdGeom.Tokens.varying):
            corner_uv = (idx if idx is not None else np.arange(len(points)))[fvi]
        else:
            uv_table, corner_uv = np.zeros((1, 2)), np.zeros(n_corners, dtype=np.int64)
    else:
        uv_table, corner_uv = np.zeros((1, 2)), np.zeros(n_corners, dtype=np.int64)

    # Per-corner normal (fall back to face normals when absent).
    normals_attr = mesh.GetNormalsAttr()
    normals = np.array(normals_attr.Get(), dtype=np.float64) if normals_attr.HasValue() else None
    if normals is not None and mesh.GetNormalsInterpolation() == UsdGeom.Tokens.faceVarying and len(normals) == n_corners:
        corner_n = _normals_to_world(normals, m)
    elif normals is not None and len(normals) == len(points):
        corner_n = _normals_to_world(normals, m)[fvi]
    else:
        corner_n = None

    if y_up:
        # Rotate +90deg about X: (x, y, z) -> (x, -z, y), a proper rotation
        # (determinant +1) that turns Y-up into Z-up.
        flip = np.array([1, -1, 1])
        points = points[:, [0, 2, 1]] * flip
        if corner_n is not None:
            corner_n = corner_n[:, [0, 2, 1]] * flip

    used = corner_tris.reshape(-1)
    if corner_n is None:
        a, b, c = (points[fvi[corner_tris[:, k]]] for k in range(3))
        fn = np.cross(b - a, c - a)
        fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
        corner_n = np.zeros((n_corners, 3))
        corner_n[used] = np.repeat(fn, 3, axis=0)

    # One output vertex per unique (point, uv, quantized normal) corner.
    nq = np.round(corner_n[used] * _NORMAL_QUANT).astype(np.int64)
    keys = np.column_stack([fvi[used], corner_uv[used], nq])
    _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    src = used[first]

    return {
        "name": prim.GetName(),
        "positions": points[fvi[src]],
        "normals": corner_n[src],
        "uvs": uv_table[corner_uv[src]],
        "indices": inverse.reshape(-1, 3).astype(np.uint32),
        "unique_points": points[np.unique(fvi)],
    }


def _load_texture(path: str | None) -> bytes | None:
    if not path:
        return None
    try:
        img = Image.open(path)
        img.load()
    except Exception as exc:  # noqa: BLE001 - defensive, e.g. unreadable EXR
        print(f"[mussel_loader] WARNING: could not decode texture '{path}' ({exc})", file=sys.stderr)
        return None
    img = img.convert("RGB")
    if max(img.size) > TEXTURE_MAX_SIZE:
        img.thumbnail((TEXTURE_MAX_SIZE, TEXTURE_MAX_SIZE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=TEXTURE_JPEG_QUALITY)
    return buf.getvalue()


def _write_glb(path: str, parts: list[dict], texture_jpeg: bytes | None, base_color) -> None:
    """Minimal glTF 2.0 binary writer: one mesh, one primitive per USD prim,
    one shared (optionally textured) PBR material."""
    blob = bytearray()
    buffer_views, accessors = [], []

    def add_view(data: bytes, target=None) -> int:
        while len(blob) % 4:
            blob.append(0)
        view = {"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        blob.extend(data)
        buffer_views.append(view)
        return len(buffer_views) - 1

    def add_accessor(arr: np.ndarray, gl_type: str, component: int, target: int, minmax=False) -> int:
        acc = {
            "bufferView": add_view(arr.tobytes(), target),
            "componentType": component,
            "count": int(arr.shape[0]),
            "type": gl_type,
        }
        if minmax:
            acc["min"] = arr.min(axis=0).tolist()
            acc["max"] = arr.max(axis=0).tolist()
        accessors.append(acc)
        return len(accessors) - 1

    FLOAT, UINT32, ARRAY, ELEMENTS = 5126, 5125, 34962, 34963
    primitives = []
    for part in parts:
        uvs = part["uvs"].astype(np.float32)
        if FLIP_V:
            uvs[:, 1] = 1.0 - uvs[:, 1]
        attributes = {
            "POSITION": add_accessor(part["positions"].astype(np.float32), "VEC3", FLOAT, ARRAY, minmax=True),
            "NORMAL": add_accessor(part["normals"].astype(np.float32), "VEC3", FLOAT, ARRAY),
        }
        if texture_jpeg is not None:
            attributes["TEXCOORD_0"] = add_accessor(uvs, "VEC2", FLOAT, ARRAY)
        primitives.append({
            "attributes": attributes,
            "indices": add_accessor(part["indices"].reshape(-1), "SCALAR", UINT32, ELEMENTS),
            "material": 0,
            "extras": {"name": part["name"]},
        })

    pbr = {"baseColorFactor": [*base_color, 1.0], "metallicFactor": 0.0, "roughnessFactor": 0.6}
    gltf = {
        "asset": {"version": "2.0", "generator": "Mussel-Visualizer mussel_loader.py"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "Mussel", "mesh": 0}],
        "meshes": [{"name": "Mussel", "primitives": primitives}],
        "materials": [{"name": "mussel", "pbrMetallicRoughness": pbr, "doubleSided": True}],
    }
    if texture_jpeg is not None:
        gltf["images"] = [{"bufferView": add_view(texture_jpeg), "mimeType": "image/jpeg"}]
        gltf["samplers"] = [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}]
        gltf["textures"] = [{"sampler": 0, "source": 0}]
        pbr["baseColorFactor"] = [1.0, 1.0, 1.0, 1.0]
        pbr["baseColorTexture"] = {"index": 0}
    while len(blob) % 4:
        blob.append(0)
    gltf["buffers"] = [{"byteLength": len(blob)}]
    gltf["bufferViews"] = buffer_views
    gltf["accessors"] = accessors

    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(blob)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(json_bytes), 0x4E4F534A))
        f.write(json_bytes)
        f.write(struct.pack("<II", len(blob), 0x004E4942))
        f.write(blob)


def _support_points(hull_points: np.ndarray, n_directions: int = SUPPORT_DIRECTIONS) -> np.ndarray:
    """Thin a (smooth, very dense) convex hull down to the extreme points
    along a Fibonacci-sphere set of directions. The lowest/highest point
    after any rotation is then within ~radius * spacing^2 / 2 of exact.
    """
    i = np.arange(n_directions) + 0.5
    z = 1.0 - 2.0 * i / n_directions
    phi = np.pi * (1.0 + 5**0.5) * i
    r = np.sqrt(1.0 - z * z)
    dirs = np.stack([r * np.cos(phi), r * np.sin(phi), z], axis=1)
    return hull_points[np.unique(np.argmax(hull_points @ dirs.T, axis=0))]


def convert(usd_path: str = USD_PATH, glb_path: str = GLB_PATH, meta_path: str = META_PATH) -> None:
    stage = Usd.Stage.Open(usd_path)
    if stage is None:
        raise FileNotFoundError(f"Could not open USD stage: {usd_path}")

    up_axis = str(UsdGeom.GetStageUpAxis(stage))
    print(f"[mussel_loader] stage upAxis={up_axis}")

    mesh_prims = _find_mesh_prims(stage)
    print(f"[mussel_loader] found {len(mesh_prims)} mesh prim(s) under {MUSSEL_ROOT_PATH}")
    if not mesh_prims:
        raise RuntimeError(f"No UsdGeom.Mesh prims found under {MUSSEL_ROOT_PATH}")

    parts = [_extract_prim(p, y_up=(up_axis == "Y")) for p in mesh_prims]
    for p, part in zip(mesh_prims, parts):
        print(f"[mussel_loader]   {p.GetPath()}: vertices={len(part['positions'])} triangles={len(part['indices'])}")

    # The viewer uses a single material; take it from the first prim that has one.
    texture_jpeg, base_color = None, _FALLBACK_COLOR
    for prim in mesh_prims:
        kind, value = _resolve_diffuse_source(prim)
        if kind == "texture":
            texture_jpeg = _load_texture(value)
            if texture_jpeg is not None:
                break
        elif kind == "color":
            base_color = value
            break

    # Re-center on the bounding-box center so the glTF node's origin is the
    # mussel's rest-pose center (the pivot for all rotations).
    all_points = np.concatenate([part["unique_points"] for part in parts])
    rest_center = (all_points.min(axis=0) + all_points.max(axis=0)) / 2.0
    for part in parts:
        part["positions"] = part["positions"] - rest_center
    centered = all_points - rest_center

    hull = _support_points(centered[ConvexHull(centered).vertices])
    bounding_radius = float(np.linalg.norm(centered, axis=1).max())

    os.makedirs(os.path.dirname(glb_path), exist_ok=True)
    _write_glb(glb_path, parts, texture_jpeg, base_color)
    with open(meta_path, "w") as f:
        json.dump(
            {
                "bounding_radius": bounding_radius,
                "rest_center": rest_center.tolist(),
                "hull": np.round(hull, 6).tolist(),
            },
            f,
        )

    n_tris = sum(len(p["indices"]) for p in parts)
    print(
        f"[mussel_loader] wrote {glb_path} ({os.path.getsize(glb_path) / 1e6:.1f} MB, "
        f"{n_tris} triangles, texture={'yes' if texture_jpeg else 'no'})"
    )
    print(f"[mussel_loader] wrote {meta_path} (hull points={len(hull)}, bounding_radius={bounding_radius:.4f})")


if __name__ == "__main__":
    convert()
