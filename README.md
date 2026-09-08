# Mussel 3D Visualizer

An interactive [Streamlit](https://streamlit.io/) app for visualizing a 3D
mussel model resting on, or partially buried in, a flat substrate ("sand").
Orientation (yaw/pitch/roll) and burial depth are controlled with sliders,
and the initial camera view and scene colors are configured via a separate
JSON file.

## Features

- Yaw / pitch / roll sliders to orient the mussel in 3D.
- A burial "Height" slider from `0` (resting on top of the substrate) to
  `-1` (fully buried under it).
- Live display of the currently selected parameters.
- A flat substrate plane rendered under the mussel.
- Camera framing and scene colors configured separately in
  [`render_config.json`](#render_configjson), independent of the sliders.

## How it works

The source 3D model ships as a binary [OpenUSD](https://openusd.org/)
(`.usdc`) file with an associated texture. Since USD isn't a convenient
format for a lightweight, browser-based viewer, the app converts it once
into a small cached NumPy array (`assets/mussel.npz`) — extracting the mesh
geometry and baking the texture into per-vertex colors — and renders that
cache with [Plotly](https://plotly.com/python/) `Mesh3d`. Conversion runs
automatically the first time the app starts (or whenever the cache is
missing) and is skipped on subsequent runs.

Because the source mesh is very high-resolution (~714k triangles), it is
also automatically decimated (voxel clustering) down to a triangle count
that stays responsive for interactive use — see `DECIMATE_TARGET_TRIANGLES`
in `mussel_loader.py` if you want to trade off shape detail against
responsiveness.

## Requirements

- Python 3.11+
- See [`requirements.txt`](requirements.txt) (Streamlit, Plotly, NumPy,
  SciPy, Pillow, and `usd-core` for reading the source `.usdc` model).

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

This opens the app in your browser (by default at
`http://localhost:8501`). The first run will take a bit longer while it
converts and caches the mesh; subsequent runs load the cache directly.

## Project structure

```
Model01/                 Source 3D model: Musselv1.usdc + textures/
mussel_loader.py          USD -> NumPy conversion + cache loading (assets/mussel.npz)
geometry.py                Pure-NumPy pose math: rotation, burial depth, substrate plane
render_plotly.py           Builds the Plotly figure from posed geometry + render_config.json
app.py                      Streamlit entrypoint: sliders, parameter display, layout
render_config.json          Camera + color configuration (see below)
requirements.txt
assets/                     Generated cache (gitignored, rebuilt automatically)
```

## `render_config.json`

Controls the camera's default view and the scene's colors. This is
independent of the sliders — it's read once per app rerun, but is not
itself a slider.

```json
{
  "camera": {
    "elevation_deg": 25.0,
    "azimuth_deg": 45.0,
    "distance_scale": 1.0
  },
  "substrate_color": "#C2B280",
  "background_color": "#FFFFFF",
  "substrate_size_scale": 2.2
}
```

| Field | Description |
|---|---|
| `camera.elevation_deg` | Camera angle above the substrate plane, in degrees. |
| `camera.azimuth_deg` | Camera angle around the vertical axis, in degrees. |
| `camera.distance_scale` | Multiplier on the default camera distance (empirically tuned; not literal world units). |
| `substrate_color` | Hex color of the substrate ("sand") plane. |
| `background_color` | Hex color of the scene background. |
| `substrate_size_scale` | How large the substrate plane is, relative to the mussel's bounding radius. |

The camera always points at the mussel's rest-pose center, so only the
elevation/azimuth/distance need to be set to frame the scene.

Edit this file and reload the app in your browser to see the changes — it
is not hot-reloaded automatically.

## Known limitations

- The mesh is decimated for performance, so fine surface detail is
  smoothed out compared to the original high-resolution scan.
- The texture is baked into per-vertex colors at conversion time (Plotly's
  `Mesh3d` has no true UV image-texture mapping), so texture sharpness is
  limited by the mesh's vertex density rather than the source image
  resolution.
- Mouse-based orbiting/zooming of the 3D view is disabled; the view is
  fixed to whatever `render_config.json` specifies.

## License

[MIT](LICENSE)
