"""Episode bundle for Xense tactile recordings: schema `xense-tactile-v1`.

Shared by the recorder (xense_bridge.py --record), the FiftyOne loader and the
robosimstudio replay. Layout of one episode directory:

    manifest.json      identity, provenance, units, rates, summary
    frames.jsonl       one line per force sample; image samples carry "image"
    images/NNNNNN_rectify.jpg   rectified camera image
    images/NNNNNN_depth.png     depth as a colour map (display only)
    images/NNNNNN_depth.npz     depth as float32 (key "depth"), as the SDK returns it

Units are what the SDK reports and are not independently verified: force and
torque are labelled `sdk_unverified`, depth likewise. Forces are in the sensor's
own frame; no sensor-to-tool transform is applied or implied.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

SCHEMA = "xense-tactile-v1"
FORCE_LABELS = ["Fx", "Fy", "Fz", "Mx", "My", "Mz"]
DATA_KINDS = {"real", "synthetic_fixture"}


def depth_to_color(depth, depth_scale):
    """Fixed scale (0..depth_scale) so a resting sensor stays dark instead of amplifying noise."""
    d = np.clip(np.asarray(depth, dtype=np.float32).squeeze() / depth_scale, 0.0, 1.0)
    return cv2.applyColorMap((d * 255).astype(np.uint8), cv2.COLORMAP_JET)


def _sdk_version():
    try:
        from importlib.metadata import version

        return version("xensesdk")
    except Exception:
        return None


class EpisodeRecorder:
    def __init__(self, folder, *, episode_id, serial, data_kind, force_hz, image_hz, depth_scale):
        if data_kind not in DATA_KINDS:
            raise ValueError(f"data_kind must be one of {sorted(DATA_KINDS)}")
        self.folder = Path(folder).resolve()
        self.folder.mkdir(parents=True, exist_ok=False)  # never append to an old episode
        (self.folder / "images").mkdir()
        self.depth_scale = depth_scale
        self.n_frames = 0
        self.n_images = 0
        self.n_contact = 0
        self.t0 = None
        self.t_last = None
        self.manifest = dict(
            schema_version=SCHEMA,
            episode_id=episode_id,
            source="xense_sdk",
            sdk_version=_sdk_version() if data_kind == "real" else None,
            serial=serial,
            data_kind=data_kind,
            started_at=datetime.now(timezone.utc).isoformat(),
            frames="frames.jsonl",
            force_labels=FORCE_LABELS,
            units=dict(force="sdk_unverified", torque="sdk_unverified", depth="sdk_unverified"),
            coordinate_frame="xense_sensor_uncalibrated",
            force_rate_hz=force_hz,
            image_rate_hz=image_hz,
            depth_display_scale=depth_scale,
        )
        self._frames = (self.folder / "frames.jsonl").open("w")
        self._write_manifest()

    def write(self, t, force, depth_max, contact, depth=None, rectify=None):
        if self.t0 is None:
            self.t0 = t
        self.t_last = t
        row = dict(i=self.n_frames, t=t, force=force, depth_max=depth_max, contact=contact)
        if depth is not None and rectify is not None:
            stem = f"images/{self.n_images:06d}"
            cv2.imwrite(str(self.folder / f"{stem}_rectify.jpg"), rectify, [cv2.IMWRITE_JPEG_QUALITY, 90])
            cv2.imwrite(str(self.folder / f"{stem}_depth.png"), depth_to_color(depth, self.depth_scale))
            np.savez_compressed(self.folder / f"{stem}_depth.npz", depth=np.asarray(depth, np.float32).squeeze())
            row["image"] = self.n_images
            self.n_images += 1
        self._frames.write(json.dumps(row, allow_nan=False) + "\n")
        self.n_frames += 1
        self.n_contact += bool(contact)

    def close(self):
        self._frames.close()
        self.manifest.update(
            ended_at=datetime.now(timezone.utc).isoformat(),
            duration_s=round(self.t_last - self.t0, 3) if self.t0 is not None else 0.0,
            frame_count=self.n_frames,
            image_count=self.n_images,
            contact_frame_count=self.n_contact,
        )
        self._write_manifest()

    def _write_manifest(self):
        (self.folder / "manifest.json").write_text(json.dumps(self.manifest, indent=2, allow_nan=False) + "\n")


def load_episode(folder):
    """Read and check a bundle. Returns (manifest, frames) with image paths made absolute."""
    folder = Path(folder).resolve()
    manifest = json.loads((folder / "manifest.json").read_text())
    if manifest.get("schema_version") != SCHEMA:
        raise ValueError(f"{folder}: not a {SCHEMA} episode")
    if "frame_count" not in manifest:
        raise ValueError(f"{folder}: recording was not closed cleanly (no frame_count)")
    frames = [json.loads(line) for line in (folder / manifest["frames"]).read_text().splitlines() if line]
    if len(frames) != manifest["frame_count"]:
        raise ValueError(f"{folder}: frames.jsonl has {len(frames)} rows, manifest says {manifest['frame_count']}")
    for row in frames:
        if "image" in row:
            stem = folder / "images" / f"{row['image']:06d}"
            row["rectify_path"] = str(stem) + "_rectify.jpg"
            row["depth_png_path"] = str(stem) + "_depth.png"
            row["depth_npz_path"] = str(stem) + "_depth.npz"
            for key in ("rectify_path", "depth_png_path", "depth_npz_path"):
                if not Path(row[key]).is_file():
                    raise ValueError(f"{folder}: missing {row[key]}")
    return manifest, frames


def default_episode_id(serial):
    return f"{serial}-{time.strftime('%Y%m%d-%H%M%S')}"
