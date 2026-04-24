import math
from pathlib import Path

import bpy
import gin


# Alles laden
def _load_all(blend_path: Path):
    with bpy.data.libraries.load(str(blend_path), link=False) as (src, dst):
        dst.objects = list(src.objects)
    for obj in dst.objects:
        if obj is not None:
            bpy.context.collection.objects.link(obj)


# Nur laden, was eine Armature hat
def _load_for_armatures(blend_path: Path, armature_names: list):
    with bpy.data.libraries.load(str(blend_path), link=False) as (src, dst):
        dst.objects = list(src.objects)
    for obj in dst.objects:
        if obj is not None:
            bpy.context.collection.objects.link(obj)

    armatures = {bpy.data.objects[n] for n in armature_names if n in bpy.data.objects}
    to_keep = set(armatures)
    for obj in (o for o in dst.objects if o is not None):
        for mod in obj.modifiers:
            if mod.type == "ARMATURE" and mod.object in armatures:
                to_keep.add(obj)
                break
        if obj.parent in armatures:
            to_keep.add(obj)

    for obj in (o for o in dst.objects if o is not None):
        if obj not in to_keep:
            bpy.data.objects.remove(obj, do_unlink=True)


def _set_props(arm_name: str, props: dict):
    arm = bpy.data.objects[arm_name]
    for key, value in (props or {}).items():
        if isinstance(value, (list, tuple)):
            for i, v in enumerate(value):
                arm[key][i] = v
        else:
            arm[key] = value


@gin.configurable
def place_or_equipment(
    blend_path: str,
    zeego_armature: str = "zeego_armature",
    zeego_location=(0.0, 0.0, 0.0),
    zeego_rotation_deg=(0.0, 0.0, 0.0),
    zeego_props=None,
    table_armature: str = "table_armature",
    table_location=(0.0, 0.0, 0.0),
    table_rotation_deg=(0.0, 0.0, 0.0),
    table_props=None,
):
    # _load_all(Path(blend_path))
    _load_for_armatures(Path(blend_path), [zeego_armature, table_armature])
    for arm_name, loc, rot_deg, props in [
        (zeego_armature, zeego_location, zeego_rotation_deg, zeego_props),
        (table_armature, table_location, table_rotation_deg, table_props),
    ]:
        arm = bpy.data.objects[arm_name]
        arm.location = loc
        arm.rotation_euler = [math.radians(d) for d in rot_deg]
        _set_props(arm_name, props)
    bpy.context.view_layer.update()
