"""Strict stdlib-only installation and reversal of the bounded policy entrypoint.

This tool never imports CFD/model code and never writes into archived studies.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile


PACKAGE = Path(__file__).resolve().parent
REPOSITORY = PACKAGE.parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def relative_path(value):
    if not isinstance(value, str):
        raise ValueError("Manifest paths must be strings")
    path = Path(value)
    if path.is_absolute() or not path.parts or any(x in (".", "..") for x in path.parts) or str(path) != value:
        raise ValueError("Unsafe manifest path: " + value)
    return path


def path_in(root, name):
    target = root / relative_path(name)
    for item in (target,) + tuple(target.parents):
        if item.is_symlink():
            raise ValueError("Symlinks are not allowed: " + str(item))
        if item == root:
            break
    if root.resolve() not in target.resolve().parents:
        raise ValueError("Path leaves root: " + name)
    return target


def files(root):
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Regular root directory required: " + str(root))
    observed = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlinks are not allowed: " + str(path))
        if path.is_file() and "__pycache__" not in path.relative_to(root).parts:
            observed[str(path.relative_to(root))] = sha(path)
    return observed


def assert_tree(root, expected):
    actual = files(root)
    if actual != expected:
        changed = sorted(k for k in set(actual) | set(expected) if actual.get(k) != expected.get(k))
        raise ValueError("Tree differs from registered bytes: " + ", ".join(changed))


def mutable_root(value):
    raw = Path(value).absolute()
    if raw.is_symlink():
        raise ValueError("Mutable root cannot be a symlink")
    root = raw.resolve()
    archives = (REPOSITORY / "real-runs").resolve()
    if root == archives or archives in root.parents:
        raise ValueError("Archived studies are read-only; prepare a fresh isolated root")
    # Archive identities travel with their markers. A migrated --repository or
    # an unrelated frozen batch must remain protected outside this checkout.
    for ancestor in (root,) + tuple(root.parents):
        for marker in ("freeze.json", "freeze.sha256"):
            path = ancestor / marker
            if path.exists() or path.is_symlink():
                raise ValueError("Frozen archive is read-only: " + str(ancestor))
    if root == PACKAGE or PACKAGE in root.parents or root in PACKAGE.parents:
        raise ValueError("Do not mutate the delivery package or repository root")
    return root


def package_manifest():
    manifest = read(PACKAGE / "manifest.json")
    if manifest.get("schema_version") != "1.0":
        raise ValueError("Unsupported delivery manifest")
    for mapping in (manifest["base_files"], manifest["target_files"], manifest["package_files"]):
        for name, expected in mapping.items():
            relative_path(name)
            if not isinstance(expected, str) or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
                raise ValueError("Invalid sha256: " + name)
    for name, expected in manifest["package_files"].items():
        if sha(path_in(PACKAGE, name)) != expected:
            raise ValueError("Delivery payload changed: " + name)
    base, target, added = manifest["base_files"], manifest["target_files"], manifest["added_files"]
    if any(name in base for name in added) or target != dict(base, **{name: added[name]["sha256"] for name in added}):
        raise ValueError("Only the declared opt-in entrypoint may be added")
    for name, item in added.items():
        relative_path(name)
        if sha(path_in(PACKAGE, item["payload"])) != item["sha256"]:
            raise ValueError("Target payload differs: " + name)
    return manifest


def verify_sources(manifest, repository):
    """Verify archival identities without executing an archived coordinator."""
    repository = Path(repository).resolve()
    source = path_in(repository, manifest["basis"]["runtime_root"])
    assert_tree(source, manifest["base_files"])
    for name, expected in manifest["evidence_files"].items():
        if sha(path_in(repository, name)) != expected:
            raise ValueError("Evidence differs: " + name)
    freeze = read(path_in(repository, manifest["basis"]["freeze_path"]))
    if freeze["common_runtime"] != manifest["base_files"]:
        raise ValueError("Frozen runtime list differs from delivery basis")
    for name, expected in manifest["protected_repository_files"].items():
        if sha(path_in(repository, name)) != expected:
            raise ValueError("Historical protected file differs: " + name)
    return source


def prepare(root, manifest, repository):
    source = verify_sources(manifest, repository)
    root = mutable_root(root)
    if root.exists():
        raise ValueError("Preparation requires a destination that does not exist")
    if source == root or source in root.parents or root in source.parents:
        raise ValueError("Destination must be separate from the basis")
    root.mkdir(parents=True)
    try:
        for name in manifest["base_files"]:
            target = path_in(root, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path_in(source, name), target)
        assert_tree(root, manifest["base_files"])
    except Exception:
        shutil.rmtree(root)
        raise
    return root


def apply(root, manifest):
    root = mutable_root(root)
    assert_tree(root, manifest["base_files"])
    installed = []
    try:
        for name, item in manifest["added_files"].items():
            target = path_in(root, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation fails closed on an already present entrypoint.
            with target.open("xb") as handle:
                installed.append(target)
                handle.write(path_in(PACKAGE, item["payload"]).read_bytes())
        assert_tree(root, manifest["target_files"])
    except Exception:
        for target in installed:
            target.unlink()
        raise
    return root


def reverse(root, manifest):
    root = mutable_root(root)
    assert_tree(root, manifest["target_files"])
    for name in manifest["added_files"]:
        path_in(root, name).unlink()
    assert_tree(root, manifest["base_files"])
    return root


def isolated_verification(manifest, repository):
    """Verify actual application, refusal to overwrite edits, and reversal."""
    verify_sources(manifest, repository)
    with tempfile.TemporaryDirectory(prefix="simclinic-bounded-delivery-") as temporary:
        root = prepare(Path(temporary) / "runtime", manifest, repository)
        before = files(root)
        apply(root, manifest)
        after = files(root)
        reverse(root, manifest)
        restored = files(root)
        if before != restored:
            raise ValueError("Reversal did not restore exact basis")
        report = dict(schema_version="1.0", freeze_sha256=manifest["basis"]["freeze_sha256"],
                      manifest_sha256=sha(PACKAGE / "manifest.json"),
                      base_file_count=len(before), target_file_count=len(after),
                      target_bytes_match=after == manifest["target_files"],
                      reverse_restored_all_basis_bytes=restored == manifest["base_files"],
                      source_and_evidence_hashes_verified=True,
                      protected_file_count=len(manifest["protected_repository_files"]),
                      temporary_environment_removed=False, new_model_calls=0, new_simulations=0)
    report["temporary_environment_removed"] = not Path(temporary).exists()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check", "apply", "reverse", "verify"))
    parser.add_argument("--root", type=Path, help="Fresh isolated runtime root; never a frozen archive")
    parser.add_argument("--repository", type=Path, default=REPOSITORY, help="Repository containing immutable evidence")
    parser.add_argument("--state", choices=("base", "target"), default="base", help="Expected state for check")
    parser.add_argument("--output", type=Path, help="New JSON verification report; existing files are refused")
    arguments = parser.parse_args()
    if arguments.action != "verify" and arguments.root is None:
        parser.error("--root is required for this action")
    if arguments.output is not None and arguments.action != "verify":
        parser.error("--output is valid only for verify")
    try:
        manifest = package_manifest()
        if arguments.action == "verify":
            output = mutable_root(arguments.output) if arguments.output is not None else None
            report = isolated_verification(manifest, arguments.repository)
            if output is not None:
                output.parent.mkdir(parents=True, exist_ok=True)
                with output.open("x", encoding="utf-8") as handle:
                    json.dump(report, handle, indent=2)
                    handle.write("\n")
            print(json.dumps(report, indent=2))
        elif arguments.action == "check":
            assert_tree(arguments.root, manifest[arguments.state + "_files"])
            print("Verified " + arguments.state + " bytes: " + str(arguments.root))
        elif arguments.action == "prepare":
            print("Prepared verified basis: " + str(prepare(arguments.root, manifest, arguments.repository)))
        elif arguments.action == "apply":
            print("Applied opt-in entrypoint: " + str(apply(arguments.root, manifest)))
        else:
            print("Restored exact basis: " + str(reverse(arguments.root, manifest)))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("Delivery refused: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
