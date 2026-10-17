# Xense tactile: live dashboard, recording, FiftyOne and robosimstudio

Three Python environments, joined by Socket.IO and files, never imported into each other:

| Environment | Python | Runs |
| --- | --- | --- |
| `~/fira-station/.venv` | 3.10 (Xense SDK) | `xense_bridge.py` |
| `.venv-fiftyone` | 3.11 | `load_fiftyone_tactile.py` |
| robosimstudio checkout (`uv`) | 3.12, NVIDIA GPU | `robosim_tactile.py`, `robosim_trossen.py` |
| any with `trossen-arm` | the driver's | `trossen_joint_bridge.py` |

```text
Xense sensor ──> xense_bridge.py ──Socket.IO──> server.js ──> dashboard "Tactile" tab
                      │                             └──────> robosim_tactile.py --live
                      └── --record ──> episode dir ──> load_fiftyone_tactile.py
                                                  └──> robosim_tactile.py --episode
Trossen follower ──> trossen_joint_bridge.py ──Socket.IO──> server.js ──> robosim_trossen.py --live
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

## Trossen WidowX AI follower + Xense in robosimstudio

`robosim_trossen.py` registers the follower from Trossen's own
[`wxai_follower.urdf`](https://github.com/TrossenRobotics/trossen_arm_description)
(BSD-3-Clause; read from your checkout, not copied here), puts a Xense pad on the
left finger, and adds a **Follower mirror** panel next to the tactile panel.

```bash
git clone https://github.com/TrossenRobotics/trossen_arm_description.git ~/trossen_arm_description

# 1. follower joints -> base station (read-only; see below)
uv pip install trossen-arm "python-socketio[client]"
python tools/trossen_joint_bridge.py --ip <follower-ip>          # or --fake to test

# 2. Xense -> base station
python tools/xense_bridge.py --serial OG000741

# 3. the studio
cd ~/robosimstudio
uv run --with "python-socketio[client]" --with opencv-python-headless \
    python ~/fira-station/tools/robosim_trossen.py \
    --description ~/trossen_arm_description --live http://localhost:3000
```

Tick **Mirror real follower** to drive the simulated arm from the real one; untick,
or let the stream stop for 0.5 s, and the arm goes back to its gizmo where it is.

**Reading the follower.** `trossen_joint_bridge.py` calls only `configure()` and
`get_all_positions()`: no mode, position, velocity or effort is ever sent and
errors are not cleared. Whether the controller accepts this second connection
while your teleop program holds the first is not documented by Trossen. If it is
refused, publish from inside the teleop loop instead:

```python
from trossen_joint_bridge import JointStatePublisher
pub = JointStatePublisher("http://localhost:3000")
pub.send(follower_driver.get_all_positions())   # each loop iteration
```

**From Trossen's files:** joint names, limits and axes, meshes, the carriage
mimic, the 0..0.044 m finger travel (88 mm between finger faces at full open)
and the `ee_gripper` tool frame 156 mm along `link_6` x. `home_q` is Trossen's
leader home pose.

**Not measured, stated as such:** the Xense pad position and size
(`--xense-offset`, `--xense-size`; the default sits on the left finger's inner
face, visual only, no collider, so it does not change grasp physics), the
sensor-to-finger rotation (`--mount-rpy`, default gel normal facing the other
finger), gripper pad dimensions (0, robosimstudio's convention for unmeasured),
servo gains (robosimstudio defaults), and where the base stands (`--base`).
The mirror writes joint *targets*; the simulated arm follows through its own
servo, lags the real one, and can be blocked by simulated contact the real arm
never met.
