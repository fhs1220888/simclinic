"""Curate our validated channel inputs and actual fixed-version command help."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from simclinic.core.report import write_json
from simclinic.domains.openfoam.channel import create_channel
IMAGE='openfoam/openfoam10-paraview510@sha256:d6ff1f9a2e7bc3c9177f373bebbdeb542fd8b49144afc24d5e3a3cd9bfae253d'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    out=args.out.resolve()
    if out.exists():raise ValueError('Use a new corpus directory.')
    case=create_channel(out/'reference-case')
    (case/'Allrun').write_text('#!/bin/bash\nset -eu\nblockMesh > log.blockMesh 2>&1\ncheckMesh > log.checkMesh 2>&1\nsimpleFoam > log.simpleFoam 2>&1\n')
    groups={name:sorted(path.name for path in (case/name).iterdir()) for name in ('0','constant','system')}
    structure='\n'.join('<dir>directory name: '+name+'. File names in this directory: ['+', '.join(files)+']</dir>' for name,files in groups.items())
    content='<directory_structure>\n'+structure+'\n</directory_structure>\n'
    for name,files in groups.items():
        for filename in files:
            content+='<file path="'+name+'/'+filename+'">\n'+(case/name/filename).read_text()+'\n</file>\n'
    metadata=dict(case_name='poiseuille_channel',case_solver='simpleFoam',case_domain='incompressible',case_category='laminar',dir_structure=structure)
    case_doc=dict(id='validated-channel-v1',index='poiseuille channel simpleFoam incompressible laminar steady velocity pressure',full_content=content,**metadata)
    allrun_doc=dict(id='channel-allrun-v1',index='poiseuille channel simpleFoam blockMesh checkMesh incompressible',full_content=(case/'Allrun').read_text(),**metadata)
    command_docs=[]
    for command in ('blockMesh','checkMesh','simpleFoam','foamDictionary'):
        result=subprocess.run(['docker','run','--rm','--pull','never','--platform','linux/amd64','--network','none',
             '--entrypoint','/bin/bash',IMAGE,'-c','source /opt/openfoam10/etc/bashrc && '+command+' -help'],capture_output=True,text=True)
        if result.returncode:raise RuntimeError('Actual command help failed: '+command)
        text=result.stdout+result.stderr
        (out/(command+'-help.txt')).write_text(text)
        command_docs.append(dict(id=command+'-foundation10',index=command,full_content=text,command=command,help_text=text))
    write_json(out/'corpus.json',dict(schema_version='1.0',synthetic=False,retrieval_method='lexical-overlap-v1',
        source='SimClinic authored channel inputs with archived real CFD validation; actual Foundation10 command help',
        validated_reference_archive=str(ROOT/'real-runs/native-postprocessing-final-20261004'),image=IMAGE,
        collections=dict(openfoam_tutorials_structure=[case_doc],openfoam_tutorials_details=[case_doc],
                         openfoam_allrun_scripts=[allrun_doc],openfoam_command_help=command_docs)))
    (out/'raw').mkdir()
    write_json(out/'raw/openfoam_case_stats.json',dict(case_domain=['incompressible'],case_category=['laminar'],case_solver=['simpleFoam']))
    (out/'raw/openfoam_commands.txt').write_text('blockMesh\ncheckMesh\nsimpleFoam\n')
    print('Reference corpus created:',out)


if __name__=='__main__':main()
