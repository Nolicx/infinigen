import gin
import bpy
import numpy as np
from mathutils import Vector


from infinigen.core.util import blender as butil
from infinigen.core.placement import camera as cam_util


@gin.configurable
def compute_camera_poses_or(
    cam_rigs,
    scene_preprocessed: dict,
    min_candidates_ratio: int = 5,
    min_base_views_ratio: int = 10,
    **kwargs,
):
    # Temporary camera to check cam pose proposals
    cam = cam_util.spawn_camera()

    start = bpy.context.scene.frame_start
    end = bpy.context.scene.frame_end

    # Check if animation enabled
    if end <= start:
        configure_camera_poses_or(
            cam_rigs=cam_rigs, scene_preprocessed=scene_preprocessed, **kwargs
        )
        butil.delete(cam)
        return []

    n_cams = len(cam_rigs)
    # num trajectories to fully compute and score
    n_min_candidates = int(min_candidates_ratio * n_cams)

    base_views = configure_camera_poses_or(
        cam_rigs=cam_rigs,
        scene_preprocessed=scene_preprocessed,
        n_views=n_min_candidates * min_base_views_ratio,
        **kwargs,
    )

    butil.delete(cam)
    return base_views


@gin.configurable
def configure_camera_poses_or(
    cam_rigs,
    scene_preprocessed: dict,
    init_bounding_box: tuple[np.ndarray, np.ndarray] | None = None,
    init_surfaces: list[bpy.types.Object] | None = None,
    terrain_mesh=None,
    nonroom_objs=None,
    mvs_setting=False,
    mvs_radius=("uniform", 12, 18),
    n_views: int | None = None,
    **kwargs,
):
    bpy.context.view_layer.update()

    if init_bounding_box is not None:

        def location_sample():
            return np.random.uniform(*init_bounding_box)
    elif init_surfaces is not None:
        random_locs = cam_util.sample_random_locs(init_surfaces)

        def location_sample():
            loc = Vector(random_locs[np.random.randint(len(random_locs)), :])
            loc.z += 1e-3
            return loc
    else:
        raise ValueError("Either init_bounding_box or init_surfaces must be provided")

    if mvs_setting:
        if terrain_mesh:
            vertices = np.array([np.array(v.co) for v in terrain_mesh.data.vertices])
            sdfs = scene_preprocessed["terrain"].compute_camera_space_sdf(vertices)
            vertices = vertices[sdfs >= -1e-5]
            center_coordinate = list(
                vertices[np.random.choice(list(range(len(vertices))))]
            )
        elif nonroom_objs:

            def contain_keywords(name, keywords):
                for keyword in keywords:
                    if name == keyword or name.startswith(f"{keyword}."):
                        return True
                return False

            inside_objs = [
                x
                for x in nonroom_objs
                if not contain_keywords(x.name, ["window", "door", "entrance"])
            ]
            assert inside_objs != []
            obj = np.random.choice(inside_objs)
            vertices = [v.co for v in obj.data.vertices]
            center_coordinate = vertices[np.random.choice(list(range(len(vertices))))]
            center_coordinate = obj.matrix_world @ center_coordinate
            center_coordinate = list(np.array(center_coordinate))
        else:
            raise ValueError(
                f"Got {mvs_setting=} yet {terrain_mesh=} {nonroom_objs=}, we expected at least one in order to choose a center coordinate"
            )
    else:
        center_coordinate = None

    views = None
    for cam_rig in cam_rigs:
        views = cam_util.compute_base_views(
            cam_rig,
            n_views=5,
            location_sample=location_sample,
            center_coordinate=center_coordinate,
            radius=mvs_radius,
            bbox=init_bounding_box,
            **scene_preprocessed,
            **kwargs,
        )

        score, props, focus_dist = views[0]
        cam_rig.location = props.loc
        cam_rig.rotation_euler = props.rot

        for cam in cam_rig.children:
            cam.data.lens = props.focal_length

        if focus_dist is not None:
            for cam in cam_rig.children:
                if not cam.type == "CAMERA":
                    continue
                cam.data.dof.focus_distance = focus_dist

    return views
