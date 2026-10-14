"""Foundation OpenFOAM 10 / simpleFoam offline evidence helpers."""
from .logs import parse_solver_log

TARGET = {"distribution": "OpenFOAM Foundation", "version": "10", "solver": "simpleFoam",
          "regime": "steady-incompressible-single-phase"}
