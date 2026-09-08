"""Stage 1: convert the mussel USDC model into a lightweight numpy cache.

Only this module imports `pxr` and `PIL` — everything downstream of the
cached `.npz` file works with plain numpy arrays.
"""
from __future__ import annotations

import dataclasses
import os
import sys

import numpy as np
from PIL import Image
from pxr import Usd, UsdGeom, UsdShade
from scipy.ndimage import map_coordinates

_HERE = os.path.dirname(os.path.abspath(__file__))
USD_PATH = os.path.join(_HERE, "Model01", "Musselv1.usdc")
CACHE_PATH = os.path.join(_HERE, "assets", "mussel.npz")
MUSSEL_ROOT_PATH = "/root/Mussel"

# If the baked texture ever looks vertically mirrored, flip this.
FLIP_V = True

# Triangle count above which we automatically decimate (voxel clustering)
# down toward DECIMATE_TARGET_TRIANGLES. The raw Musselv1.usdc mesh is
# ~714k triangles, which measurably freezes the browser tab for 20-30s per
# slider drag when sent to Plotly on every Streamlit rerun — decimation is
# required for the app to be usable, not just a nice-to-have.
DECIMATE_THRESHOLD_TRIANGLES = 120_000
DECIMATE_TARGET_TRIANGLES = 10_000

_FALLBACK_COLOR = np.array([160, 160, 160], dtype=np.uint8)


@dataclasses.dataclass
class MeshData:
    vertices: np.ndarray        # (N, 3) float32, Z-up world space, rest pose
    faces: np.ndarray           # (M, 3) int32, triangulated
    vertex_colors: np.ndarray   # (N, 3) uint8
    rest_centroid: np.ndarray   # (3,) float32
    bounding_radius: float


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


def _fan_triangulate(face_vertex_counts, face_vertex_indices) -> np.ndarray:
    """Vectorized fan triangulation of (possibly n-gon) polygons.

    Note: fan triangulation is only guaranteed correct for convex polygons;
    concave n-gons could triangulate with minor visual artifacts. Acceptable
    for this scan/CAD-style mesh.
    """
    counts = np.asarray(face_vertex_counts, dtype=np.int64)
    indices = np.asarray(face_vertex_indices, dtype=np.int64)
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    n_tris = np.maximum(counts - 2, 0)
    total = int(n_tris.sum())
    if total == 0:
        return np.zeros((0, 3), dtype=np.int32)

    face_id = np.repeat(np.arange(len(counts)), n_tris)
    group_start = np.repeat(np.cumsum(n_tris) - n_tris, n_tris)
    local_t = np.arange(total) - group_start

    face_offset = starts[face_id]
    v0 = indices[face_offset]
    v1 = indices[face_offset + local_t + 1]
    v2 = indices[face_offset + local_t + 2]
    return np.stack([v0, v1, v2], axis=1).astype(np.int32)


def _local_to_world(points: np.ndarray, prim: Usd.Prim) -> np.ndarray:
    xformable = UsdGeom.Xformable(prim)
    mat4 = xformable.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    m = np.array(mat4, dtype=np.float64)  # row-vector convention: p' = [p,1] @ m
    homo = np.concatenate([points, np.ones((points.shape[0], 1))], axis=1)
    return (homo @ m)[:, :3]


def _average_uv_per_point(num_points, face_vertex_indices, flattened_uv) -> np.ndarray:
    point_idx = np.asarray(face_vertex_indices, dtype=np.int64)
    corner_uv = np.asarray(flattened_uv, dtype=np.float64)
    sum_u = np.bincount(point_idx, weights=corner_uv[:, 0], minlength=num_points)
    sum_v = np.bincount(point_idx, weights=corner_uv[:, 1], minlength=num_points)
    count = np.bincount(point_idx, minlength=num_points)
    count_safe = np.maximum(count, 1)
    return np.stack([sum_u / count_safe, sum_v / count_safe], axis=1)


def _voxel_cluster_decimate(vertices, faces, colors, voxel_size):
    """Merge vertices that fall in the same voxel_size grid cell, remap
    faces onto the merged vertices, and drop degenerate/duplicate faces.

    A simple, dependency-free (numpy only) decimation — lower quality than
    quadric edge-collapse, but adequate for an interactive viewer where the
    exact silhouette detail matters less than staying responsive.
    """
    bbox_min = vertices.min(axis=0)
    cell = np.floor((vertices - bbox_min) / voxel_size).astype(np.int64)
    _, inverse, counts = np.unique(cell, axis=0, return_inverse=True, return_counts=True)
    inverse = inverse.reshape(-1)
    n_clusters = counts.shape[0]

    sum_pos = np.zeros((n_clusters, 3), dtype=np.float64)
    sum_col = np.zeros((n_clusters, 3), dtype=np.float64)
    for a in range(3):
        sum_pos[:, a] = np.bincount(inverse, weights=vertices[:, a].astype(np.float64), minlength=n_clusters)
        sum_col[:, a] = np.bincount(inverse, weights=colors[:, a].astype(np.float64), minlength=n_clusters)
    new_vertices = (sum_pos / counts[:, None]).astype(np.float32)
    new_colors = np.clip(sum_col / counts[:, None], 0, 255).astype(np.uint8)

    new_faces = inverse[faces]
    degenerate = (
        (new_faces[:, 0] == new_faces[:, 1])
        | (new_faces[:, 1] == new_faces[:, 2])
        | (new_faces[:, 0] == new_faces[:, 2])
    )
    new_faces = new_faces[~degenerate]
    sorted_faces = np.sort(new_faces, axis=1)
    _, unique_idx = np.unique(sorted_faces, axis=0, return_index=True)
    new_faces = new_faces[np.sort(unique_idx)].astype(np.int32)

    return new_vertices, new_faces, new_colors


def _decimate_to_target(vertices, faces, colors, target_triangles):
    if len(faces) <= target_triangles:
        return vertices, faces, colors

    bbox_diag = float(np.linalg.norm(vertices.max(axis=0) - vertices.min(axis=0)))
    lo, hi = bbox_diag * 1e-4, bbox_diag * 0.25
    best = (vertices, faces, colors)
    for _ in range(10):
        mid = (lo + hi) / 2.0
        v2, f2, c2 = _voxel_cluster_decimate(vertices, faces, colors, mid)
        n = len(f2)
        best = (v2, f2, c2)
        if n > target_triangles * 1.15:
            lo = mid
        elif n < target_triangles * 0.5:
            hi = mid
        else:
            break
    return best


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


def _load_texture_rgb(path: str) -> np.ndarray | None:
    try:
        img = Image.open(path).convert("RGB")
        return np.asarray(img)
    except Exception as exc:  # noqa: BLE001 - defensive, e.g. unreadable EXR
        print(f"[mussel_loader] WARNING: could not decode texture '{path}' ({exc})", file=sys.stderr)
        # Heuristic fallback: some filenames encode a flat color as hex, e.g. color_0C0C0C.exr
        import re

        m = re.search(r"([0-9A-Fa-f]{6})", os.path.basename(path))
        if m:
            rgb = tuple(int(m.group(1)[i : i + 2], 16) for i in (0, 2, 4))
            print(f"[mussel_loader] falling back to flat color {rgb} parsed from filename", file=sys.stderr)
            return np.array([[rgb]], dtype=np.uint8)
        return None


def _sample_texture_colors(image_rgb: np.ndarray, uv: np.ndarray) -> np.ndarray:
    h, w = image_rgb.shape[:2]
    u = np.clip(uv[:, 0], 0.0, 1.0)
    v = np.clip(uv[:, 1], 0.0, 1.0)
    col = u * (w - 1)
    row = (1.0 - v) * (h - 1) if FLIP_V else v * (h - 1)
    coords = np.stack([row, col])
    channels = [
        map_coordinates(image_rgb[..., c].astype(np.float64), coords, order=1, mode="nearest")
        for c in range(3)
    ]
    rgb = np.stack(channels, axis=1)
    return np.clip(rgb, 0, 255).astype(np.uint8)


def extract_mesh_from_usd(usd_path: str = USD_PATH) -> MeshData:
    stage = Usd.Stage.Open(usd_path)
    if stage is None:
        raise FileNotFoundError(f"Could not open USD stage: {usd_path}")

    up_axis = str(UsdGeom.GetStageUpAxis(stage))
    print(f"[mussel_loader] stage upAxis={up_axis}")

    mesh_prims = _find_mesh_prims(stage)
    print(f"[mussel_loader] found {len(mesh_prims)} mesh prim(s) under {MUSSEL_ROOT_PATH}")
    if not mesh_prims:
        raise RuntimeError(f"No UsdGeom.Mesh prims found under {MUSSEL_ROOT_PATH}")

    texture_cache: dict[str, np.ndarray] = {}

    all_points = []
    all_faces = []
    all_colors = []
    offset = 0

    for prim in mesh_prims:
        mesh = UsdGeom.Mesh(prim)
        points_local = np.array(mesh.GetPointsAttr().Get(), dtype=np.float64)
        fvc = mesh.GetFaceVertexCountsAttr().Get()
        fvi = mesh.GetFaceVertexIndicesAttr().Get()

        points_world = _local_to_world(points_local, prim)
        faces_local = _fan_triangulate(fvc, fvi)

        primvars_api = UsdGeom.PrimvarsAPI(prim)
        uv_primvar = primvars_api.GetPrimvar("st")
        if uv_primvar is None or not uv_primvar.HasValue():
            uv_primvar = primvars_api.FindPrimvarWithInheritance("st")

        if uv_primvar is not None and uv_primvar.HasValue():
            flattened_uv = uv_primvar.ComputeFlattened()
            uv_per_point = _average_uv_per_point(len(points_local), fvi, flattened_uv)
        else:
            uv_per_point = None

        kind, value = _resolve_diffuse_source(prim)
        if kind == "texture":
            if value not in texture_cache:
                loaded = _load_texture_rgb(value)
                texture_cache[value] = loaded
            texture_rgb = texture_cache[value]
            if texture_rgb is not None and uv_per_point is not None:
                colors = _sample_texture_colors(texture_rgb, uv_per_point)
            else:
                colors = np.tile(_FALLBACK_COLOR, (len(points_local), 1))
        elif kind == "color":
            rgb = tuple(int(round(np.clip(c, 0, 1) * 255)) for c in value)
            colors = np.tile(np.array(rgb, dtype=np.uint8), (len(points_local), 1))
        else:
            colors = np.tile(_FALLBACK_COLOR, (len(points_local), 1))

        print(
            f"[mussel_loader]   {prim.GetPath()}: points={len(points_local)} "
            f"triangles={len(faces_local)} material_source={kind or 'none'}"
        )
        if uv_per_point is not None and len(uv_per_point):
            sample_n = min(3, len(uv_per_point))
            for i in range(sample_n):
                print(f"[mussel_loader]     sample uv={uv_per_point[i]} -> rgb={colors[i]}")

        all_points.append(points_world)
        all_faces.append(faces_local + offset)
        all_colors.append(colors)
        offset += len(points_local)

    vertices = np.concatenate(all_points, axis=0).astype(np.float32)
    faces = np.concatenate(all_faces, axis=0).astype(np.int32)
    vertex_colors = np.concatenate(all_colors, axis=0).astype(np.uint8)

    if up_axis == "Y":
        # Rotate +90deg about X: (x, y, z) -> (x, -z, y), a proper rotation
        # (determinant +1) that turns Y-up into Z-up.
        vertices = vertices[:, [0, 2, 1]] * np.array([1, -1, 1], dtype=np.float32)
        print("[mussel_loader] applied Y-up -> Z-up reorientation")

    print(f"[mussel_loader] TOTAL (pre-decimation) vertices={len(vertices)} triangles={len(faces)}")
    if len(faces) > DECIMATE_THRESHOLD_TRIANGLES:
        print(
            f"[mussel_loader] triangle count ({len(faces)}) exceeds "
            f"{DECIMATE_THRESHOLD_TRIANGLES} — decimating toward "
            f"{DECIMATE_TARGET_TRIANGLES} triangles for interactive performance.",
        )
        vertices, faces, vertex_colors = _decimate_to_target(
            vertices, faces, vertex_colors, DECIMATE_TARGET_TRIANGLES
        )
        print(f"[mussel_loader] TOTAL (post-decimation) vertices={len(vertices)} triangles={len(faces)}")

    rest_centroid = vertices.mean(axis=0)
    bounding_radius = float(np.linalg.norm(vertices - rest_centroid, axis=1).max())
    print(f"[mussel_loader] rest_centroid={rest_centroid} bounding_radius={bounding_radius:.4f}")

    return MeshData(
        vertices=vertices,
        faces=faces,
        vertex_colors=vertex_colors,
        rest_centroid=rest_centroid.astype(np.float32),
        bounding_radius=bounding_radius,
    )


def save_cache(mesh_data: MeshData, cache_path: str = CACHE_PATH) -> None:
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    np.savez(
        cache_path,
        vertices=mesh_data.vertices,
        faces=mesh_data.faces,
        vertex_colors=mesh_data.vertex_colors,
        rest_centroid=mesh_data.rest_centroid,
        bounding_radius=np.float32(mesh_data.bounding_radius),
    )
    print(f"[mussel_loader] wrote cache to {cache_path}")


def load_mesh(force_reconvert: bool = False, usd_path: str = USD_PATH, cache_path: str = CACHE_PATH) -> MeshData:
    if not force_reconvert and os.path.exists(cache_path):
        data = np.load(cache_path)
        return MeshData(
            vertices=data["vertices"],
            faces=data["faces"],
            vertex_colors=data["vertex_colors"],
            rest_centroid=data["rest_centroid"],
            bounding_radius=float(data["bounding_radius"]),
        )
    mesh_data = extract_mesh_from_usd(usd_path)
    save_cache(mesh_data, cache_path)
    return mesh_data


if __name__ == "__main__":
    mesh_data = load_mesh(force_reconvert=True)
    print("vertices dtype/shape:", mesh_data.vertices.dtype, mesh_data.vertices.shape)
    print("faces dtype/shape:", mesh_data.faces.dtype, mesh_data.faces.shape)
    print("vertex_colors dtype/shape:", mesh_data.vertex_colors.dtype, mesh_data.vertex_colors.shape)
    assert mesh_data.faces.shape[1] == 3
    assert mesh_data.vertex_colors.dtype == np.uint8
    assert mesh_data.vertex_colors.shape == mesh_data.vertices.shape
    print("OK")
