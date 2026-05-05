import shapely
from infinigen.core.constraints.example_solver.room.base import room_name
from infinigen.core.tags import Semantics
from shapely.geometry import LineString


def angio_room(seed):
    """
    Simple rebuild of angiography room at UMM

    Dimensions:

    Wall thickness = 0.15

    x1: 0
    x2: 9.75 + 2*0.15
    y1: 0
    y2: 6.0 + 2*0.15
    """
    return {
        "rooms": {
            room_name(Semantics.Utility, 0): {
                "shape": shapely.box(
                    0, 0, 10.05, 6.3
                )  # Außenmaße, also Innenraum + Wände
            },  ## 9.75 + 2×0.15, 6.0 + 2×0.15
        },
        "windows": {
            "window": {
                "shape": LineString([(3.025, 6.3), (6.025, 6.3)]),
                "is_panoramic": False,
            }
        },
        "doors": {
            "door": {
                "shape": LineString(
                    [(2.5, 0), (3.5, 0)]
                )  # 1m breite Tür auf der Südwand
            }
        },
    }
