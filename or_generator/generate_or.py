import argparse
import logging
from pathlib import Path

logging.basicConfig(
    format="[%(asctime)s.%(msecs)03d] [%(module)s] [%(levelname)s] | %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)

import gin
from infinigen.core import execute_tasks, init
from infinigen.core.util import pipeline
from infinigen_examples.generate_indoors import compose_indoors  # noqa: E402
from or_generator.assets.equipment import place_or_equipment


@gin.configurable
def compose_or(output_folder, scene_seed, **overrides):
    result = compose_indoors(output_folder, scene_seed, **overrides)
    place_or_equipment()
    return result


def main(args):
    scene_seed = init.apply_scene_seed(args.seed)
    init.apply_gin_configs(
        configs=["base_or.gin"] + args.configs,
        overrides=args.overrides,
        config_folders=[
            "or_generator/configs",
            "infinigen_examples/configs_indoor",
            "infinigen_examples/configs_nature",
        ],
    )
    execute_tasks.main(
        compose_scene_func=compose_or,
        populate_scene_func=None,
        input_folder=args.input_folder,
        output_folder=args.output_folder,
        task=args.task,
        task_uniqname=args.task_uniqname,
        scene_seed=scene_seed,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output_folder", type=Path)
    parser.add_argument("--input_folder", type=Path, default=None)
    parser.add_argument("-s", "--seed", default=None)
    parser.add_argument("-t", "--task", nargs="+", default=["coarse"])
    parser.add_argument("-g", "--configs", nargs="+", default=[])
    parser.add_argument("-p", "--overrides", nargs="+", default=[])
    parser.add_argument("--task_uniqname", type=str, default=None)
    parser.add_argument("-d", "--debug", type=str, nargs="*", default=None)

    args = init.parse_args_blender(parser)
    main(args)
