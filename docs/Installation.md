
# Installation

## Installation Options & Supported Platforms

You can install Infinigen either as a Python Module or a Blender Python script:
- Python Module (default option)
  - Cannot open a Blender UI - headless execution only
  - Installs the `infinigen` package into the user's own python environment
  - Installs `bpy` as a [pip dependency](https://docs.blender.org/api/current/info_advanced_blender_as_bpy.html)
- Blender Python script 
  - Can use Infinigen interactively in the Blender UI
  - Installs the `infinigen` package into *Blender's* built-in python interpreter, not the user's python.
  - Uses a standard standalone installation of Blender.

In either case, certain features have limited support on some operating systems, as shown below:

| Feature Set        | Needed to generate...    | Linux x86_64 | Mac x86_64   | Mac ARM      | Windows x86_64 | Windows WSL2 x86_64 |
|--------------------|--------------------------|--------------|--------------|--------------|----------------|---------------------|
| Minimal Install.   | objects & materials      | yes          | yes          | yes          | experimental   | experimental        |
| Terrain (CPU)      | full scenes              | yes          | yes          | yes          | no             | experimental        |
| Terrain (CUDA)     | speedup, faster videos   | yes          | no           | no           | no             | experimental        |
| OpenGL Annotations | *additional* training GT | yes          | yes          | yes          | no             | experimental        |
| Fluid Simulation   | fires, simulated water   | yes          | experimental | experimental | no             | experimental        |

Users wishing to run our [Hello World Demo](./HelloWorld.md) or generate full scenes should install Infinigen as a Python Module and enable the Terrain (CPU) setting.
Users wishing to use Infinigen assets in the Blender UI, or develop their own assets, can install Infinigen as a Blender-Python script with the "Minimal Install" setting.

See our [Configuring Infinigen](./ConfiguringInfinigen.md), [Ground Truth Annotations ](./GroundTruthAnnotations.md), and [Fluid Simulation](./GeneratingFluidSimulations.md) docs for more information about the various optional features. Note: fields marked "experimental" are feasible but untested and undocumented. Fields marked "no" are largely _possible_ but not yet implemented.

Once you have chosen your configuration, proceed to the relevant section below for instructions.

## Installing Infinigen as a Python Module

### Dependencies

Please install [uv](https://docs.astral.sh/uv/getting-started/installation/).

Then, install the following system dependencies. Examples are shown for Ubuntu, Mac ARM and Mac x86.
```bash
# on Ubuntu / Debian / WSL / etc
sudo apt-get install wget cmake g++ libgles2-mesa-dev libglew-dev libglfw3-dev libglm-dev zlib1g-dev

# on an Mac ARM (M1/M2/...)
arch -arm64 brew install wget cmake llvm open-mpi libomp glm glew zlib

# on  Mac x86_64 (Intel)
brew install wget cmake llvm open-mpi libomp glm glew zlib
```

### Installation

Clone the repo and install:

```bash
git clone https://github.com/princeton-vl/infinigen.git
cd infinigen
```

Then install using one of the options below. uv will automatically create a `.venv` with Python 3.11.

```bash
# Minimal install (no terrain or OpenGL GT, ok for Infinigen-Indoors or single-object generation)
INFINIGEN_MINIMAL_INSTALL=True uv sync

# Full install (Terrain & OpenGL-GT enabled, needed for Infinigen-Nature HelloWorld)
uv sync --extra terrain --extra vis

# Installation for simulation assets
uv sync --extra sim

# Developer install (includes pytest, ruff, other recommended dev tools)
INFINIGEN_MINIMAL_INSTALL=True uv sync --extra dev --extra vis
pre-commit install
```

Activate the environment or use `uv run` to execute commands:
```bash
source .venv/bin/activate
# or directly:
uv run python -m infinigen ...
```

:exclamation: If you encounter any issues with the above, please add `-vv > logs.txt 2>&1` to the end of your command and run again, then provide the resulting logs.txt file as an attachment when making a Github Issue.

## Installing Infinigen as a Blender Python script

On Linux / Mac / WSL:
```bash
git clone https://github.com/princeton-vl/infinigen.git
cd infinigen
```

Activate your Python environment of choice, then install using one of the options below:
```bash

# Minimal installation (recommended setting for use in the Blender UI)
INFINIGEN_MINIMAL_INSTALL=True bash scripts/install/interactive_blender.sh

# Normal install
bash scripts/install/interactive_blender.sh

# Enable OpenGL GT
INFINIGEN_INSTALL_CUSTOMGT=True bash scripts/install/interactive_blender.sh
```

:exclamation: If you encounter any issues with the above, please add ` > logs.txt 2>&1` to the end of your command and run again, then provide the resulting logs.txt file as an attachment when making a Github Issue.

Once complete, you can use the helper script `python -m infinigen.launch_blender` to launch a blender UI, which will find and execute the `blender` executable in your `infinigen/blender` or `infinigen/Blender.app` folder.

:warning: If you installed Infinigen as a Blender-Python script and encounter encounter example commands of the form `python -m <MODULEPATH> <ARGUMENTS>` in our documentation, you should instead run `python -m infinigen.launch_blender -m <MODULEPATH> -- <ARGUMENTS>` to launch them using your standalone blender installation rather than the system python..

## Using Infinigen in a Docker Container

**Docker on Linux**

```
git clone https://github.com/princeton-vl/infinigen.git
cd infinigen
make docker-build
make docker-setup
make docker-run
```
To enable CUDA compilation, use `make docker-build-cuda` instead of `make docker-build`

To run without GPU passthrough use `make docker-run-no-gpu`
To run without OpenGL ground truth use `docker-run-no-opengl` 
To run without either, use `docker-run-no-gpu-opengl` 

Note: `make docker-setup` can be skipped if not using OpenGL.

Use `exit` to exit the container and `docker exec -it infinigen bash` to re-enter the container as needed.

**Docker on Windows**

Install [WSL2](https://infinigen.org/docs/installation/intro#setup-for-windows) and [Docker Desktop](https://www.docker.com/products/docker-desktop/), with "Use the WSL 2 based engine..." enabled in settings. Keep the Docker Desktop application open while running containers. Then follow instructions as above.
