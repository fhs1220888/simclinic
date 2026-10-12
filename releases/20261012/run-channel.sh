#!/bin/bash
# Runs only the existing healthy fixed channel, once per variant.
source /opt/openfoam10/etc/bashrc || exit 2
set -e
set -u
export OMP_NUM_THREADS=1
destination=${1:-/runs/channel-demo}
if [ -e "$destination" ]; then
    printf 'Output already exists: %s\nUse a new output directory.\n' "$destination" >&2
    exit 2
fi
mkdir -p "$destination"
python -I -B /kit/environment.py "$destination/environment.json"
python -I -B -m simclinic collect --example healthy --variant baseline --out "$destination/before" > "$destination/before.log" 2>&1
# Reuse only the same existing input files; old results are ignored by collect.
python -I -B -m simclinic collect --case "$destination/before/case" --variant patched --out "$destination/after" > "$destination/after.log" 2>&1
python -I -B -m simclinic audit "$destination/before/record.json" --case "$destination/before/case" --out "$destination/before/audit-again" --fail-on-finding
python -I -B -m simclinic audit "$destination/after/record.json" --case "$destination/after/case" --out "$destination/after/audit-again" --fail-on-finding
python -I -B -m simclinic compare "$destination/before/record.json" "$destination/after/record.json" --out "$destination/comparison.json"
python -I -B /kit/verify-channel.py "$destination"
