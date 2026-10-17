"""Load recorded Xense tactile episodes (xense-tactile-v1) into a local FiftyOne dataset.

One group per recorded image frame, with two slices:
  rectify  the rectified camera image, with a `depth` heatmap overlay
  depth    the depth colour map
Every sample carries the force/torque, depth_max and contact reading taken in
the same loop iteration as the image, so image and force are the same instant.

    python tools/load_fiftyone_tactile.py episodes/press-01 episodes/press-02 --name xense-press
    python tools/load_fiftyone_tactile.py --existing --name xense-press
"""
import argparse
from pathlib import Path

import numpy as np

from xense_episode import load_episode


def load_dataset(episode_dirs, name):
    episodes = [load_episode(d) for d in episode_dirs]
    ids = [m["episode_id"] for m, _ in episodes]
    if len(set(ids)) != len(ids):
        raise ValueError(f"Duplicate episode ids: {ids}")
    import fiftyone as fo

    if fo.dataset_exists(name):
        raise ValueError(f"Dataset already exists: {name}; choose a new name or use --existing")
    dataset = fo.Dataset(name)
    dataset.add_group_field("group", default="rectify")
    try:
        for manifest, frames in episodes:
            t0 = frames[0]["t"] if frames else 0.0
            labels = manifest["force_labels"]
            scale = float(manifest["depth_display_scale"])
            samples = []
            for row in frames:
                if "image" not in row:
                    continue
                force = row["force"]
                common = dict(
                    episode_id=manifest["episode_id"],
                    serial=manifest["serial"],
                    data_kind=manifest["data_kind"],
                    coordinate_frame=manifest["coordinate_frame"],
                    force_units=manifest["units"]["force"],
                    frame_index=row["i"],
                    image_index=row["image"],
                    t=row["t"],
                    t_rel=round(row["t"] - t0, 4),
                    depth_max=row["depth_max"],
                    contact=bool(row["contact"]),
                    force_norm=float(np.linalg.norm(force[:3])),
                    **{label.lower(): float(v) for label, v in zip(labels, force)},
                )
                tags = [manifest["data_kind"], "contact" if row["contact"] else "no_contact"]
                group = fo.Group()
                rect = fo.Sample(filepath=row["rectify_path"], group=group.element("rectify"), tags=tags, **common)
                depth = np.load(row["depth_npz_path"])["depth"]
                rect["depth"] = fo.Heatmap(map=np.clip(depth, 0, None).astype(np.float32), range=[0, scale])
                samples.append(rect)
                samples.append(
                    fo.Sample(filepath=row["depth_png_path"], group=group.element("depth"), tags=tags, **common)
                )
            dataset.add_samples(samples)
        dataset.persistent = True
        return dataset
    except Exception:
        dataset.delete()  # only this newly created partial dataset; episode files stay intact
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("episodes", type=Path, nargs="*")
    parser.add_argument("--name", required=True)
    parser.add_argument("--port", type=int, default=5151)
    parser.add_argument("--existing", action="store_true", help="open an already imported dataset")
    parser.add_argument("--no-launch", action="store_true")
    args = parser.parse_args()
    if args.existing:
        if args.episodes:
            parser.error("--existing does not accept episodes")
        import fiftyone as fo

        dataset = fo.load_dataset(args.name)
    else:
        if not args.episodes:
            parser.error("provide episode directories or use --existing")
        dataset = load_dataset(args.episodes, args.name)
    print(f"Dataset: {dataset.name}; groups={len(dataset)}; slices={dataset.group_slices}")
    if not args.no_launch:
        import fiftyone as fo

        session = fo.launch_app(dataset, address="127.0.0.1", port=args.port, auto=False)
        print(f"Open http://127.0.0.1:{args.port}; keep this process running")
        session.wait()


if __name__ == "__main__":
    main()
