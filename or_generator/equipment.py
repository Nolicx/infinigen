import math
from pathlib import Path

import bpy
import gin


def _iter_coll_objects(coll):
    yield from coll.objects
    for child in coll.children:
        yield from _iter_coll_objects(child)


# Nur laden, was eine Armature hat
def load_collections(
    blend_path: Path, collection_names: list[str], armature_names: list[str]
):
    with bpy.data.libraries.load(str(blend_path), link=False) as (src, dst):
        dst.collections = [c for c in src.collections if c in collection_names]

    for coll in dst.collections:
        if coll is not None:
            bpy.context.scene.collection.children.link(coll)

    all_object_names = [
        obj.name for coll in dst.collections if coll for obj in _iter_coll_objects(coll)
    ]

    armature_names_set = set(armature_names)
    to_keep_names = set(armature_names_set)

    for name in all_object_names:
        if name not in bpy.data.objects:
            continue
        obj = bpy.data.objects[name]
        for mod in obj.modifiers:
            if (
                mod.type == "ARMATURE"
                and mod.object
                and mod.object.name in armature_names_set
            ):
                to_keep_names.add(name)
                break
        if obj.parent and obj.parent.name in armature_names_set:
            to_keep_names.add(name)

    for name in all_object_names:
        if name not in to_keep_names and name in bpy.data.objects:
            bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)

    for coll in list(bpy.context.scene.collection.children):
        if len(coll.objects) == 0 and len(coll.children) == 0:
            bpy.data.collections.remove(coll)


def _set_props(arm_name: str, props: dict):
    arm = bpy.data.objects[arm_name]
    for key, value in (props or {}).items():
        convert = "_rot" in key
        if isinstance(value, (list, tuple)):
            for i, v in enumerate(value):
                arm[key][i] = math.radians(v) if convert else v
        else:
            arm[key] = math.radians(value) if convert else value


@gin.configurable
def place_or_equipment(
    blend_path: str,
    zeego_collection: str = "zeego",
    zeego_armature: str = "zeego_armature",
    zeego_location=(0.0, 0.0, 0.0),
    zeego_rotation_deg=(0.0, 0.0, 0.0),
    zeego_props=None,
    table_collection: str = "table",
    table_armature: str = "table_armature",
    table_location=(0.0, 0.0, 0.0),
    table_rotation_deg=(0.0, 0.0, 0.0),
    table_props=None,
) -> dict:
    # _load_all(Path(blend_path))
    load_collections(
        Path(blend_path),
        [zeego_collection, table_collection],
        [zeego_armature, table_armature],
    )
    armatures = {}
    for arm_name, loc, rot_deg, props in [
        (zeego_armature, zeego_location, zeego_rotation_deg, zeego_props),
        (table_armature, table_location, table_rotation_deg, table_props),
    ]:
        arm = bpy.data.objects[arm_name]
        arm.location = loc
        arm.rotation_euler = [math.radians(d) for d in rot_deg]
        _set_props(arm_name, props)
        bpy.context.view_layer.update()
        armatures[arm_name] = arm
    return armatures
