# Xense tactile: live dashboard, recording, FiftyOne and robosimstudio

Three Python environments, joined by Socket.IO and files, never imported into each other:

| Environment | Python | Runs |
| --- | --- | --- |
| `~/fira-station/.venv` | 3.10 (Xense SDK) | `xense_bridge.py` |
| `.venv-fiftyone` | 3.11 | `load_fiftyone_tactile.py` |
| robosimstudio checkout (`uv`) | 3.12, NVIDIA GPU | `robosim_tactile.py` |

```text
Xense sensor ──> xense_bridge.py ──Socket.IO──> server.js ──> dashboard "Tactile" tab
                      │                             └──────> robosim_tactile.py --live
                      └── --record ──> episode dir ──> load_fiftyone_tactile.py
                                                  └──> robosim_tactile.py --episode
```

## Bridge and recording

```bash
source ~/fira-station/.venv/bin/activate
uv pip install "python-socketio[client]"
python tools/xense_bridge.py --serial OG000741                                  # live only
python tools/xense_bridge.py --serial OG000741 --record episodes/press-01       # live + record
python tools/xense_bridge.py --serial OG000741 --record episodes/press-01 --no-server
python tools/xense_bridge.py --fake --record episodes/fake-01 --no-server       # no hardware
```

Ctrl+C ends a recording and writes the final manifest. An episode without
`frame_count` in its manifest was not closed cleanly and is refused by the loaders.
Each recording directory must be new.

### Episode schema: `xense-tactile-v1`

| File | Content |
| --- | --- |
| `manifest.json` | `episode_id`, `serial`, `data_kind` (`real` / `synthetic_fixture`), `sdk_version`, units, rates, counts |
| `frames.jsonl` | `i`, `t` (unix s), `force` [Fx Fy Fz Mx My Mz], `depth_max`, `contact`, and `image` on image rows |
| `images/NNNNNN_rectify.jpg` | rectified camera image |
| `images/NNNNNN_depth.npz` | depth, float32, key `depth`, as the SDK returns it |
| `images/NNNNNN_depth.png` | depth colour map at `depth_display_scale`, display only |

Force, torque and depth units are recorded as `sdk_unverified`: they are what
the SDK reports and have not been checked against a reference load. Forces are
in the sensor's frame (`xense_sensor_uncalibrated`). `contact` is `|Fz| >
--contact-force` (default 0.5), a display threshold, not a validated detector.
Image and force on an image row come from the same loop iteration. At the
default rates a real episode is several MB per second; depth `.npz` dominates.

## FiftyOne (Voxel51)

```bash
uv venv --python 3.11 .venv-fiftyone
source .venv-fiftyone/bin/activate
uv pip install -r tools/requirements-fiftyone.txt
python tools/load_fiftyone_tactile.py episodes/press-01 episodes/press-02 --name xense-press
python tools/load_fiftyone_tactile.py --existing --name xense-press     # reopen later
```

Open http://127.0.0.1:5151. Each group is one image frame with slices `rectify`
(with a `depth` heatmap overlay) and `depth`. Sidebar fields: `fx`..`mz`,
`force_norm`, `depth_max`, `contact`, `t_rel`, `episode_id`, `data_kind`; tags
`contact` / `no_contact`. Filter for example `fz < -5` to see hard presses.
The importer refuses an existing dataset name and duplicate episode ids; media
stay in the episode directories, so do not move them after import. On the OMEN
over SSH, forward the port (`ssh -L 5151:localhost:5151`) rather than exposing it.

## robosimstudio

```bash
git clone https://github.com/robosimstudio/robosimstudio.git ~/robosimstudio
cd ~/robosimstudio && uv sync
uv run --with "python-socketio[client]" --with opencv-python-headless \
    python ~/fira-station/tools/robosim_tactile.py --live http://localhost:3000
uv run --with opencv-python-headless \
    python ~/fira-station/tools/robosim_tactile.py --episode ~/fira-station/episodes/press-01
```

Open http://localhost:8080. The script builds the hello-world bench (Panda and a
cube) and adds, through `rss.view(sim, page=...)`, an "Xense tactile" panel and a
force arrow at the arm's tool-centre point (red while in contact). `--robot`,
`--arm`, `--cell` choose the scene; `--arrow-scale` the metres per force unit.

What it is not: the simulated arm does not follow the real arm, the measured
force is not applied to the simulation, and the arrow's direction is the raw
sensor vector rotated by the tool pose and `--mount-rpy` (identity by default),
which is not a calibrated mount. Magnitude and timing are faithful; direction
is only as good as `--mount-rpy`. No sim-to-real or grasp-success claim follows
from this view.
