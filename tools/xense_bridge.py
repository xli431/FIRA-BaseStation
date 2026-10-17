#!/usr/bin/env python3
"""Stream a Xense tactile sensor to the base station over Socket.IO.

Emits `tactile_from_sensor` to server.js, which relays it to the browser
as `tactile_to_browser`. Listens for `tactile_command_to_sensor` (e.g. tare).

    python tools/xense_bridge.py                   # real sensor, first one found
    python tools/xense_bridge.py --serial OG000741
    python tools/xense_bridge.py --fake            # synthetic data, no hardware
"""
import argparse
import base64
import math
import time

import cv2
import numpy as np
import socketio

FORCE_LABELS = ["Fx", "Fy", "Fz", "Mx", "My", "Mz"]


def encode_jpeg(img, quality=70):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return base64.b64encode(buf).decode("ascii") if ok else None


def depth_to_color(depth, depth_scale):
    """Fixed scale (0..depth_scale) so a resting sensor stays dark instead of amplifying noise."""
    d = np.clip(np.asarray(depth, dtype=np.float32).squeeze() / depth_scale, 0.0, 1.0)
    return cv2.applyColorMap((d * 255).astype(np.uint8), cv2.COLORMAP_JET)


def shrink(img, width):
    h, w = img.shape[:2]
    if w <= width:
        return img
    return cv2.resize(img, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)


class XenseSource:
    def __init__(self, serial):
        from xensesdk import Sensor

        self.T = Sensor.OutputType
        if serial is None:
            found = Sensor.scanSerialNumber()
            if not found:
                raise RuntimeError("No Xense sensor found")
            serial = next(iter(found))
        self.serial = serial
        self.sensor = Sensor.create(serial)
        time.sleep(1.0)  # camera warm-up

    def read(self, with_images):
        if with_images:
            force, depth, rectify = self.sensor.selectSensorInfo(
                self.T.ForceResultant, self.T.Depth, self.T.Rectify
            )
            return np.asarray(force), np.asarray(depth), np.asarray(rectify)
        force, depth = self.sensor.selectSensorInfo(self.T.ForceResultant, self.T.Depth)
        return np.asarray(force), np.asarray(depth), None

    def tare(self):
        self.sensor.calibrateSensor()

    def release(self):
        self.sensor.release()


class FakeSource:
    """Periodic press-and-release so the dashboard can be tested without hardware."""

    serial = "FAKE0001"

    def __init__(self):
        self.t0 = time.time()
        yy, xx = np.mgrid[0:700, 0:400]
        self.blob = np.exp(-(((xx - 200) / 60.0) ** 2 + ((yy - 350) / 80.0) ** 2))

    def read(self, with_images):
        t = time.time() - self.t0
        press = max(0.0, math.sin(t * 1.5)) ** 2
        force = np.array([-6, 12, -13, 3, 1, 1.5], dtype=np.float32) * press
        force += np.random.normal(0, 0.02, 6).astype(np.float32)
        depth = (self.blob * 0.45 * press).astype(np.float32)
        rectify = None
        if with_images:
            rectify = np.full((700, 400, 3), 180, np.uint8)
            rectify[..., 0] = (180 - 120 * self.blob * press).astype(np.uint8)
        return force, depth, rectify

    def tare(self):
        self.t0 = time.time()

    def release(self):
        pass


def connect_with_retry(sio, url):
    while True:
        try:
            sio.connect(url, wait_timeout=5)
            return
        except socketio.exceptions.ConnectionError as e:
            print(f"Server {url} not reachable ({e}); retrying in 2 s")
            time.sleep(2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="http://localhost:3000")
    ap.add_argument("--serial", default=None, help="sensor serial number (default: first found)")
    ap.add_argument("--hz", type=float, default=30.0, help="force/depth update rate")
    ap.add_argument("--img-hz", type=float, default=10.0, help="image update rate")
    ap.add_argument("--img-width", type=int, default=200, help="width of images sent to the browser")
    ap.add_argument("--depth-scale", type=float, default=0.5, help="depth shown as full colour")
    ap.add_argument("--contact-force", type=float, default=0.5, help="|Fz| above this counts as contact")
    ap.add_argument("--fake", action="store_true", help="send synthetic data instead of reading a sensor")
    args = ap.parse_args()

    source = FakeSource() if args.fake else XenseSource(args.serial)
    print(f"Sensor {source.serial} ready")

    pending = []
    sio = socketio.Client(reconnection=True, reconnection_delay=1)

    @sio.event
    def connect():
        print(f"Connected to {args.server}")
        sio.emit("register_tactile", {"serial": source.serial})

    @sio.event
    def disconnect():
        print("Disconnected from server")

    @sio.on("tactile_command_to_sensor")
    def on_command(data):
        pending.append(data)  # handled in the main loop so the sensor is used from one thread

    connect_with_retry(sio, args.server)

    period = 1.0 / args.hz
    img_period = 1.0 / args.img_hz
    last_img = 0.0
    fps, n, fps_t0 = 0.0, 0, time.time()

    try:
        while True:
            t_start = time.time()

            while pending:
                cmd = pending.pop(0)
                if isinstance(cmd, dict) and cmd.get("action") == "tare":
                    print("Tare requested")
                    try:
                        source.tare()
                    except Exception as e:
                        print("Tare failed:", repr(e))

            with_images = t_start - last_img >= img_period
            force, depth, rectify = source.read(with_images)
            depth_max = float(np.max(depth))
            force = [round(float(v), 3) for v in force.ravel()[:6]]

            msg = {
                "serial": source.serial,
                "ts": t_start,
                "fps": round(fps, 1),
                "force": force,
                "labels": FORCE_LABELS,
                "depth_max": round(depth_max, 4),
                "contact": abs(force[2]) > args.contact_force,
            }
            if with_images:
                last_img = t_start
                msg["depth_frame"] = encode_jpeg(shrink(depth_to_color(depth, args.depth_scale), args.img_width))
                if rectify is not None:
                    msg["rectify_frame"] = encode_jpeg(shrink(rectify, args.img_width))

            if sio.connected:
                sio.emit("tactile_from_sensor", msg)

            n += 1
            if t_start - fps_t0 >= 1.0:
                fps, n, fps_t0 = n / (t_start - fps_t0), 0, t_start

            time.sleep(max(0.0, period - (time.time() - t_start)))
    except KeyboardInterrupt:
        pass
    finally:
        source.release()
        if sio.connected:
            sio.disconnect()


if __name__ == "__main__":
    main()
