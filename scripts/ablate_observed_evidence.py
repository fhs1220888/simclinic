"""Deliberately incomplete export of real evidence; never a new simulation run."""
import argparse
import copy
import hashlib
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from simclinic.core.record import load_record,validate
from simclinic.core.report import write_json,write_report
from simclinic.cli import get_report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();source=args.source.resolve();out=args.out.resolve()
    record=load_record(source/'record.json',source)
    out.mkdir(parents=True,exist_ok=False)
    data=copy.deepcopy(record.data)
    for artifact in data['artifacts']:
        path=source/artifact['path']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=artifact['sha256']:
            raise ValueError('Source evidence changed; do not export unverifiable evidence.')
        target=out/artifact['path'];target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    data['record_id']+='-incomplete-export'
    data['evidence_ablation']=dict(source_record=str(source/'record.json'),new_simulation_run=False,
        method='Remove normalized tools, control decisions, references and completion claims; retain actual measurements and artifact bytes.')
    data['events']=[event for event in data['events'] if event['kind'] in ('measurement','observation')]
    data['coverage']=dict(tools=False,decisions=False,references=False,acceptance=True)
    data['collection_status']='incomplete_export'
    write_json(out/'record.json',validate(data))
    report=get_report(load_record(out/'record.json',out));write_report(report,out/'audit')
    assert report['summary']['finding']==0 and report['summary']['insufficient_evidence']==5,report['summary']
    write_json(out/'summary.json',dict(synthetic=False,evidence_ablation=True,new_simulation_run=False,
        source_run_id=data['run_id'],source_record_id=record.data['record_id'],audit_summary=report['summary']))
    print('Incomplete actual-evidence export correctly reports five insufficient-evidence checks:',out)


if __name__=='__main__':main()
