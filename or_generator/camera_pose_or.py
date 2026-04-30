import typing
import gin
import bpy
import logging
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from tqdm.auto import tqdm
from copy import deepcopy

from infinigen.core.util import blender as butil
from infinigen.core.placement import camera as cam_util
from infinigen.core.util.random import random_general
from infinigen.core.placement.animation_policy import get_altitude

logger = logging.getLogger(__name__)


@gin.configurable
def compute_cam_rigs_poses_or(
    cam_rigs,
    scene_preprocessed: dict,
    angio_armatures: list[bpy.types.Object],
    angio_objs_merged: list[bpy.types.Object],
    floor_surface: list[bpy.types.Object],
    nonroom_objs: list[bpy.types.Object] | None = None,
    min_angio_dist: int = 0,
):
    bpy.context.view_layer.update()

    # Calculate gantry_target position
    # We want to force all cameras to look in direction of the C-ARM as roi
    zeego_armature = angio_armatures["zeego_armature"]
    gantry_target = zeego_armature.pose.bones["gantry_target"]
    # Transform into world coords
    gantry_target_matrix = zeego_armature.matrix_world @ gantry_target.matrix
    gantry_target_loc = gantry_target_matrix.translation
    logging.debug(f"Gantry target located at: {gantry_target_loc}")

    # Compute combined 3D bounds of all angio_objs
    angio_objs_corners = np.array(
        [
            obj.matrix_world @ Vector(c)
            for obj in angio_objs_merged
            for c in obj.bound_box
        ]
    )
    bbox_min, bbox_max = (
        Vector(angio_objs_corners.min(axis=0)),
        Vector(angio_objs_corners.max(axis=0)),
    )

    floor_locations = cam_util.sample_random_locs(floor_surface)
    # Filter all locations inside x,y bounds of angio objs
    in_angio_area = (
        (floor_locations[:, 0] >= bbox_min.x - min_angio_dist)
        & (floor_locations[:, 0] <= bbox_max.x + min_angio_dist)
        & (floor_locations[:, 1] >= bbox_min.y - min_angio_dist)
        & (floor_locations[:, 1] <= bbox_max.y + min_angio_dist)
    )
    floor_locations = floor_locations[~in_angio_area]

    rng = np.random.default_rng()

    def sample_location():
        return Vector(rng.choice(floor_locations))

    for cam_rig in cam_rigs:
        candidate_poses = sample_cam_rig_poses_or(
            cam_rig,
            scene_preprocessed=scene_preprocessed,
            center_coordinate=gantry_target_loc,
            sample_location=sample_location,
        )
        # Pick candidate with the highest score
        score, pose_proposal, straight_ahead_dists = candidate_poses[0]
        pose_proposal.apply(cam_rig)

        for i, cam in enumerate(cam_rig.children):
            if not cam.type == "CAMERA":
                continue

            cam.data.lens = pose_proposal.focal_length
            focus_dist = straight_ahead_dists[i]
            if focus_dist is not None:
                cam.data.dof.focus_distance = (
                    focus_dist  # Used if render_image.use_dof is True (default: False)
                )


@gin.configurable
def sample_cam_rig_poses_or(
    cam_rig: bpy.types.Object,
    scene_preprocessed,
    center_coordinate: Vector,
    sample_location: typing.Callable,
    n_candidates: int = 50,
    n_tries: int = 10000,
    visualize: bool = False,
):
    candidate_poses = []
    scene_bvh = scene_preprocessed["scene_bvh"]

    with tqdm(
        total=n_candidates,
        desc=f"Searching for {n_candidates} candidate poses for {cam_rig.name}",
    ) as pbar:
        for i in range(n_tries):
            pose_proposal = cam_rig_pose_proposal(
                scene_bvh=scene_bvh,
                center_coordinate=center_coordinate,
                sample_location=sample_location,
            )
            if pose_proposal is None:
                continue

            # Apply pose to cam_rig
            pose_proposal.apply(cam_rig)

            # Check if each cam within the cam rig has a valid pose
            # keep_cam_pose_proposal ranks proposal with a score (evaluated based on depth variation, min_dist)
            cam_scores = [
                cam_util.keep_cam_pose_proposal(cam, **scene_preprocessed)
                for cam in cam_rig.children
            ]
            criterion = (
                np.mean(cam_scores) if all(s is not None for s in cam_scores) else None
            )

            if visualize:
                criterion_str = f"{criterion:.2f}" if criterion is not None else "None"
                marker = butil.spawn_empty(f"attempt_{i}_{criterion_str}")
                marker.location = cam_rig.location
                marker.rotation_euler = cam_rig.rotation_euler

            if criterion is None:
                logger.debug(f"{i=} {criterion=}")
                continue

            # Compute distance to the nearest object for each camera
            # TODO: Check if C-ARM is far enough away to be visible
            straight_ahead_dists = []
            for cam in cam_rig.children:
                destination = cam.matrix_world @ Vector((0.0, 0.0, -1.0))
                forward_dir = (destination - cam.location).normalized()
                *_, straight_ahead_dist = scene_bvh.ray_cast(cam.location, forward_dir)
                straight_ahead_dists.append(straight_ahead_dist)

            candidate_poses.append(
                (criterion, deepcopy(pose_proposal), straight_ahead_dists)
            )
            pbar.update(1)

            if len(candidate_poses) >= n_candidates:
                break

    if len(candidate_poses) == 0:
        if visualize:
            butil.save_blend("compute_base_views-failed.blend")
        raise ValueError(
            f"Could not find any valid pose for cam rig {cam_rig.name} after {n_tries} tries"
        )

    candidate_poses = sorted(candidate_poses, reverse=True)
    return candidate_poses


@gin.configurable
def cam_rig_pose_proposal(
    scene_bvh: BVHTree,
    center_coordinate: Vector,
    sample_location: typing.Callable,
    altitude=("uniform", 2.0, 2.0),
    roll_noise=None,
    pitch_noise=None,
    yaw_noise=None,
    focal_length=50,
) -> cam_util.CameraProposal:
    # Sample random floor location within OR room
    loc = sample_location()

    # Sample and update altitude (from floor)
    # Get distance from floor
    curr_alt = get_altitude(loc=loc, scene_bvh=scene_bvh)
    if curr_alt is None:
        logger.debug(f"Got {curr_alt=} for {loc=}")
        return None
    # Sample altitude
    target_alt = random_general(altitude)
    # Update
    loc[2] += target_alt - curr_alt

    # Look at center_coordinate
    direction = loc - Vector(center_coordinate)
    rotation_matrix = direction.to_track_quat("Z", "Y").to_matrix()
    roll, pitch, yaw = rotation_matrix.to_euler("XYZ")

    # Apply random noise to roll, pitch, yaw if specified
    def apply_noise(angle: float, noise) -> float:
        return angle + np.deg2rad(random_general(noise)) if noise is not None else angle

    rot = np.array(
        [
            apply_noise(roll, roll_noise),
            apply_noise(pitch, pitch_noise),
            apply_noise(yaw, yaw_noise),
        ]
    )

    focal_length = random_general(focal_length)
    pose_proposal = cam_util.CameraProposal(loc=loc, rot=rot, focal_length=focal_length)
    return pose_proposal
