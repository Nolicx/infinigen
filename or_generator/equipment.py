import math
from pathlib import Path

import bpy
import gin

from infinigen.core.util import blender as butil


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
        dst.collections = list(src.collections)
        dst.objects = list(src.objects)

    child_colls = {c.name for coll in dst.collections if coll for c in coll.children}
    for coll in dst.collections:
        # if coll is not None and coll.name not in child_colls:
        #     bpy.context.scene.collection.children.link(coll)
        if coll is None or coll.name in child_colls:
            continue
        if len(coll.objects) == 0:
            for child in coll.children:
                bpy.context.scene.collection.children.link(child)
            bpy.data.collections.remove(coll)
        else:
            bpy.contect.scene.collection.children.link(coll)

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
        convert = "_rot" in key
        if isinstance(value, (list, tuple)):
            for i, v in enumerate(value):
                arm[key][i] = math.radians(v) if convert else v
        else:
            arm[key] = math.radians(value) if convert else value


def _armature_to_mesh(armature: bpy.types.Object) -> bpy.types.Object:
    """Converts an armature and all its child meshes into a single static mesh.

    Child meshes store their position relative to the armature parent. Before
    joining, the parent relationship is removed while preserving each mesh's
    world-space transform, so the final mesh ends up at the correct position.
    The armature is deleted afterwards.
    """
    meshes = [c for c in armature.children_recursive if c.type == "MESH"]
    for mesh in meshes:
        print(mesh.name, mesh.parent)
        world_matrix = mesh.matrix_world.copy()
        mesh.parent = None
        mesh.matrix_world = world_matrix
    butil.delete(armature)
    return butil.join_objects(meshes)


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
) -> dict:
    # _load_all(Path(blend_path))
    _load_for_armatures(Path(blend_path), [zeego_armature, table_armature])
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
