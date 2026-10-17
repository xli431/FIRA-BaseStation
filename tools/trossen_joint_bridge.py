#!/usr/bin/env python3
"""Stream a Trossen WidowX AI follower's joint positions to the base station.

Emits `follower_state_from_arm` to server.js, which relays it as
`follower_state_to_browser`; robosim_trossen.py mirrors it onto the simulated arm.

**Read-only.** This calls TrossenArmDriver.configure() and get_all_positions()
and nothing else: it never sets a mode, a position, a velocity or an effort, and
it does not clear errors. Per Trossen's docs configure() opens the connection
and does not change joint modes.

Whether the arm controller accepts a second connection while your teleop
program holds one is not documented. If this script cannot connect while
teleop runs, use JointStatePublisher inside the teleop loop instead:

    from trossen_joint_bridge import JointStatePublisher
    pub = JointStatePublisher("http://localhost:3000")
    ...  # in the loop, after reading the follower:
    pub.send(follower_driver.get_all_positions())

    python tools/trossen_joint_bridge.py --ip 192.168.1.3
    python tools/trossen_joint_bridge.py --fake          # a slow wave, no hardware
"""
import argparse
import math
import threading
import time

import socketio

NUM_ARM_JOINTS = 6
GRIPPER_MAX_M = 0.044  # left_carriage_joint upper limit in wxai_follower.urdf


class JointStatePublisher:
    """Send [j0..j5, gripper_m] to the base station. Safe to call at any rate; drops while disconnected."""

    def __init__(self, server="http://localhost:3000", name="wxai_follower"):
        self.name = name
        self.sio = socketio.Client(reconnection=True, reconnection_delay=1)
        self.sio.on("connect", lambda: self.sio.emit("register_follower", {"name": self.name}))
        # Connect from a thread so a teleop loop never blocks on the base station.
        threading.Thread(target=self._connect, args=(server,), daemon=True).start()

    def _connect(self, server):
        while not self.sio.connected:
            try:
                self.sio.connect(server, wait_timeout=5)
            except socketio.exceptions.ConnectionError:
                time.sleep(2)

    def send(self, positions):
        positions = [float(p) for p in positions]
        if len(positions) < NUM_ARM_JOINTS + 1:
            raise ValueError(f"expected {NUM_ARM_JOINTS} joints + gripper, got {len(positions)} values")
        if self.sio.connected:
            self.sio.emit("follower_state_from_arm", {
                "name": self.name,
                "ts": time.time(),
                "joints": positions[:NUM_ARM_JOINTS],
                "gripper_m": positions[NUM_ARM_JOINTS],
            })

    def close(self):
        if self.sio.connected:
            self.sio.disconnect()


class TrossenReader:
    def __init__(self, ip):
        import trossen_arm

        self.driver = trossen_arm.TrossenArmDriver()
        # clear_error=False: reading must not change the controller's state.
        self.driver.configure(trossen_arm.Model.wxai_v0, trossen_arm.StandardEndEffector.wxai_v0_follower, ip, False)

    def read(self):
        return list(self.driver.get_all_positions())

    def close(self):
        pass  # no cleanup(): Trossen's cleanup paths move the arm home


class FakeReader:
    def __init__(self):
        self.t0 = time.time()

    def read(self):
        t = time.time() - self.t0
        return [
            0.6 * math.sin(0.4 * t),
            1.0 + 0.3 * math.sin(0.3 * t),
            0.6 + 0.3 * math.sin(0.5 * t),
            0.5 * math.sin(0.35 * t),
            0.4 * math.sin(0.45 * t),
            0.8 * math.sin(0.25 * t),
            GRIPPER_MAX_M * (0.5 + 0.5 * math.sin(0.8 * t)),
        ]

    def close(self):
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--ip", help="follower arm controller IP")
    src.add_argument("--fake", action="store_true", help="send a slow joint wave instead of reading an arm")
    ap.add_argument("--server", default="http://localhost:3000")
    ap.add_argument("--hz", type=float, default=50.0)
    args = ap.parse_args()

    reader = FakeReader() if args.fake else TrossenReader(args.ip)
    pub = JointStatePublisher(args.server)
    print("Streaming follower joints; Ctrl+C to stop")
    period = 1.0 / args.hz
    n = 0
    try:
        while True:
            t = time.time()
            q = reader.read()
            pub.send(q)
            n += 1
            if n % int(args.hz) == 0:
                print("\r" + "  ".join(f"J{i}:{math.degrees(v):+7.1f}" for i, v in enumerate(q[:NUM_ARM_JOINTS]))
                      + f"  grip:{q[NUM_ARM_JOINTS] * 1000:5.1f}mm", end="", flush=True)
            time.sleep(max(0.0, period - (time.time() - t)))
    except KeyboardInterrupt:
        print()
    finally:
        reader.close()
        pub.close()


if __name__ == "__main__":
    main()
