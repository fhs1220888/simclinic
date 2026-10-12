"""A specified low-Re channel benchmark, independent of Agent suggestions."""
from pathlib import Path

SPEC = {"length": 1.0, "height": 0.1, "width": 0.01, "mean_velocity": 0.01,
        "nu": 0.001, "nx": 40, "ny": 20, "profile_relative_l2_tolerance": 0.02,
        "pressure_gradient_relative_tolerance": 0.03, "continuity_tolerance": 1e-7}


def dictionary(name, body, cls="dictionary"):
    return f"FoamFile\n{{ version 2.0; format ascii; class {cls}; object {name}; }}\n\n{body}\n"


def create_channel(directory):
    directory = Path(directory)
    if directory.exists():
        raise ValueError("Channel destination already exists; use a new directory")
    for folder in ("0", "constant", "system"):
        (directory / folder).mkdir(parents=True, exist_ok=True)
    def write(folder, name, body, cls="dictionary"):
        (directory / folder / name).write_text(dictionary(name, body, cls))
    write("system", "blockMeshDict", """
convertToMeters 1;
vertices ((0 0 0) (1 0 0) (1 0.1 0) (0 0.1 0) (0 0 0.01) (1 0 0.01) (1 0.1 0.01) (0 0.1 0.01));
blocks (hex (0 1 2 3 4 5 6 7) (40 20 1) simpleGrading (1 1 1));
edges ();
boundary (
 inlet {type patch; faces ((0 4 7 3));}
 outlet {type patch; faces ((1 2 6 5));}
 walls {type wall; faces ((0 1 5 4) (3 7 6 2));}
 frontAndBack {type empty; faces ((0 3 2 1) (4 5 6 7));}
);
mergePatchPairs ();
""")
    # Uniform block inlet has twenty faces. The symmetric profile gives the
    # same sequence in either y orientation; no dynamic compilation required.
    write("0", "U", """
dimensions [0 1 -1 0 0 0 0]; internalField uniform (0.01 0 0);
boundaryField {
 inlet { type fixedValue; value nonuniform List<vector> 20 (_PROFILE_VALUES_); }
 outlet {type zeroGradient;}
 walls {type fixedValue; value uniform (0 0 0);}
 frontAndBack {type empty;}
}
""".replace("_PROFILE_VALUES_", " ".join(f"({6*0.01*((i+0.5)/20)*(1-(i+0.5)/20):.12g} 0 0)" for i in range(20))), "volVectorField")
    write("0", "p", """
dimensions [0 2 -2 0 0 0 0]; internalField uniform 0;
boundaryField { inlet {type zeroGradient;} outlet {type fixedValue; value uniform 0;}
 walls {type zeroGradient;} frontAndBack {type empty;} }
""", "volScalarField")
    write("constant", "physicalProperties", "viscosityModel constant;\nnu 0.001;")
    write("constant", "momentumTransport", "simulationType laminar;")
    write("system", "fvSchemes", """
ddtSchemes {default steadyState;}
gradSchemes {default Gauss linear;}
divSchemes {default none; div(phi,U) Gauss linear; div((nuEff*dev2(T(grad(U))))) Gauss linear;}
laplacianSchemes {default Gauss linear corrected;}
interpolationSchemes {default linear;}
snGradSchemes {default corrected;}
fluxRequired {default no; p;}
""")
    write("system", "fvSolution", """
solvers {
 p {solver GAMG; tolerance 1e-10; relTol 0.01; smoother GaussSeidel;}
 U {solver smoothSolver; smoother symGaussSeidel; tolerance 1e-10; relTol 0.01;}
}
SIMPLE {nNonOrthogonalCorrectors 0; residualControl {p 1e-7; U 1e-7;}}
relaxationFactors {fields {p 0.3;} equations {U 0.7;}}
""")
    write("system", "controlDict", """
application simpleFoam; startFrom startTime; startTime 0; stopAt endTime; endTime 2000;
deltaT 1; writeControl timeStep; writeInterval 2000; purgeWrite 0;
writeFormat ascii; writePrecision 12; writeCompression off; timeFormat general; timePrecision 6;
runTimeModifiable false;
""")
    write("system", "sample", """
type sets; libs ("libsampling.so"); interpolationScheme cellPoint;
setFormat raw; fields (U p);
sets (
 profile {type lineUniform; axis y; start (0.5 0.0025 0.005); end (0.5 0.0975 0.005); nPoints 20;}
 pressure {type lineUniform; axis x; start (0.1 0.05 0.005); end (0.9 0.05 0.005); nPoints 17;}
);
""")
    (directory / "Allrun").write_text("#!/bin/bash\nset -eu\nsimpleFoam > log.simpleFoam 2>&1\n")
    return directory
