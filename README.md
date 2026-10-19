# Mussel 3D Visualizer

An interactive [Streamlit](https://streamlit.io/) app for visualizing a 3D
mussel model resting on, or partially buried in, a flat substrate ("sand").
Drag the mussel to rotate it, fine-tune yaw/pitch/roll with sliders, and set
how deeply it is buried. The substrate and camera never move.

The goal is to match the 3D view to an underwater camera image shown next to
it. The app is also the custom front end of the
[Bionic Mussels](https://www.zooniverse.org/projects/coopsciscoop/bionic-mussels)
Zooniverse project, where volunteers pose the mussel for each image and submit
the pose as a classification.

## Features

- **Drag to rotate**: click and drag anywhere in the view to rotate the
  mussel trackball-style. The camera and substrate plane stay fixed.
- **Yaw / pitch / roll sliders** (plus numeric boxes), kept in sync with the drag.
- **Burial "Height"** slider from `0` (resting on top of the substrate) to
  `-1` (fully buried). At any orientation, height `0` means the mussel's
  lowest point touches the substrate.
- **Zooniverse integration**: volunteers get images from the Bionic Mussels
  workflow, optionally sign in with their Zooniverse account, and submit the
  pose as a classification (see [Zooniverse mode](#zooniverse-mode)).
- **Reference image side by side**: the underwater camera image (a
  Zooniverse subject, or `image_file` in local mode) is shown on the left, and
  the 3D view on the right uses the same aspect ratio so the two can be
  matched.
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
  `usd-core` for reading the source `.usdc` model, `requests` for the
  Zooniverse API, and `pytest` for the tests).

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
Without `.streamlit/secrets.toml` it runs in local mode, showing `image_file`
from `render_config.toml`; to run it against Zooniverse, set up the secrets as
described in [Zooniverse mode](#zooniverse-mode).

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
app.py                       Streamlit entrypoint: camera image + viewer (local or Zooniverse mode)
zooniverse.py                Panoptes HTTP client: OAuth, subjects, classifications
render_config.toml           Mode, camera + color configuration (see below)
sample_image.jpg             Example camera image for local mode
sample_substrate.jpg         Example substrate image
.streamlit/secrets.toml.example  Template for the Zooniverse secrets
tests/                       Unit tests (pytest, no network)
requirements.txt
```

## `render_config.toml`

Controls the image source, the fixed camera view, the scene's colors and the
viewer size. The file is [TOML](https://toml.io/) and documents every option
in comments:

```toml
mode = "zooniverse"
image_file = "sample_image.jpg"
substrate_mode = "image"
substrate_image = "sample_substrate.jpg"
substrate_color = "#C2B280"
substrate_size_scale = 2.2
background_color = "#FFFFFF"
viewer_height = 640

[camera]
elevation_deg = 25.0
azimuth_deg = 45.0
distance_scale = 1.0
```

| Field | Description |
|---|---|
| `mode` | `"zooniverse"` (default): use Zooniverse when its secrets are set, otherwise fall back to local mode. `"local"`: always use `image_file`, even when Zooniverse secrets exist. |
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

`mussel_viewer(...)` returns the last committed pose, or `None` before the
first interaction:

```python
{"yaw": 12.5, "pitch": -3.0, "roll": 40.0,
 "quaternion": {"w": 0.93, "x": 0.34, "y": -0.07, "z": 0.10},
 "height": -0.25, "token": "117025476"}
```

The returned value persists across reruns. To pose several things with one
viewer, pass `pose_token` (the app uses the Zooniverse subject id): when it
changes, the viewer resets to `initial_pose` without reloading the model.
`token` in the result is the `pose_token` that was active when the pose was
committed, so a pose whose `token` differs from the current one is stale.
Keep the same component `key` across tokens.

`geometry.rotation_matrix(pose)` turns it into a 3×3 matrix whose columns are
the mussel's body axes expressed in world coordinates.

## Zooniverse mode

With Zooniverse secrets configured (and `mode` left at `"zooniverse"`), the app
becomes a custom front end for the
[Bionic Mussels](https://www.zooniverse.org/projects/coopsciscoop/bionic-mussels)
project:

1. It fetches the next subject of the workflow from the Panoptes API and shows
   its image on the left. Each subject starts from the default pose.
2. Volunteers can **Log in with Zooniverse** (it opens in a new tab and returns
   there signed in) or classify as guests. Sign-in lasts for the browser
   session; refreshing the page or an expired token signs the volunteer out.
3. **Submit** sends the pose as a classification and loads the next subject.
   It is enabled once the mussel has been moved for the current subject.
   **Done & Talk** also submits and then offers a link to the subject's Talk
   page; **Skip** moves on without classifying.

Without the secrets, or with `"mode": "local"`, the app shows `image_file` and
submits nothing (local mode).

### Secrets

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill
it in (the file is git-ignored; never commit it):

```toml
[zooniverse_oauth]
client_id = "..."
client_secret = "..."
redirect_uri = "http://localhost:8501"

[zooniverse]
project_id = 31635
workflow_id = 31559
# project_slug = "coopsciscoop/bionic-mussels"   # optional, for Talk links
```

On Streamlit Community Cloud, paste the same content in the app's
**Settings → Secrets**, with `redirect_uri` set to the app's
`https://….streamlit.app/` URL.

`[zooniverse]` alone enables Zooniverse mode for guests; `[zooniverse_oauth]`
adds sign-in. The `redirect_uri` must match one of the Redirect URIs of the
OAuth application on Zooniverse **exactly** (scheme, host, port and trailing
slash), or Zooniverse rejects the sign-in.

### Classification format

Each classification answers task `T0` with `0`, the index of its only answer,
"Pose matched". The pose itself is in `metadata.pose`: the workflow
classification export translates single-choice values to answer labels (and
shows anything else as "unknown answer label"), while it keeps the metadata
column intact.

```json
{
  "annotations": [{"task": "T0", "value": 0}],
  "metadata": {
    "workflow_version": "2.5",
    "started_at": "…", "finished_at": "…",
    "user_language": "en", "utc_offset": "0",
    "user_agent": "Mussel-Visualizer/1.0 (Streamlit)",
    "source": "mussel-visualizer",
    "subject_dimensions": [{"naturalWidth": 1920, "naturalHeight": 1080,
                            "clientWidth": null, "clientHeight": null}],
    "pose": {"quaternion": {"w": 0.93, "x": 0.34, "y": -0.07, "z": 0.10},
             "yaw": 12.5, "pitch": -3.0, "roll": 40.0, "height": -0.25},
    "pose_convention": "intrinsic ZYX Euler (deg); quaternion is authoritative; height in [-1, 0]",
    "camera": {"elevation_deg": 25.0, "azimuth_deg": 45.0, "distance_scale": 1.0}
  },
  "links": {"project": "31635", "workflow": "31559", "subjects": ["117025476"]},
  "completed": true
}
```

`workflow_version` is read from the API when the session starts. `camera` is
the camera used while posing (from `render_config.toml`), so the pose can be
interpreted even if the configuration changes later.

### Known limitations

- The OAuth flow does not use a `state` parameter (the sign-in returns in a
  new browser session, so there is nowhere to keep it).
- In the project's development mode subjects never retire, and guests get
  random subjects; the app avoids repeating subjects within a session only.

### Tests

```bash
python -m pytest
```

The tests mock all HTTP calls, so they need no network or secrets.

## License

[MIT](LICENSE)
