import copy
import hashlib
import re
from pathlib import Path
from simclinic.core.record import Record, load_record, require, validate

TAG = re.compile(r"<(error_logs|review_analysis|rewrite_plan)>\s*\n?(.*?)</\1>", re.DOTALL)


class FoamAgentTextAdapter:
    """Pinned v2.1.0 logger reader; not validated on real Agent runs."""

    def import_record(self, source: Path, metadata: Path) -> Record:
        source = source.resolve()
        require(source.is_dir(), "Foam-Agent source must be a case directory")
        data = copy.deepcopy(load_record(metadata).data)
        # A text stream does not prove completeness of any structured event category.
        data["coverage"] = {k: False for k in ("tools", "decisions", "references", "acceptance")}
        data["events"], data["artifacts"] = [], []
        data["adapter"] = {"name": "foam-agent-text", "format_status": "source-reviewed-v2.1.0",
                           "source_dir": str(source),
                           "reviewed_source_commit": "bfb7f791f77438ff90c1a39ff398739ee97bcc67",
                           "verified_run_compatibility": False}
        # review.log duplicates tagged messages in workflow.log; choose one, never double count.
        paths = [source / "workflow.log"] if (source / "workflow.log").is_file() else [source / "review.log"]
        require(paths[0].is_file(), "workflow.log or review.log required")
        for path in paths:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
            data["artifacts"].append({"id": path.name, "path": path.name, "run_id": None,
                                      "input_version": None, "sha256": hashlib.sha256(raw).hexdigest()})
            data["events"].append({"id": "raw-log", "kind": "observation", "run_id": None,
                                   "input_version": None, "text": "Imported raw text; no tool state inferred.",
                                   "source": {"file": str(path), "line": 1}})
            for index, match in enumerate(TAG.finditer(text)):
                data["events"].append({"id": f"tag-{index}", "kind": "observation",
                    "run_id": None, "input_version": None, "tag": match[1], "text": match[2].strip(),
                    "source": {"file": str(path), "line": text.count("\n", 0, match.start()) + 1}})
        return Record(validate(data), str(metadata.resolve()), source)
