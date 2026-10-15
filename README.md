# Mussel 3D Visualizer

An interactive [Streamlit](https://streamlit.io/) app for visualizing a 3D
mussel model resting on, or partially buried in, a flat substrate ("sand").
Drag the mussel to rotate it, fine-tune yaw/pitch/roll with sliders, and set
how deeply it is buried. The substrate and camera never move.

## Features

- **Drag to rotate**: click and drag anywhere in the view to rotate the
  mussel trackball-style. The camera and substrate plane stay fixed.
- **Yaw / pitch / roll sliders** (plus numeric boxes), kept in sync with the drag.
- **Burial "Height"** slider from `0` (resting on top of the substrate) to
  `-1` (fully buried). At any orientation, height `0` means the mussel's
  lowest point touches the substrate.
- **Reference image side by side**: the underwater camera image set by
  `image_file` is shown on the left, and the 3D view on the right uses the
  same aspect ratio so the two can be matched.
- Substrate as a solid color or as an image (e.g. a photo of the sediment).
- Options for a see-through substrate (to see the buried part) and the
  mussel's body axes.
- Full-resolution mesh (~714k triangles) with the real texture.

## How it works

Rendering runs in the browser with [three.js](https://threejs.org/), inside a
Streamlit custom component. The mesh is uploaded to the GPU once. After that,
rotating the mussel or changing its burial depth only updates its transform,
so interaction runs at full frame rate with no Python round trip. Streamlit
reruns only when an interaction *ends* and the component reports the new pose.

The source model is a binary [OpenUSD](https://openusd.org/) (`.usdc`) file.
`mussel_loader.py` converts it once into web-friendly assets in
`mussel_viewer/frontend/assets/`:

- `mussel.glb`: textured glTF binary, centered on the mussel's rest-pose
  center (the pivot for rotations).
- `mussel_meta.json`: a few thousand convex-hull support points. The viewer
  uses these to find the lowest/highest point after any rotation, which drives
  the rest-on-substrate and burial logic.

These generated files are committed to the repo, so the app never has to
convert at startup. That matters for deployment: conversion takes ~4 GB of
RAM, more than Streamlit Community Cloud provides. If the files are missing,
the app converts automatically (locally this takes a few seconds).

### Conventions

- World frame: Z up; the substrate is the plane z = 0.
- Euler angles: intrinsic ZYX, i.e. `R = Rz(yaw) · Ry(pitch) · Rx(roll)`, the
  same as scipy `Rotation.from_euler("ZYX", [yaw, pitch, roll], degrees=True)`.
  Pitch is limited to ±90°. Near ±90°, yaw and roll become coupled (gimbal
  lock), and the viewer warns you. The quaternion is always unambiguous.
- Height: the mussel sinks by `|height|` × its current vertical extent.

## Requirements

- Python 3.11+
- A browser with WebGL (any modern browser), and internet access to load
  three.js from cdn.jsdelivr.net.
- See [`requirements.txt`](requirements.txt) (Streamlit, NumPy, SciPy, Pillow,
  and `usd-core` for reading the source `.usdc` model).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Running the app

```bash
streamlit run app.py
```

This opens the app in your browser (by default at `http://localhost:8501`).

To regenerate the viewer assets, for example after changing the model or
the conversion settings, run the following and commit the updated files in
`mussel_viewer/frontend/assets/`:

```bash
python mussel_loader.py
```

## Project structure

```
Model01/                     Source 3D model: Musselv1.usdc + textures/
mussel_loader.py             USD -> mussel.glb + mussel_meta.json conversion
mussel_viewer/__init__.py    Streamlit component wrapper: mussel_viewer(...) -> pose
mussel_viewer/frontend/      three.js viewer (plain HTML/JS/CSS, no build step)
  assets/                    Generated GLB + metadata (committed; regenerate with mussel_loader.py)
geometry.py                  Python pose helpers (quaternion/Euler -> rotation matrix)
app.py                       Streamlit entrypoint: camera image + viewer
render_config.json           Camera + color configuration (see below)
requirements.txt
```

## `render_config.json`

Controls the fixed camera view, the scene's colors and the viewer size.

```json
{
  "image_file": "sample_image.jpg",
  "camera": {
    "elevation_deg": 25.0,
    "azimuth_deg": 45.0,
    "distance_scale": 1.0
  },
  "substrate_mode": "image",
  "substrate_image": "sample_substrate.jpg",
  "substrate_color": "#C2B280",
  "background_color": "#FFFFFF",
  "substrate_size_scale": 2.2,
  "viewer_height": 640
}
```

| Field | Description |
|---|---|
| `image_file` | Camera image shown on the left, relative to the project folder. The 3D view takes its aspect ratio. |
| `camera.elevation_deg` | Camera angle above the substrate plane, in degrees. |
| `camera.azimuth_deg` | Camera angle around the vertical axis, in degrees. |
| `camera.distance_scale` | Multiplier on the default camera distance (6.8 × the mussel's bounding radius). |
| `camera.target` | Optional `[x, y, z]` point the camera looks at (default `[0, 0, 0]`). |
| `substrate_mode` | `"color"` for a solid `substrate_color`, or `"image"` to cover the substrate with `substrate_image`. |
| `substrate_image` | Image stretched once over the substrate plane (no tiling), relative to the project folder. The plane takes the image's aspect ratio. Used when `substrate_mode` is `"image"`; if the file is missing, the app warns and uses the color. |
| `substrate_color` | Hex color of the substrate ("sand") plane in `"color"` mode. |
| `background_color` | Hex color of the scene background. |
| `substrate_size_scale` | Half-width of the substrate plane (its longer side, for an image), relative to the mussel's bounding radius. |
| `viewer_height` | Height of the viewer in pixels, used only when no image is shown. |

Edit this file and reload the app in your browser to see the changes. The
current pose is kept.

## Using the pose in Python

`mussel_viewer(...)` returns the last committed pose:

```python
{"yaw": 12.5, "pitch": -3.0, "roll": 40.0,
 "quaternion": {"w": 0.93, "x": 0.34, "y": -0.07, "z": 0.10},
 "height": -0.25}
```

`geometry.rotation_matrix(pose)` turns it into a 3×3 matrix whose columns are
the mussel's body axes expressed in world coordinates.

## License

[MIT](LICENSE)
