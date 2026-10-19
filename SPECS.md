# SPECS: Connect Mussel Visualizer to Zooniverse

This file specifies the changes that turn the Mussel 3D Visualizer into a custom
Zooniverse front end for the **Bionic Mussels** project. Volunteers get an image
from Zooniverse, pose the 3D mussel to match it, and submit the pose as a
Zooniverse classification.

Read this whole file before changing code. Keep the existing behavior and
conventions described in `README.md` unless this file says otherwise.

---

## 1. Context

- **App:** this repo (`ARoS-NCSU/Mussel-Visualizer`). It is a Streamlit app with a
  three.js custom component (`mussel_viewer/`) and is hosted on Streamlit
  Community Cloud at
  `https://mussel-visualizer-9kryg2pki7uvjswey3qzqu.streamlit.app/`.
- **Zooniverse back end:** Panoptes REST API at `https://panoptes.zooniverse.org`.
  Use plain HTTP calls (`requests`), **not** the `panoptes-client` package. That
  client logs in with a username and password and does not support the volunteer
  OAuth flow.
- **Reference implementation:** the Solar Jet Hunter app
  (`https://github.com/somusset/SJH_IFE_time-distance`, file
  `app4_time-distance.py`) does the same thing for another project. Its functions
  `get_next_subject`, `get_oauth_login_url`, `exchange_code_for_token`,
  `get_authenticated_user` and `create_classification` are the model for this
  work. Port the approach; do not copy its UI.

### Zooniverse project facts (already set up; do not change them from code)

| Item | Value |
|---|---|
| Project | Bionic Mussels, id `31635`, slug `coopsciscoop/bionic-mussels` |
| Workflow | Mussel Orientation, id `31559`, currently **inactive** (team members have the Tester role and can classify on it) |
| Workflow task | `T0`, a single-choice question ("Rotate and sink the 3D mussel so it matches the mussel in the photo."), one answer "Pose matched" |
| Subject set | Underwater/Side View – Test, id `133757`, 234 subjects |
| Subjects | One JPEG each, **one mussel per image**, all from camera `cam1`. `locations[0]["image/jpeg"]` is the image URL. `metadata` has only `Filename`. |
| OAuth application | "Bionic Mussels Orientation App", scopes `user subject classification` (plus `public`), redirect URIs: the streamlit.app URL with and without trailing slash, and `http://localhost:8501` |

### Secrets (provided by the user; never hard-code or log them)

Streamlit secrets (`st.secrets`). On Community Cloud they are set in the app's
Settings → Secrets; locally they are in `.streamlit/secrets.toml`:

```toml
[zooniverse_oauth]
client_id = "..."
client_secret = "..."
redirect_uri = "https://mussel-visualizer-9kryg2pki7uvjswey3qzqu.streamlit.app/"   # locally: "http://localhost:8501"

[zooniverse]
project_id = 31635
workflow_id = 31559
```

---

## 2. Goals

1. Fetch the next subject for the workflow from Zooniverse and show its image on
   the left, replacing the local `image_file`.
2. Start each subject with a fresh mussel pose.
3. Let volunteers sign in with their Zooniverse account (optional; guests can
   still try the app).
4. Submit the pose as a classification for task `T0`, then load the next subject.
5. Provide "Done & Talk" so volunteers can discuss a subject on Zooniverse Talk.
6. Keep a **local mode**: with no `[zooniverse]` secrets, the app behaves exactly
   as it does today, using `render_config.json`'s `image_file`.

## 3. Non-goals

- No multi-mussel selection, bounding boxes or highlighting. Images contain one
  mussel; any highlighting is done in preprocessing before upload.
- No per-subject camera parameters. The camera stays fixed in
  `render_config.json`. (Design hook only; see §9.)
- No uploading of subjects, editing of workflows or exporting of data from the
  app.
- No changes to the 3D model, `mussel_loader.py` or the rendering/geometry
  conventions.

---

## 4. New module: `zooniverse.py`

A small, UI-free module containing all Panoptes HTTP code. Every function takes
explicit arguments (no `st.` calls inside), so it can be unit-tested.

```python
PANOPTES_URL = "https://panoptes.zooniverse.org"
API_HEADERS = {"Accept": "application/vnd.api+json; version=1",
               "Content-Type": "application/json"}
OAUTH_SCOPE = "public user subject classification"
TIMEOUT = 15  # seconds, on every request
```

Functions:

- `login_url(client_id, redirect_uri) -> str`
  Builds `{PANOPTES_URL}/oauth/authorize?response_type=code&client_id=…&redirect_uri=…&scope=…`.
- `exchange_code(code, client_id, client_secret, redirect_uri) -> dict`
  POSTs to `{PANOPTES_URL}/oauth/token` with `grant_type=authorization_code`.
  Returns the token JSON (`access_token`, `expires_in`, maybe `refresh_token`).
- `get_me(token) -> dict | None`
  `GET /api/me`, returns `users[0]` or `None`.
- `get_workflow(workflow_id, token=None) -> dict`
  `GET /api/workflows/{id}`, returns `workflows[0]`. Used for `version`.
- `get_next_subject(workflow_id, token=None, exclude_ids=()) -> dict | None`
  `GET /api/subjects/queued?workflow_id=…&page_size=10`. Returns the first
  subject whose `id` is not in `exclude_ids`, else `None`. (The project is in
  development mode where subjects never retire, and guests get random subjects,
  so `exclude_ids` stops the app from repeating subjects within a session.)
- `subject_image_url(subject) -> str | None`
  First `locations` entry whose MIME type starts with `image/`.
- `create_classification(*, project_id, workflow_id, workflow_version, subject_id, pose, started_at, finished_at, subject_dimensions, token=None) -> dict`
  POSTs to `/api/classifications`; payload in §6. Adds
  `Authorization: Bearer {token}` only when a token is given.
- `talk_url(project_slug, subject_id) -> str`
  `https://www.zooniverse.org/projects/{slug}/talk/subjects/{subject_id}`.

Errors: raise a single `ZooniverseError(message, status_code)` for non-2xx
responses and network failures, with the response body (truncated to ~500
characters) in the message. Never include tokens or secrets in messages.

---

## 5. App flow (`app.py`)

### 5.1 Mode selection
- **Zooniverse mode** if `st.secrets` contains a `zooniverse` section; otherwise
  **local mode** (current behavior, unchanged). Guard every `st.secrets` access
  so a missing `secrets.toml` does not crash local mode.
- Sign-in is available only when `zooniverse_oauth` is also present.

### 5.2 Session state (Zooniverse mode)
| Key | Meaning |
|---|---|
| `zoo_token` | access token or `None` |
| `zoo_user` | display name / login or `None` (guest) |
| `subject` | current subject dict |
| `seen_ids` | list of subject ids shown this session |
| `started_at` | ISO-8601 UTC time the current subject was shown |
| `workflow_version` | from `get_workflow`, cached once per session |
| `last_result` | short status message from the previous submit |

### 5.3 OAuth callback
At the top of the run, if `st.query_params` has `code` and `zoo_token` is unset:
exchange it, store token and user, then **clear the query params** so a page
refresh does not re-use the code. Drop the current subject so the next one is
fetched with the token (signed-in volunteers get subjects they haven't
classified). On failure show `st.error` with a "Try again" sign-in link.
If a later API call returns 401, clear the token and ask the volunteer to sign
in again.

### 5.4 Layout
- **Header row:** title; on the right either "Signed in as **{user}**" with a
  "Sign out" button (clears token and user only), or a "Log in with Zooniverse"
  `st.link_button` plus the hint "You can also try it as a guest."
- **Instructions:** a collapsible `st.expander("How to classify")` with the
  task's main and help text. Read these from `workflow["tasks"]["T0"]`
  (`question`, `help`), falling back to constants if missing.
- **Two columns** as today: subject image on the left (caption: "Subject
  {id}"), viewer on the right with `aspect` set from the image.
- **Action row** under the columns:
  - **Submit** (primary). Disabled until the volunteer has interacted with the
    mussel for this subject (§7).
  - **Done & Talk**: submits like Submit, then shows a `st.link_button` to the
    subject's Talk page. Streamlit cannot open a tab on its own, so the link
    button is shown along with the next subject.
  - **Skip**: loads the next subject without classifying.
- After a successful submit: `st.toast("Classification submitted. Thank you!")`,
  add the id to `seen_ids`, load the next subject and reset `started_at`.
- When `get_next_subject` returns `None`: show "You've seen all the available
  images, thank you!" and stop.

### 5.5 Images
- Download the subject image with `requests` and open it with PIL. Cache with
  `st.cache_data` keyed by the URL (`max_entries≈32`). Reuse the existing
  `DISPLAY_MAX_SIDE` thumbnailing.
- Record the image's original `naturalWidth`/`naturalHeight` for
  `subject_dimensions` before thumbnailing.
- On a download failure, show a warning with a "Skip" button.

---

## 6. Classification payload

```json
{
  "classifications": {
    "annotations": [
      {
        "task": "T0",
        "value": {
          "quaternion": {"w": 0.93, "x": 0.34, "y": -0.07, "z": 0.10},
          "yaw": 12.5, "pitch": -3.0, "roll": 40.0,
          "height": -0.25
        }
      }
    ],
    "metadata": {
      "workflow_version": "<workflow.version from the API>",
      "started_at": "<ISO-8601 UTC>",
      "finished_at": "<ISO-8601 UTC>",
      "user_language": "en",
      "utc_offset": "0",
      "user_agent": "Mussel-Visualizer/1.0 (Streamlit)",
      "source": "mussel-visualizer",
      "subject_dimensions": [{"naturalWidth": 1920, "naturalHeight": 1080,
                               "clientWidth": null, "clientHeight": null}],
      "pose_convention": "intrinsic ZYX Euler (deg); quaternion is authoritative; height in [-1, 0]",
      "camera": {"elevation_deg": 25.0, "azimuth_deg": 45.0, "distance_scale": 1.0}
    },
    "links": {
      "project": "31635",
      "workflow": "31559",
      "subjects": ["<subject id>"]
    },
    "completed": true
  }
}
```

Notes:
- IDs are strings in `links`.
- `workflow_version` must come from the API, not a constant. The workflow is at
  2.5 now and changes whenever it is edited.
- `camera` copies the `render_config.json` camera block used when posing, so the
  pose can be interpreted later even if the config changes.
- The annotation `value` is an object, not an answer index. Panoptes accepts
  this; the team will analyze it from the classification export.

---

## 7. Viewer changes (`mussel_viewer/`)

Problem: `initial_pose` is applied only on the component's first load, and the
returned value is `None` until the first interaction and then persists across
reruns. With one component instance across many subjects, the pose would carry
over and a stale pose could be submitted.

Required changes:

1. **New argument `pose_token: str | None`** on `mussel_viewer(...)`, passed
   through to the frontend. Use the subject id.
2. **Frontend (`main.js`)**: on each `streamlit:render`, if `args.pose_token`
   differs from the last seen token, re-apply `args.initial_pose` (same logic as
   in `init`, refactored into a `resetPose(pose)` helper), sync the sliders and
   re-render. Do **not** reload the model.
3. **Echo the token**: `currentPose()` includes `token: state.poseToken`.
4. **App side**: treat the returned pose as valid for the current subject only
   if `pose["token"] == str(subject_id)`. Otherwise Submit stays disabled
   ("Rotate or adjust the mussel first.").

Keep the same component `key` across subjects so the GLB is not re-downloaded.
Update the docstring in `mussel_viewer/__init__.py` and the "Using the pose in
Python" section of `README.md`.

---

## 8. Files to add or change

- `zooniverse.py`: new (§4).
- `app.py`: Zooniverse mode (§5), local mode unchanged.
- `mussel_viewer/__init__.py`, `mussel_viewer/frontend/main.js`: §7.
- `requirements.txt`: add `requests>=2.31,<3`.
- `.gitignore`: add `.streamlit/secrets.toml`.
- `.streamlit/secrets.toml.example`: template from §1 with placeholder values.
- `README.md`: add a "Zooniverse mode" section covering secrets (local and
  Community Cloud), local vs Zooniverse mode, the classification format (§6), and
  the note that the Redirect URI on the Panoptes OAuth application must match
  `redirect_uri` exactly.
- `tests/test_zooniverse.py`: unit tests (§10).

Do not commit `.streamlit/secrets.toml`, tokens, or the client secret.

---

## 9. Design hooks for later (implement only the hook)

- **Per-subject camera:** add `camera_for_subject(subject, render_config) -> dict`
  that returns `render_config["camera"]` today. Later it may read
  `#camera_elevation_deg` / `#camera_azimuth_deg` (or `#camera`) from subject
  metadata. Use it both for the viewer config and for the `camera` block in §6.
- **Workflow switching:** read the project and workflow IDs only from
  `st.secrets["zooniverse"]` (with an optional `project_slug`, default
  `coopsciscoop/bionic-mussels`), never from literals in `app.py`.

---

## 10. Testing and acceptance

### Unit tests (`pytest`, no network)
Mock `requests` (e.g. with `unittest.mock`) to test:
- `login_url` encodes all parameters and the scope.
- `get_next_subject` skips ids in `exclude_ids` and returns `None` on an empty list.
- `subject_image_url` picks the first `image/*` location.
- `create_classification` builds exactly the payload in §6 (string IDs, task `T0`,
  `completed: true`) and adds the auth header only with a token.
- Non-2xx responses raise `ZooniverseError` without leaking the token.

### Manual checks (run with `streamlit run app.py`)
1. **Local mode:** temporarily move `.streamlit/secrets.toml` away; the app
   behaves as before.
2. **Guest:** with secrets present, a subject image from Zooniverse loads; Submit
   is disabled until the mussel is moved; Skip loads a different subject.
   (Guest submits to an inactive workflow may be rejected by Zooniverse; the
   app must show the error clearly and keep the pose.)
3. **Sign-in:** "Log in with Zooniverse" → approve → back in the app with
   "Signed in as …" and no `?code=` in the address bar. Refreshing the page does
   not error.
4. **Submit:** after posing and submitting, a toast appears, the next subject
   loads with the mussel back at the default pose, and the classification is
   visible on Zooniverse (e.g. the volunteer's Recents at
   `https://www.zooniverse.org/projects/coopsciscoop/bionic-mussels/recents`).
5. **Done & Talk:** submits, then a link opens the subject's Talk page.
6. **Hosted:** the same flow works on the streamlit.app URL with Community Cloud
   secrets.

---

## 11. Suggested order of work

1. `zooniverse.py` + unit tests.
2. Viewer `pose_token` change; verify locally in local mode that nothing regresses.
3. `app.py` Zooniverse mode without sign-in (guest fetch + display + Skip).
4. Sign-in flow.
5. Submit and Done & Talk.
6. README, `.gitignore`, secrets example; manual checks in §10.

Work on a branch (e.g. `zooniverse-integration`) and open a pull request.
