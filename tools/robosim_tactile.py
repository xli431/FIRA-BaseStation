"""Show Xense tactile data inside a robosimstudio studio page.

Adds an "Xense tactile" panel (force/torque, depth_max, contact, the rectified
and depth images) and a force arrow drawn at the arm's tool-centre point, using
the studio's own extension seam: rss.view(sim, page=...). No robosimstudio code
is changed.

Two sources:
  --live URL      the base station's Socket.IO server; run xense_bridge.py beside it
  --episode DIR   a recorded xense-tactile-v1 episode, replayed in a loop

Runs in robosimstudio's environment (Python >= 3.12, NVIDIA GPU), not the
Python 3.10 venv the Xense SDK lives in; the two meet over Socket.IO or files.

    cd ~/robosimstudio
    uv run --with "python-socketio[client]" --with opencv-python-headless \\
        python ~/fira-station/tools/robosim_tactile.py --live http://localhost:3000
    uv run --with opencv-python-headless \\
        python ~/fira-station/tools/robosim_tactile.py --episode ~/fira-station/episodes/press-01

The arrow is the sensor's raw force vector rotated by the tool pose and by
--mount-rpy, which defaults to identity. That mounting transform is not
calibrated: the arrow shows magnitude and timing faithfully, and direction only
as well as --mount-rpy matches the real mount. The simulated arm does not follow
the real one and the force is not applied to the simulation.
"""
import argparse
import base64
import bisect
import math
import threading
import time

import cv2
import numpy as np

LABELS = ["Fx", "Fy", "Fz", "Mx", "My", "Mz"]
MAX_ARROW_M = 0.3


def decode_jpeg_b64(data):
    buf = np.frombuffer(base64.b64decode(data), np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return None if img is None else cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def read_rgb(path, width):
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    if w > width:
        img = cv2.resize(img, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def quat_xyzw_to_mat(q):
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def rpy_to_mat(roll, pitch, yaw):
    r, p, y = (math.radians(a) for a in (roll, pitch, yaw))
    rx = np.array([[1, 0, 0], [0, math.cos(r), -math.sin(r)], [0, math.sin(r), math.cos(r)]])
    ry = np.array([[math.cos(p), 0, math.sin(p)], [0, 1, 0], [-math.sin(p), 0, math.cos(p)]])
    rz = np.array([[math.cos(y), -math.sin(y), 0], [math.sin(y), math.cos(y), 0], [0, 0, 1]])
    return rz @ ry @ rx


def connect_in_background(sio, url):
    """Keep trying to reach the base station from a thread, so the studio starts whether or not it is up yet."""
    import socketio

    def run():
        while not sio.connected:
            try:
                sio.connect(url, wait_timeout=5)
            except socketio.exceptions.ConnectionError:
                time.sleep(2)

    threading.Thread(target=run, daemon=True).start()


class LiveSource:
    """Latest message from the base station. Callbacks only assign; decoding happens on the studio's thread."""

    def __init__(self, url):
        import socketio

        self.label = f"live {url}"
        self.msg = None
        self.connected = False
        self.sensor = None
        self._seen = None
        self._rectify = None
        self._depth = None
        sio = socketio.Client(reconnection=True, reconnection_delay=1)
        sio.on("connect", lambda: setattr(self, "connected", True))
        sio.on("disconnect", lambda: setattr(self, "connected", False))
        sio.on("tactile_to_browser", lambda data: setattr(self, "msg", data))
        sio.on("tactile_status", lambda data: setattr(self, "sensor", data))
        connect_in_background(sio, url)
        self.sio = sio

    def status(self):
        if not self.connected:
            return "base station not reachable"
        if not (self.sensor or {}).get("connected"):
            return "base station up, no sensor bridge"
        return f"sensor {self.sensor.get('serial', '?')}"

    def latest(self):
        msg = self.msg
        if msg is None:
            return None
        if msg is not self._seen:
            self._seen = msg
            if msg.get("rectify_frame"):
                self._rectify = decode_jpeg_b64(msg["rectify_frame"])
            if msg.get("depth_frame"):
                self._depth = decode_jpeg_b64(msg["depth_frame"])
        return dict(force=msg["force"], depth_max=msg["depth_max"], contact=msg["contact"],
                    rectify=self._rectify, depth=self._depth)


class EpisodeSource:
    """A recorded episode replayed at its own timing, looping."""

    def __init__(self, folder, img_width):
        from xense_episode import load_episode

        self.manifest, self.frames = load_episode(folder)
        if not self.frames:
            raise ValueError(f"{folder}: episode has no frames")
        self.label = f"episode {self.manifest['episode_id']} ({self.manifest['data_kind']})"
        t0 = self.frames[0]["t"]
        self.times = [row["t"] - t0 for row in self.frames]
        self.duration = max(self.times[-1], 1e-3)
        self.image_rows = [k for k, row in enumerate(self.frames) if "image" in row]
        self.img_width = img_width
        self.start = time.time()
        self._loaded = None
        self._rectify = self._depth = None

    def status(self):
        return f"replay {(time.time() - self.start) % self.duration:5.1f} / {self.duration:.1f} s"

    def latest(self):
        k = max(0, bisect.bisect_right(self.times, (time.time() - self.start) % self.duration) - 1)
        row = self.frames[k]
        j = bisect.bisect_right(self.image_rows, k) - 1
        if j >= 0 and j != self._loaded:
            img_row = self.frames[self.image_rows[j]]
            self._rectify = read_rgb(img_row["rectify_path"], self.img_width)
            self._depth = read_rgb(img_row["depth_png_path"], self.img_width)
            self._loaded = j
        return dict(force=row["force"], depth_max=row["depth_max"], contact=row["contact"],
                    rectify=self._rectify, depth=self._depth)


def tool_anchor(sim, arm=""):
    """Where the arrow starts and how it is oriented: the arm's tool-centre point."""

    def anchor():
        pos, quat = sim.arm(arm).ee_pose(sim.state)
        return pos, quat_xyzw_to_mat(quat)

    return anchor


def make_page(sim, source, args, anchor=None):
    """``page(studio)`` adding the tactile panel and arrow. ``anchor()`` returns (position, rotation matrix)."""
    anchor = anchor or tool_anchor(sim, args.arm)
    mount = rpy_to_mat(*args.mount_rpy)
    blank = np.zeros((int(args.img_width * 1.75), args.img_width, 3), np.uint8)

    def page(studio):
        gui, scene = studio.gui, studio.scene
        with gui.add_folder("Xense tactile"):
            status = gui.add_text("Source", initial_value=source.label, disabled=True)
            state = gui.add_text("State", initial_value="waiting", disabled=True)
            contact = gui.add_checkbox("Contact", initial_value=False, disabled=True)
            numbers = [gui.add_number(label, initial_value=0.0, step=0.001, disabled=True) for label in LABELS]
            depth_max = gui.add_number("depth_max", initial_value=0.0, step=0.0001, disabled=True)
            show = gui.add_checkbox("Force arrow", initial_value=True)
            scale = gui.add_slider("Arrow m per unit", min=0.001, max=0.05, step=0.001, initial_value=args.arrow_scale)
            gui.add_markdown("Arrow direction uses `--mount-rpy`; the mount is **not calibrated**.")
            rect_img = gui.add_image(blank, label="Rectify", format="jpeg")
            depth_img = gui.add_image(blank, label="Depth", format="jpeg")

        segment = np.zeros((1, 2, 3), np.float32)
        arrow = scene.add_line_segments("/xense/force", points=segment, colors=(200, 200, 200), line_width=4.0)
        tip = scene.add_icosphere("/xense/tip", radius=0.006, color=(200, 200, 200))
        shown = {"rectify": None, "depth": None}

        def update():
            nonlocal arrow
            status.value = source.label
            state.value = source.status()
            data = source.latest()
            if data is None:
                return
            force = np.asarray(data["force"], dtype=np.float64)
            for handle, v in zip(numbers, force):
                handle.value = round(float(v), 3)
            depth_max.value = round(float(data["depth_max"]), 4)
            contact.value = bool(data["contact"])
            for key, handle in (("rectify", rect_img), ("depth", depth_img)):
                if data[key] is not None and data[key] is not shown[key]:
                    handle.image = data[key]
                    shown[key] = data[key]

            pos, rot = anchor()
            vec = rot @ mount @ force[:3] * scale.value
            norm = float(np.linalg.norm(vec))
            if norm > MAX_ARROW_M:
                vec *= MAX_ARROW_M / norm
            end = pos + vec
            colour = (230, 80, 40) if data["contact"] else (200, 200, 200)
            visible = bool(show.value)
            points = np.asarray([[pos, end]], dtype=np.float32)
            try:
                arrow.points = points
                arrow.colors = colour
            except AttributeError:  # an older viser without settable segments
                arrow.remove()
                arrow = scene.add_line_segments("/xense/force", points=points, colors=colour, line_width=4.0)
            arrow.visible = visible
            tip.position = tuple(end)
            tip.color = colour
            tip.visible = visible

        studio.each_step(update)

    return page


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--live", metavar="URL", help="base station Socket.IO URL, e.g. http://localhost:3000")
    src.add_argument("--episode", metavar="DIR", help="recorded xense-tactile-v1 episode to replay")
    ap.add_argument("--robot", default="panda", help="registered robot (./ctl catalog robots)")
    ap.add_argument("--arm", default="", help="arm name for the arrow (default: the first)")
    ap.add_argument("--cell", default="bench_075")
    ap.add_argument("--mount-rpy", type=float, nargs=3, default=(0.0, 0.0, 0.0), metavar=("R", "P", "Y"),
                    help="sensor-to-tool rotation in degrees (uncalibrated; default identity)")
    ap.add_argument("--arrow-scale", type=float, default=0.01, help="arrow metres per force unit")
    ap.add_argument("--img-width", type=int, default=200)
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()

    source = LiveSource(args.live) if args.live else EpisodeSource(args.episode, args.img_width)

    import robosimstudio as rss

    scene = rss.Scene(cell=args.cell, physics=rss.MUJOCO_500HZ)
    scene.add(rss.box(name="cube", half=(0.025, 0.025, 0.025), at=(-0.143, -0.115, 0.1)))
    scene.add(rss.robot(args.robot))
    sim = scene.build()
    sim.run(50).sync()
    rss.view(sim, page=make_page(sim, source, args), port=args.port)


if __name__ == "__main__":
    main()
