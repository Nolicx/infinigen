# Copyright (C) 2024, Princeton University.
# This source code is licensed under the BSD 3-Clause license found in the LICENSE file in the root directory
# of this source tree.

# Authors:
# - Lingjie Mei: primary author
# - Karhan Kayan: fix constants

import logging
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping

import bpy
import gin
import numpy as np
import shapely
from numpy.random import uniform
from shapely import LineString, line_interpolate_point
from shapely.ops import linemerge

from infinigen.assets.utils.decorate import (
    read_center,
    read_co,
    write_attribute,
    write_co,
)
from infinigen.assets.utils.mesh import canonicalize_mls, prepare_for_boolean
from infinigen.assets.utils.object import new_cube
from infinigen.assets.utils.shapes import buffer, polygon2obj, simplify_polygon
from infinigen.core import tagging
from infinigen.core import tags as t
from infinigen.core.constraints import constraint_language as cl
from infinigen.core.constraints.example_solver.state_def import (
    ObjectState,
    RelationState,
    State,
)
from infinigen.core.surface import read_attr_data, write_attr_data
from infinigen.core.tagging import PREFIX
from infinigen.core.tags import Semantics
from infinigen.core.util import blender as butil
from infinigen.core.util.random import random_general as rg

from .base import RoomGraph, room_type, valid_rooms
from .utils import mls_ccw
from shapely.geometry import Polygon

logger = logging.getLogger(__name__)

_eps = 1e-5
_snap = 0.5

panoramic_rooms = defaultdict(
    float,
    {
        Semantics.Hallway: ("bool", 0.1),
        Semantics.Balcony: ("bool", 0.8),
        Semantics.OpenOffice: ("bool", 0.2),
        Semantics.Office: ("bool", 0.2),
        Semantics.MeetingRoom: ("bool", 0.2),
        Semantics.BreakRoom: ("bool", 0.2),
        Semantics.Garage: ("bool", 1),
    },
)

combined_rooms = [
    ({Semantics.Bedroom}, {"non-adjacent": "none", "adjacent": "none"}),
    (
        {Semantics.Hallway, Semantics.OpenOffice},
        {"non-adjacent": "open", "adjacent": "open"},
    ),
    (
        {
            Semantics.Hallway,
            Semantics.LivingRoom,
            Semantics.DiningRoom,
            Semantics.StaircaseRoom,
        },
        {
            "non-adjacent": (
                "weighted_choice",
                (0.3, "open"),
                (0.3, "panoramic"),
                (0.4, "door"),
            ),
            "adjacent": ("weighted_choice", (0.5, "open"), (0.5, "door")),
        },
    ),
    (
        {Semantics.Garage, Semantics.Warehouse},
        {"non-adjacent": "open", "adjacent": "open"},
    ),
    (
        {Semantics.DiningRoom, Semantics.Kitchen},
        {
            "non-adjacent": (
                "weighted_choice",
                (0.3, "open"),
                (0.3, "panoramic"),
                (0.4, "door"),
            ),
            "adjacent": "open",
        },
    ),
    (
        {Semantics.Balcony, Semantics.LivingRoom, Semantics.Hallway},
        {
            "non-adjacent": (
                "weighted_choice",
                (0.3, "open"),
                (0.3, "panoramic"),
                (0.4, "door"),
            ),
            "adjacent": ("weighted_choice", (0.5, "open"), (0.5, "door")),
        },
    ),
    (
        {Semantics.Balcony, Semantics.Bedroom},
        {
            "non-adjacent": (
                "weighted_choice",
                (0.3, "open"),
                (0.3, "panoramic"),
                (0.4, "door"),
            ),
            "adjacent": "door",
        },
    ),
    (
        {Semantics.OpenOffice, Semantics.Hallway},
        {"non-adjacent": "open", "adjacent": "open"},
    ),
    (
        {
            Semantics.MeetingRoom,
            Semantics.BreakRoom,
            Semantics.Hallway,
            Semantics.OpenOffice,
        },
        {
            "non-adjacent": ("weighted_choice", (0.5, "window"), (0.5, "door")),
            "adjacent": "door",
        },
    ),
]

window_rooms = defaultdict(
    lambda: 1.0,
    {
        Semantics.Utility: ("bool", 0.3),
        Semantics.Closet: 0.0,
        Semantics.Bathroom: ("bool", 0.5),
        Semantics.Garage: 0.0,
        Semantics.Warehouse: 0.0,
    },
)

wall_cut_prob = "bool", 0.5


def split_mls(mls, min_length=-np.inf):
    lss = mls.geoms if mls.geom_type == "MultiLineString" else [mls]
    for ls in lss:
        for (x, y), (x_, y_) in zip(ls.coords[:-1], ls.coords[1:]):
            if np.linalg.norm((x - x_, y - y_)) > min_length:
                yield x, y, x_, y_


def max_mls(mls):
    lss = mls.geoms if mls.geom_type == "MultiLineString" else [mls]
    coords = []
    lengths = []
    for ls in lss:
        for (x, y), (x_, y_) in zip(ls.coords[:-1], ls.coords[1:]):
            lengths.append(np.linalg.norm((x - x_, y - y_)))
            coords.append((x, y, x_, y_))
    return coords[np.argmax(lengths)]


@gin.configurable
class BlueprintSolidifier:
    def __init__(self, consgraph, graph: RoomGraph, level, enable_open=True):
        self.constants = consgraph.constants
        self.consgraph = consgraph
        self.graph = graph
        self.level = level
        self.enable_open = enable_open

        self._wall_panels = {}

    @staticmethod
    def unroll(x):
        for k, cs in x.items():
            if isinstance(cs, Mapping):
                for l, c in cs.items():
                    if k < l:
                        if isinstance(c, Iterable):
                            for cc in c:
                                yield (k, l), cc
                        else:
                            yield (k, l), c
            elif isinstance(cs, Iterable):
                for c in cs:
                    yield (k,), c
            else:
                yield (k,), cs

    def solidify(self, state):
        wt = self.constants.wall_thickness
        segments = {k: obj_st.polygon for k, obj_st in valid_rooms(state)}
        shared_edges = {
            k: {l.target_name: canonicalize_mls(l.value) for l in obj_st.relations}
            for k, obj_st in valid_rooms(state)
        }
        exterior = next(k for k in state.objs if room_type(k) == Semantics.Exterior)
        exterior_edges = {
            r.target_name: mls_ccw(canonicalize_mls(r.value), state, r.target_name)
            for r in state[exterior].relations
        }
        exterior_buffer = shapely.simplify(
            state[exterior].polygon.buffer(-wt / 2 - _eps, join_style="mitre"), 1e-3
        )
        exterior_shape = state[exterior].polygon

        rooms = {k: self.make_room(state, k) for k, _ in valid_rooms(state)}

        wall_col = butil.get_collection("unique_assets:room_wall")
        for panels in self._wall_panels.values():
            for panel, _, _ in panels:
                butil.put_in_collection(panel, wall_col)

        valid_neighbours = (
            self.graph.valid_neighbours if self.graph is not None else None
        )
        open_cutters, door_cutters, interior_cutters = self.make_interior_cutters(
            valid_neighbours, shared_edges, segments, exterior_buffer
        )
        window_cutters, entrance_cutters = self.make_exterior_cutters(
            exterior_edges, exterior_shape
        )
        all_cutter_lists = [
            open_cutters,
            door_cutters,
            window_cutters,
            entrance_cutters,
        ]

        w = self.constants.wall_height
        for k, r in rooms.items():
            r.location[-1] += w * self.level
        for cutters in all_cutter_lists:
            for k, c in self.unroll(cutters):
                c.location[-1] += w * self.level

        butil.put_in_collection(rooms.values(), "placeholders:room_shells")
        rooms_ = rooms

        def clone_as_meshed(o):
            new = butil.copy(o)
            new.name = o.name + ".meshed"
            return new

        rooms = {k: clone_as_meshed(r) for k, r in rooms.items()}
        state = self.convert_solver_state(
            rooms_,
            segments,
            shared_edges,
            open_cutters,
            door_cutters,
            window_cutters,
            interior_cutters,
            entrance_cutters,
        )
        for obj in rooms_.values():
            tagging.tag_object(obj)
        for panels in self._wall_panels.values():
            for panel, _, _ in panels:
                tagging.tag_object(panel)

        # Cut windows & doors from final room meshes
        cutter_col = butil.get_collection("placeholders:portal_cutters")
        for cutters in all_cutter_lists:
            for k, c in self.unroll(cutters):
                butil.put_in_collection(c, cutter_col)
                for k_ in k:
                    # obj = rooms[k_]
                    if k_ in self._wall_panels:
                        obj = find_wall_panel(
                            self._wall_panels[k_], np.array(c.location[:2])
                        )
                    else:
                        obj = rooms[k_]
                    logger.debug(f"Cutting {c.name} from {obj.name}")
                    before = len(obj.data.polygons)
                    prepare_for_boolean(obj)
                    prepare_for_boolean(c)
                    butil.modify_mesh(
                        obj,
                        "BOOLEAN",
                        object=c,
                        operation="DIFFERENCE",
                        use_self=True,
                        use_hole_tolerant=True,
                    )
                    prepare_for_boolean(obj)
                    prepare_for_boolean(c)
                    after = len(obj.data.polygons)
                    logger.debug(
                        f"Cutting {c.name} from {obj.name}, {before=} {after=}"
                    )

        for obj in rooms.values():
            butil.modify_mesh(obj, "TRIANGULATE", min_vertices=3)
            co = read_co(obj)
            # m = wt / 2 + _snap
            # low = np.abs(co[:, -1] - m) < _eps
            # high = np.abs(co[:, -1] - self.constants.wall_height + m) < _eps
            # co[:, -1] = np.where(low, wt / 2, co[:, -1])
            # co[:, -1] = np.where(high, self.constants.wall_height - wt / 2, co[:, -1])
            ft = self.constants.floor_thickness
            ct = self.constants.ceiling_thickness
            m = max(ft, ct) + _snap
            low = np.abs(co[:, -1] - m) < _eps
            high = np.abs(co[:, -1] - self.constants.wall_height + m) < _eps
            co[:, -1] = np.where(low, ft, co[:, -1])
            co[:, -1] = np.where(high, self.constants.wall_height - ct, co[:, -1])
            write_co(obj, co)
            tagging.tag_object(obj)

        for obj in cutter_col.objects:
            offset = np.array(obj.location)[np.newaxis, :]
            offset[:, 2] -= w * self.level
            co = read_co(obj) + offset
            # m = wt / 2 + _snap
            # low = np.abs(co[:, -1] - m) < _eps
            # high = np.abs(co[:, -1] - self.constants.wall_height + m) < _eps
            # co[:, -1] = np.where(low, wt / 2, co[:, -1])
            # co[:, -1] = np.where(high, self.constants.wall_height - wt / 2, co[:, -1])
            ft = self.constants.floor_thickness
            ct = self.constants.ceiling_thickness
            m = max(ft, ct) + _snap
            low = np.abs(co[:, -1] - m) < _eps
            high = np.abs(co[:, -1] - self.constants.wall_height + m) < _eps
            co[:, -1] = np.where(low, ft, co[:, -1])
            co[:, -1] = np.where(high, self.constants.wall_height - ct, co[:, -1])
            write_co(obj, co - offset)
            tagging.tag_object(obj)

        butil.group_in_collection(rooms.values(), "placeholders:room_meshes")
        return state, rooms

    def convert_solver_state(
        self,
        rooms,
        segments,
        shared_edges,
        open_cutters,
        door_cutters,
        window_cutters,
        interior_cutters,
        entrance_cutters,
    ):
        obj_states = {}
        for k, o in rooms.items():
            obj_states[o.name] = ObjectState(
                obj=o,
                tags={t.Semantics.Room, t.SpecificObject(o.name), room_type(o.name)},
                polygon=segments[k],
            )
        for k, r in rooms.items():
            relations = obj_states[r.name].relations
            for other in shared_edges[k]:
                if other in open_cutters[k]:
                    ct = cl.ConnectorType.Open
                elif other in door_cutters[k]:
                    ct = cl.ConnectorType.Door
                else:
                    ct = cl.ConnectorType.Wall
                relations.append(
                    RelationState(cl.RoomNeighbour({ct}), rooms[other].name)
                )

        all_cutters = [
            door_cutters,
            open_cutters,
            window_cutters,
            interior_cutters,
            entrance_cutters,
        ]
        tag_cutters = [
            t.Semantics.Door,
            t.Semantics.Open,
            t.Semantics.Window,
            t.Semantics.Window,
            t.Semantics.Door,
        ]
        for cutters, tag in zip(all_cutters, tag_cutters):
            for k, c in self.unroll(cutters):
                obj_states[c.name] = ObjectState(
                    obj=c,
                    tags={tag, t.Semantics.Cutter},
                    relations=[RelationState(cl.CutFrom(), rooms[k_].name) for k_ in k],
                )

        return State(objs=obj_states)

    def make_room(self, state, name):
        obj_st = state.objs[name]
        ft = self.constants.floor_thickness
        ct = self.constants.ceiling_thickness
        wt = self.constants.wall_thickness
        wh = self.constants.wall_height
        interior_height = wh - ft - ct

        # So kann ein Raum durch die Innengröße, also excluding der Wände, Decke und Boden (Innenmaße) angegeben werden.
        # Bei mehreren Räumen überlappen dann aber die Wände ineinander
        # inner_poly = shapely.segmentize(
        #     self.constants.canonicalize(obj_st.polygon), self.constants.door_width
        # )
        # outer_poly = inner_poly.buffer(wt, join_style="mitre")

        # So gibt man die Gesamtmaße, also Innen + Wände/Decke/Böden an, aber bei mehreren Räumen gibts keine Überlappungen
        base_poly = self.constants.canonicalize(obj_st.polygon)

        room_poly = shapely.segmentize(base_poly, self.constants.door_width)
        outer_poly = room_poly
        outer_wall = base_poly
        # inner_poly = base_poly.buffer(-wt, join_style="mitre")

        # Floor slab: inner polygon, z=0 bis z=ft
        floor_obj = polygon2obj(outer_poly, reversed=True, dissolve=False)
        butil.modify_mesh(floor_obj, "WELD", merge_threshold=0.01)
        butil.modify_mesh(floor_obj, "SOLIDIFY", thickness=ft, offset=-1)

        # Wall ring: outer box minus inner box (Boolean), z=ft bis z=wh-ct
        outer_coords = list(outer_wall.exterior.coords[:-1])
        wall_panels = []
        for i in range(len(outer_coords)):
            i_next = (i + 1) % len(outer_coords)
            a = np.array(outer_coords[i][:2])
            b = np.array(outer_coords[i_next][:2])
            edge = b - a
            # Einwärts-Normale für CCW-Polygon (linke Normale)
            n = np.array([-edge[1], edge[0]])
            n = n / np.linalg.norm(n) * wt
            panel_poly = Polygon([tuple(a), tuple(b), tuple(b + n), tuple(a + n)])
            panel = polygon2obj(panel_poly, z=ft, reversed=True, dissolve=False)
            butil.modify_mesh(panel, "WELD", merge_threshold=0.01)
            butil.modify_mesh(panel, "SOLIDIFY", thickness=interior_height, offset=-1)
            wall_panels.append(panel)

        for i, panel in enumerate(wall_panels):
            panel.name = f"{name}.wall.{i:02d}"

        # Ceiling slab: inner polygon, z=wh-ct bis z=wh
        ceil_obj = polygon2obj(outer_poly, z=wh - ct, reversed=True, dissolve=False)
        butil.modify_mesh(ceil_obj, "WELD", merge_threshold=0.01)
        butil.modify_mesh(ceil_obj, "SOLIDIFY", thickness=ct, offset=-1)

        # Exterior-Erkennung (für Außenwände)
        exterior = next(k for k in state.objs if room_type(k) == Semantics.Exterior)
        exterior_edges = list(
            r.value for r in state.objs[exterior].relations if r.target_name == name
        )
        exterior_centers = []
        for ee in exterior_edges:
            for ls in ee.geoms:
                for u, v in zip(ls.coords[:-1], ls.coords[1:]):
                    exterior_centers.append(((u[0] + v[0]) / 2, (u[1] + v[1]) / 2))
        panel_midpoints = [
            (
                (outer_coords[i][0] + outer_coords[(i + 1) % len(outer_coords)][0]) / 2,
                (outer_coords[i][1] + outer_coords[(i + 1) % len(outer_coords)][1]) / 2,
            )
            for i in range(len(outer_coords))
        ]
        if exterior_centers:
            is_exterior_panel = [
                (
                    np.abs(np.array(mid) - np.array(exterior_centers)).sum(-1) < wt * 4
                ).any()
                for mid in panel_midpoints
            ]
        else:
            is_exterior_panel = [False] * len(wall_panels)

        # Tagging
        def tag_slab(obj, floor=False, wall=False, ceiling=False, exterior_mask=None):
            n = len(obj.data.polygons)
            full = np.ones(n, bool)
            empty = np.zeros(n, bool)
            write_attr_data(
                obj,
                f"{PREFIX}{t.Subpart.SupportSurface.value}",
                full if floor else empty,
                "BOOLEAN",
                "FACE",
            )
            write_attr_data(
                obj,
                f"{PREFIX}{t.Subpart.Ceiling.value}",
                full if ceiling else empty,
                "BOOLEAN",
                "FACE",
            )
            write_attr_data(
                obj,
                f"{PREFIX}{t.Subpart.Wall.value}",
                full if wall else empty,
                "BOOLEAN",
                "FACE",
            )
            write_attr_data(
                obj, f"{PREFIX}{t.Subpart.Visible.value}", full, "BOOLEAN", "FACE"
            )
            interior = ~exterior_mask if exterior_mask is not None else full
            write_attr_data(
                obj, f"{PREFIX}{t.Subpart.Interior.value}", interior, "BOOLEAN", "FACE"
            )

        tag_slab(floor_obj, floor=True)
        tag_slab(ceil_obj, ceiling=True)
        # tag_slab(wall_obj, wall=True, exterior_mask=is_exterior)
        for panel, is_ext in zip(wall_panels, is_exterior_panel):
            n = len(panel.data.polygons)
            exterior_mask = np.ones(n, bool) if is_ext else np.zeros(n, bool)
            tag_slab(panel, wall=True, exterior_mask=exterior_mask)
            self._wall_panels[name] = [
                (
                    panel,
                    np.array(outer_coords[i][:2]),
                    np.array(outer_coords[(i + 1) % len(outer_coords)][:2]),
                )
                for i, panel in enumerate(wall_panels)
            ]

        obj = butil.join_objects([floor_obj, ceil_obj])
        obj.name = name
        assert len(obj.data.vertices) > 0
        return obj

    def make_interior_cutters(self, neighbours, shared_edges, segments, exterior):
        name_groups = {}
        for k in shared_edges:
            name_groups[k] = set(
                i for i, (rt, _) in enumerate(combined_rooms) if room_type(k) in rt
            )
        dist2entrance = self.compute_dist2entrance(neighbours)
        centroids = {k: np.array(s.centroid.coords[0]) for k, s in segments.items()}
        open_cutters, door_cutters, interior_cutters = (
            defaultdict(dict),
            defaultdict(dict),
            defaultdict(dict),
        )
        for k, ses in shared_edges.items():
            for l, se in ses.items():
                if k >= l or se.length <= self.constants.segment_margin:
                    continue
                direction = (centroids[k] - centroids[l]) * (
                    1 if dist2entrance[k] > dist2entrance[l] else -1
                )
                i = name_groups[k].intersection(name_groups[l])
                if len(i) > 0 and self.enable_open:
                    group = combined_rooms[next(iter(i))][1]
                    fn = rg(
                        group["adjacent"]
                        if k in neighbours[l]
                        else group["non-adjacent"]
                    )
                else:
                    fn = "door" if k in neighbours[l] else "none"
                match fn:
                    case "open":
                        open_cutters[k][l] = open_cutters[l][k] = self.make_open_cutter(
                            se, exterior
                        )
                    case "door":
                        door_cutters[k][l] = door_cutters[l][k] = self.make_door_cutter(
                            se, direction
                        )
                    case "window":
                        interior_cutters[k][l] = interior_cutters[l][k] = (
                            self.make_window_cutter(se, False)
                        )
                    case "panoramic":
                        interior_cutters[k][l] = interior_cutters[l][k] = (
                            self.make_window_cutter(se, self.level == 0)
                        )
        return open_cutters, door_cutters, interior_cutters

    def compute_dist2entrance(self, neighbours):
        root = self.graph.root
        queue = deque([root])
        dist2living_room = {root: 0}
        while len(queue) > 0:
            node = queue.popleft()
            for n in neighbours[node]:
                if n not in dist2living_room:
                    dist2living_room[n] = dist2living_room[node] + 1
                    queue.append(n)
        return dist2living_room

    def make_exterior_cutters(self, exterior_edges, exterior):
        window_cutters = defaultdict(list)
        entrance_cutters = defaultdict(list)
        entrance = self.graph.entrance

        for k, mls in exterior_edges.items():
            if k == entrance and self.level == 0:
                continue
            for ls in mls.geoms:
                ls = ls.segmentize(self.constants.max_window_length)
                buffered = ls.buffer(0.1, single_sided=True)
                if buffered.intersection(exterior).area < buffered.area / 2:
                    ls = LineString(ls.coords[::-1])
                cutters = self.make_window_cutter(ls, panoramic_rooms[room_type(k)])
                window_cutters[k].extend(cutters)
        for k, mls in exterior_edges.items():
            if k == entrance and self.level == 0:
                x, y, x_, y_ = max_mls(mls)
                ls = LineString([(x, y), (x_, y_)])
                cutter = self.make_entrance_cutter(ls)
                entrance_cutters[k].append(cutter)
                mls = mls.difference(ls)
                if mls.length > 0:
                    cutters = self.make_window_cutter(mls, False)
                    window_cutters[k].extend(cutters)
        return window_cutters, entrance_cutters

    def make_staircase_cutters(self, staircase, names):
        cutters = defaultdict(list)
        if self.level > 0:
            for k, name in names.items():
                if room_type(name) == Semantics.StaircaseRoom:
                    with np.errstate(invalid="ignore"):
                        cutter = polygon2obj(
                            buffer(staircase, -self.constants.wall_thickness / 2)
                        )
                    butil.modify_mesh(
                        cutter,
                        "SOLIDIFY",
                        thickness=self.constants.wall_thickness * 1.2,
                        offset=0,
                    )
                    cutter.name = "staircase_cutter"
                    self.tag(cutter)
                    cutters[k].append(cutter)
        return cutters

    def make_door_cutter(self, mls, direction):
        m = self.constants.door_margin + self.constants.door_width / 2
        x, y, x_, y_ = max_mls(mls)
        cutter = new_cube()
        vertical = np.abs(x - x_) < 0.1
        wt = self.constants.wall_thickness
        ft = self.constants.floor_thickness
        cutter.scale = (
            self.constants.door_width / 2,
            self.constants.door_width + wt / 2,
            self.constants.door_size / 2 + ft / 4,
        )
        # cutter.location[-1] -= ft / 4
        butil.apply_transform(cutter, True)
        if vertical:
            y = uniform(min(y, y_) + m, max(y, y_) - m)
            z_rot = -np.pi / 2 if direction[0] > 0 else np.pi / 2
            dx = x_ - x
            dy = y_ - y
            length = np.linalg.norm([dx, dy])
            x += -(dy / length) * wt
            y += (dx / length) * wt
        else:
            x = uniform(min(x, x_) + m, max(x, x_) - m)
            z_rot = 0 if direction[1] > 0 else np.pi
            dx = x_ - x
            dy = y_ - y
            length = np.linalg.norm([dx, dy])
            x += -(dy / length) * wt
            y += (dx / length) * wt
        cutter.location = (
            x,
            y,
            self.constants.door_size / 2 + ft,
        )
        cutter.rotation_euler[-1] = z_rot
        cutter.name = t.Semantics.Door.value
        self.tag(cutter)
        return cutter

    def make_entrance_cutter(self, mls):
        x, y, x_, y_ = max_mls(mls)
        cutter = new_cube()
        length = np.linalg.norm([y_ - y, x_ - x])
        m = self.constants.door_margin + self.constants.door_width / 2
        lam = uniform(m / length, 1 - m / length)
        wt = self.constants.wall_thickness
        ft = self.constants.floor_thickness
        cutter.scale = (
            self.constants.door_width / 2,
            self.constants.door_width / 2 + wt,
            self.constants.door_size / 2 + ft / 2,  # _snap / 2,
        )
        # cutter.location[-1] += _snap / 2
        butil.apply_transform(cutter, True)
        cutter.location = (
            lam * x + (1 - lam) * x_,
            lam * y + (1 - lam) * y_,
            self.constants.door_size / 2 + ft,  # self.constants.floor_thickness / 2,
        )
        cutter.rotation_euler = 0, 0, np.arctan2(y_ - y, x_ - x)
        cutter.name = t.Semantics.Entrance.value
        self.tag(cutter)
        return cutter

    def make_window_cutter(self, mls, is_panoramic):
        cutters = []
        for x, y, x_, y_ in split_mls(mls, self.constants.door_width):
            length = np.linalg.norm([y_ - y, x_ - x])
            wt = self.constants.wall_thickness
            wm = self.constants.window_margin
            ft = self.constants.floor_thickness
            ct = self.constants.ceiling_thickness

            if rg(is_panoramic) and self.constants.wall_height < 4:
                x_scale = length / 2 - wm
                lam = 1 / 2
                z_scale = (self.constants.wall_height - max(ft, ct) - ft) / 2
                z_loc = ft + z_scale
            else:
                x_scale = uniform(self.constants.door_width / 2, length / 2 - wm)
                m = (x_scale + wm) / length
                lam = uniform(m, 1 - m)
                z_scale = self.constants.window_size / 2
                z_loc = z_scale + self.constants.window_height + ft

            cutter = new_cube()
            cutter.scale = x_scale, wt, z_scale
            butil.apply_transform(cutter)

            dx = x_ - x
            dy = y_ - y
            length = np.linalg.norm([dx, dy])
            nx = -(dy / length) * wt
            ny = (dx / length) * wt
            cutter.location = (
                lam * x + (1 - lam) * x_ - nx,
                lam * y + (1 - lam) * y_ - ny,
                z_loc,
            )
            cutter.rotation_euler = 0, 0, np.arctan2(y - y_, x - x_)
            cutter.name = t.Semantics.Window.value
            self.tag(cutter)
            cutters.append(cutter)
        return cutters

    def make_open_cutter(self, es, exterior=None):
        es = simplify_polygon(es)
        es = shapely.remove_repeated_points(
            linemerge(es) if not isinstance(es, LineString) else es, 0.01
        )
        es = [es] if isinstance(es, LineString) else es.geoms
        lines = []
        wt = self.constants.wall_thickness
        ft = self.constants.floor_thickness
        ct = self.constants.ceiling_thickness
        for ls in es:
            coords = np.array(ls.coords[:])
            if len(coords) < 2:
                continue
            coords[0] = line_interpolate_point(
                LineString(coords[0:2]), wt / 2 + _eps
            ).coords[0]
            coords[-1] = line_interpolate_point(
                LineString(coords[-1:-3:-1]), wt / 2 + _eps
            ).coords[0]
            lines.append(coords)
        line = shapely.simplify(
            shapely.remove_repeated_points(shapely.MultiLineString(lines), 0.01), 0.01
        )

        p = line.buffer(wt, cap_style="flat", join_style="mitre")
        if exterior is not None:
            p = p.intersection(exterior)
        cutters = []
        for p in [p] if p.geom_type == "Polygon" else p.geoms:
            cutter = polygon2obj(p, True)

            with butil.ViewportMode(cutter, "EDIT"):
                bpy.ops.mesh.select_all(action="SELECT")
                bpy.ops.mesh.extrude_region_move(
                    TRANSFORM_OT_translate={
                        "value": (
                            0,
                            0,
                            self.constants.wall_height - 2 * max(ft, ct) - 2 * _snap,
                        )
                    }
                )
                bpy.ops.mesh.select_mode(type="FACE")
                bpy.ops.mesh.select_all(action="SELECT")
                bpy.ops.mesh.normals_make_consistent(inside=False)
            cutter.location[-1] += ft + _snap
            cutter.name = t.Semantics.Open.value
            self.tag(cutter)
            cutters.append(cutter)
        return cutters

    def tag(self, obj, visible=True):
        center = read_center(obj) + obj.location
        high = self.constants.wall_height - self.constants.ceiling_thickness
        z = center[:, -1]
        low = self.constants.ceiling_thickness
        ceiling = (z > high - _eps) | (np.abs(z - high + _snap) < _eps)
        floor = (z < low + _eps) | (np.abs(z - low - _snap) < _eps)
        wall = ~(ceiling | floor)
        write_attr_data(
            obj, f"{PREFIX}{t.Subpart.Ceiling.value}", ceiling, "BOOLEAN", "FACE"
        )
        write_attr_data(
            obj, f"{PREFIX}{t.Subpart.SupportSurface.value}", floor, "BOOLEAN", "FACE"
        )
        write_attr_data(obj, f"{PREFIX}{t.Subpart.Wall.value}", wall, "BOOLEAN", "FACE")
        full = np.ones_like(ceiling)
        if visible:
            write_attr_data(
                obj, f"{PREFIX}{t.Subpart.Visible.value}", full, "BOOLEAN", "FACE"
            )
        else:
            write_attr_data(
                obj, f"{PREFIX}{t.Subpart.Visible.value}", ~full, "BOOLEAN", "FACE"
            )
        write_attr_data(
            obj, f"{PREFIX}{t.Subpart.Interior.value}", full, "BOOLEAN", "FACE"
        )


def find_wall_panel(panels_with_edges, cutter_xy):
    return min(
        panels_with_edges,
        key=lambda x: _point_to_segment_dist(cutter_xy, x[1], x[2]),
    )[0]


def _point_to_segment_dist(p, a, b):
    ab = b - a
    t = np.clip(np.dot(p - a, ab) / np.dot(ab, ab), 0, 1)
    return np.linalg.norm(p - (a + t * ab))
