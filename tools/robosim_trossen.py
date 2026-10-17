"""The Trossen WidowX AI follower, with a Xense on its gripper, in robosimstudio.

Registers the arm from Trossen's own `wxai_follower.urdf` (trossen_arm_description)
without editing robosimstudio, adds a Xense pad to the left finger, and opens the
studio with two extra panels:

  Follower mirror   drives the simulated arm from the real follower's joints
                    (trossen_joint_bridge.py -> server.js -> here); untick to
                    hand the arm back to the gizmo
  Xense tactile     force/torque, depth, images, and a force arrow at the pad
                    (robosim_tactile.py's panel)

Runs in robosimstudio's environment (Python >= 3.12, NVIDIA GPU):

    cd ~/robosimstudio
    uv run --with "python-socketio[client]" --with opencv-python-headless \\
        python ~/fira-station/tools/robosim_trossen.py \\
        --description ~/trossen_arm_description --live http://localhost:3000

What is taken from Trossen's files: joint names, limits, axes, meshes, the
mimic between the two carriages, the gripper's 0..0.044 m travel and the
ee_gripper tool frame 156 mm along link_6 x. The Trossen leader home pose is
used as home_q.

What is not measured and is stated as such: the Xense pad's position and size
on the finger (--xense-offset, --xense-size; visual only, no collider), the
sensor-to-finger rotation (--mount-rpy, default: gel normal facing the other
finger), the gripper pad dimensions (left 0, robosimstudio's convention for
unmeasured), servo gains (robosimstudio defaults) and where the base stands
(--base). The mirror sets joint *targets*; the simulated arm reaches them
through its own PD servo, so it lags the real arm and can be stopped by
contact the real arm did not meet.
"""
import argparse
import math
import re
import time
from pathlib import Path

import numpy as np

import robosim_tactile as rt

ARM = "arm"
JOINTS = tuple(f"joint_{i}" for i in range(6))
GRIPPER_MAX_M = 0.044
HOME_Q = (0.0, math.pi / 3, math.pi / 6, math.pi / 5, 0.0, 0.0)  # Trossen's LEADER_HOME_POSITION
XENSE_BODY = "carriage_left"
STALE_S = 0.5


def prepare_urdf(description, out_dir, xense_offset, xense_size):
    """Trossen's follower URDF with absolute mesh paths and a visual-only Xense pad on the left finger."""
    description = Path(description).expanduser().resolve()
    src = description / "urdf" / "generated" / "wxai" / "wxai_follower.urdf"
    if not src.is_file():
        raise FileNotFoundError(f"{src} not found; pass the trossen_arm_description checkout as --description")
    text = src.read_text()
    text = text.replace("package://trossen_arm_description/", f"{description}/")
    missing = [m for m in re.findall(r'filename="([^"]+)"', text) if not Path(m).is_file()]
    if missing:
        raise FileNotFoundError(f"meshes missing from {description}: {missing[:3]}")
    x, y, z = xense_offset
    sx, sy, sz = xense_size
    pad = f"""
  <link name="xense_pad">
    <inertial><origin xyz="0 0 0"/><mass value="0.001"/>
      <inertia ixx="1e-8" ixy="0" ixz="0" iyy="1e-8" iyz="0" izz="1e-8"/></inertial>
    <visual><origin xyz="0 0 0"/><geometry><box size="{sx} {sy} {sz}"/></geometry>
      <material name="xense_gel"><color rgba="0.85 0.85 0.9 1"/></material></visual>
  </link>
  <joint name="xense_pad_joint" type="fixed">
    <origin rpy="0 0 0" xyz="{x} {y} {z}"/>
    <parent link="{XENSE_BODY}"/>
    <child link="xense_pad"/>
  </joint>
</robot>"""
    text = text.replace("</robot>", pad, 1)
    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "wxai_follower_xense.urdf"
    out.write_text(text)
    return out


def register(rss, urdf, base):
    from robosimstudio.robots import register_embodiment, register_gripper
    from robosimstudio.specs import GripperSpec

    register_gripper(
        GripperSpec(
            name="trossen_wxai_v0",
            finger_names=("left_carriage_joint",),
            follower_names=("right_carriage_joint",),  # a <mimic> in Trossen's URDF
            finger_open_q=(GRIPPER_MAX_M,),
            finger_closed_q=(0.0,),
            open_width=GRIPPER_MAX_M,
            tcp_body="link_6",
            tcp_offset=(0.156062, 0.0, 0.0),  # the URDF's ee_gripper joint
            tcp_offset_rot=(0.0, math.sqrt(0.5), 0.0, math.sqrt(0.5)),  # grasp +z along link_6 +x
            approach_axis=(1.0, 0.0, 0.0),
            close_axis=(0.0, 1.0, 0.0),
            finger_bodies=(("gripper_left",), ("gripper_right",)),
            open_axis_signs=(1.0, -1.0),  # left carriage opens along +y, right along -y
        )
    )
    return register_embodiment(
        rss.EmbodimentSpec(
            name="trossen_wxai_follower",
            asset=rss.AssetSpec(path=str(urdf)),
            urdf=str(urdf),
            base_offset=(*base, 0.0, 0.0, 0.0, 1.0),
            hull_colliders=True,
            arms=(rss.ArmSpec(name=ARM, joint_names=JOINTS, ee_body_name="link_6",
                              gripper="trossen_wxai_v0", home_q=HOME_Q),),
        )
    )


class FollowerSource:
    """Latest follower joint state from the base station; callbacks only assign."""

    def __init__(self, url):
        import socketio

        self.msg = None
        self.received = 0.0
        self.connected = False
        sio = socketio.Client(reconnection=True, reconnection_delay=1)
        sio.on("connect", lambda: setattr(self, "connected", True))
        sio.on("disconnect", lambda: setattr(self, "connected", False))
        sio.on("follower_state_to_browser", self._on_state)
        sio.connect(url, wait=False)
        self.sio = sio

    def _on_state(self, data):
        self.msg = data
        self.received = time.time()

    def latest(self):
        """(joints, gripper_m) if fresh, else None."""
        if self.msg is None or time.time() - self.received > STALE_S:
            return None
        return list(self.msg["joints"]), float(self.msg["gripper_m"])

    def status(self):
        if not self.connected:
            return "base station not reachable"
        if self.msg is None:
            return "no follower stream"
        age = time.time() - self.received
        return "live" if age <= STALE_S else f"stale ({age:.1f} s)"


def joint_limits(urdf):
    """{joint: (lower, upper)} read from the URDF itself."""
    import xml.etree.ElementTree as ET

    limits = {}
    for joint in ET.parse(urdf).getroot().iter("joint"):
        limit = joint.find("limit")
        if limit is not None and "lower" in limit.attrib:
            limits[joint.get("name")] = (float(limit.get("lower")), float(limit.get("upper")))
    return limits


def mirror_page(sim, follower, limits):
    lower = np.array([limits[j][0] for j in JOINTS])
    upper = np.array([limits[j][1] for j in JOINTS])

    def page(studio):
        gui = studio.gui
        with gui.add_folder("Follower mirror"):
            enabled = gui.add_checkbox("Mirror real follower", initial_value=False)
            state = gui.add_text("State", initial_value="", disabled=True)
            readouts = [gui.add_number(f"{j} (deg)", initial_value=0.0, step=0.1, disabled=True) for j in JOINTS]
            grip = gui.add_number("gripper (mm)", initial_value=0.0, step=0.1, disabled=True)
        holding = {"on": False}

        def set_driving(on):
            if on == holding["on"]:
                return
            holding["on"] = on
            panel = studio.arms.get(ARM)
            if panel is not None:
                (panel.suspend if on else panel.resume)()

        def update():
            state.value = follower.status()
            reading = follower.latest()
            if reading is not None:
                q, g = reading
                for handle, v in zip(readouts, q):
                    handle.value = round(math.degrees(v), 1)
                grip.value = round(g * 1000, 1)
            if not enabled.value or reading is None:
                set_driving(False)  # stale or switched off: the gizmo takes the arm back where it is
                return
            set_driving(True)
            q = np.clip(np.asarray(q, dtype=np.float64), lower, upper)
            close = 1.0 - min(max(g / GRIPPER_MAX_M, 0.0), 1.0)  # robosimstudio: 0 open, 1 shut
            space = sim.use_control("joint_position")
            action = list(space.target_action(sim.control))
            index = list(sim.scene.embodiment.iks).index(ARM)
            action[index * space.per_arm:(index + 1) * space.per_arm] = [*q, close]
            space.apply(action, sim.control, sim.state)

        studio.each_step(update)

    return page


def xense_anchor(sim, offset):
    from robosimstudio.scene.entity import label_index

    body = label_index(list(sim.model.body_label), XENSE_BODY)
    offset = np.asarray(offset, dtype=np.float64)

    def anchor():
        tf = sim.state.body_q.numpy()[body]
        rot = rt.quat_xyzw_to_mat(tf[3:7])
        return np.asarray(tf[:3], dtype=np.float64) + rot @ offset, rot

    return anchor


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--description", required=True, help="trossen_arm_description checkout")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--live", metavar="URL", help="base station: follower joints and Xense, live")
    src.add_argument("--episode", metavar="DIR", help="replay a recorded Xense episode (no follower mirror)")
    ap.add_argument("--build-dir", default="~/.cache/fira-station", help="where the prepared URDF is written")
    ap.add_argument("--base", type=float, nargs=3, default=(-0.3, 0.0, 0.0), metavar=("X", "Y", "Z"),
                    help="arm base in the bench's work-surface frame [m] (not measured)")
    ap.add_argument("--cell", default="bench_075")
    ap.add_argument("--xense-offset", type=float, nargs=3, default=(0.050, -0.026, 0.0), metavar=("X", "Y", "Z"),
                    help="pad centre in carriage_left's frame [m]; default sits on the finger's inner face (placeholder, not measured)")
    ap.add_argument("--xense-size", type=float, nargs=3, default=(0.030, 0.006, 0.022), metavar=("X", "Y", "Z"),
                    help="pad box size [m] (placeholder, not measured)")
    ap.add_argument("--mount-rpy", type=float, nargs=3, default=(90.0, 0.0, 0.0), metavar=("R", "P", "Y"),
                    help="sensor-to-finger rotation in degrees; default puts sensor +z along finger -y (uncalibrated)")
    ap.add_argument("--arrow-scale", type=float, default=0.005)
    ap.add_argument("--img-width", type=int, default=200)
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    args.arm = ARM

    urdf = prepare_urdf(args.description, args.build_dir, args.xense_offset, args.xense_size)
    print(f"Prepared {urdf}")

    import robosimstudio as rss

    register(rss, urdf, args.base)
    scene = rss.Scene(cell=args.cell, name="trossen_xense", physics=rss.MUJOCO_500HZ)
    scene.add(rss.box(name="cube", half=(0.02, 0.02, 0.02), at=(0.05, 0.0, 0.02)))
    scene.add(rss.robot("trossen_wxai_follower"))
    sim = scene.build(control_mode="joint_position")
    sim.run(50).sync()
    tool = sim.arm(ARM).ee_pose(sim.state)[0]
    print(f"tool centre at ({tool[0]:.3f}, {tool[1]:.3f}, {tool[2]:.3f}) m")

    tactile = rt.LiveSource(args.live) if args.live else rt.EpisodeSource(args.episode, args.img_width)
    pages = [rt.make_page(sim, tactile, args, anchor=xense_anchor(sim, args.xense_offset))]
    if args.live:
        pages.append(mirror_page(sim, FollowerSource(args.live), joint_limits(urdf)))

    def page(studio):
        for p in pages:
            p(studio)

    rss.view(sim, page=page, port=args.port)


if __name__ == "__main__":
    main()
