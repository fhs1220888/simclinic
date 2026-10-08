#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
SIMCLINIC_RUNTIME="${SIMCLINIC_PYTHON:-python3}"
"$SIMCLINIC_RUNTIME" -m simclinic demo --out demo-output
"$SIMCLINIC_RUNTIME" -m simclinic import fixtures/foam-agent-text --adapter foam-agent-text --metadata fixtures/foam-agent-text/metadata.json --out demo-output/foam-agent-import/record.json
"$SIMCLINIC_RUNTIME" -m simclinic audit demo-output/foam-agent-import/record.json --case fixtures/foam-agent-text --out demo-output/foam-agent-import
"$SIMCLINIC_RUNTIME" -m simclinic parse-log fixtures/healthy/case/log.simpleFoam --out demo-output/solver-observations.json
"$SIMCLINIC_RUNTIME" -m simclinic compare fixtures/stale/record.json fixtures/healthy/record.json --out demo-output/comparison.json
