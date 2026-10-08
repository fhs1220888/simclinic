"""Registered task gate: measured acceptance precedes explicit Agent completion."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import uuid
from .acceptance import channel_criteria, solver_measurements, profile_error, pressure_gradient_error
from ...checks.behavior import matches
from ...core.report import write_json
from ...foam_runtime import current_record, verified_workspace
from ...result_guard import ResultRejected


def task_definition(objective):
    criteria=channel_criteria()+[
        dict(id='task_inputs_verified',stage='execution',operator='eq',target=True,description='生成输入符合指定通道任务'),
        dict(id='rendered_velocity',stage='execution',operator='eq',target=True,description='当前运行已生成所要求的 U 图片')]
    return dict(id='poiseuille-channel',objective=objective,required_preflight=['checkMesh'],acceptance=criteria)


def canonical_dictionary(path):
    text=Path(path).read_text()
    text=re.sub(r'/\*.*?\*/|//[^\n]*','',text,flags=re.DOTALL)
    text=re.sub(r'FoamFile\s*\{[^{}]*\}','',text)
    number=r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'
    tokens=re.findall(number+r'|[^\s;(){}\[\]]+|[;(){}\[\]]',text)
    return [round(float(token),12) if re.fullmatch(number,token) else token for token in tokens]


class ChannelCompletion:
    def __init__(self,recorder,reference,image):
        self.recorder=recorder;self.reference=Path(reference);self.image=image

    def __call__(self,state):
        recorder=self.recorder;context=state.get('simclinic_context') or {}
        case=Path(state.get('case_dir') or recorder.directory/'missing-case')
        native=case/'.simclinic/native'/context.get('run_id','missing')
        logs=recorder.directory/'attempts'/context.get('run_id','missing')/'logs'
        execution=json.loads((native/'execution.json').read_text()) if (native/'execution.json').is_file() else {}
        log=(logs/'log.simpleFoam').read_text() if (logs/'log.simpleFoam').is_file() else ''
        values=solver_measurements(log,execution.get('exit_code'))
        values.update(profile_error=None,pressure_gradient_error=None,task_inputs_verified=False,rendered_velocity=False)
        snapshot=native/'input-snapshot';checks={};error=None
        for folder in ('0','constant','system'):
            for path in (self.reference/folder).rglob('*'):
                if path.is_file():
                    relative=path.relative_to(self.reference);candidate=snapshot/relative
                    checks[str(relative)]=candidate.is_file() and canonical_dictionary(candidate)==canonical_dictionary(path)
        values['task_inputs_verified']=bool(checks) and all(checks.values())
        if not state.get('error_logs'):
            try:
                record=current_record(case,context)
                images=state.get('plot_outputs',[])
                image_artifacts=[a for a in record.data['artifacts'] if a.get('role')=='visualization']
                values['rendered_velocity']=bool(state.get('pyvista_visualization',{}).get('success')) and \
                    state.get('visualization_summary',{}).get('fields_visualized')==['U'] and len(images)==1 and any(
                        (case/a['path']).resolve()==Path(images[0]).resolve() and
                        hashlib.sha256(Path(images[0]).read_bytes()).hexdigest()==a['sha256'] for a in image_artifacts)
                with verified_workspace(case,context) as work:
                    values.update(self.sample(work,context))
                current_record(case,context)
            except (ResultRejected,OSError,ValueError,StopIteration) as exc:
                error=dict(type=type(exc).__name__,message=str(exc));recorder.references_complete=False
        accepted=all(matches(values[c['id']],c) is True for c in recorder.data['task']['acceptance'])
        assessment=dict(**{k:context.get(k) for k in ('run_id','input_version')},values=values,input_checks=checks,
            criteria=recorder.data['task']['acceptance'],accepted=accepted,error=error,
            method='Fixed channel measurements from current verified snapshot; no model reason used as fact.')
        assessment_path=recorder.directory/'observations/task-assessment.json'
        write_json(assessment_path,assessment)
        source=recorder.artifact(assessment_path,context)
        for criterion in recorder.data['task']['acceptance']:
            recorder.event('measurement',context=context,criterion_id=criterion['id'],value=values[criterion['id']],artifact_id=source)
        recorder.acceptance_complete=True
        claim=dict(**{k:context.get(k) for k in ('run_id','input_version')},success=accepted,
            status='completed' if accepted else 'failed' if state.get('error_logs') else 'acceptance_not_met',
            emitter='Foam-Agent registered SimClinic channel completion node',
            assessment_artifact=source,criteria_preserved=True)
        write_json(recorder.directory/'observations/completion-claim.json',claim)
        return {'acceptance_report':assessment,'completion_claim':claim}

    def sample(self,work,context):
        directory=self.recorder.directory/'observations/sampling'
        directory.mkdir()
        name='simclinic-assessment-'+uuid.uuid4().hex
        argv=['docker','run','--rm','--pull','never','--name',name,'--init','--platform','linux/amd64','--network','none',
            '--cpus','2','--memory','2g','--read-only','--tmpfs','/tmp','--user','root',
            '--mount',f'type=bind,src={work},dst=/case','--workdir','/case','--entrypoint','/bin/bash',self.image,
            '-c','source /opt/openfoam10/etc/bashrc && simpleFoam -postProcess -func sample -latestTime']
        started=time.monotonic();code=None
        try:
            result=subprocess.run(argv,capture_output=True,timeout=120)
            stdout=result.stdout;stderr=result.stderr;code=result.returncode
        except subprocess.TimeoutExpired as exc:
            stdout=exc.stdout or b'';stderr=exc.stderr or b'';self.recorder.tools_complete=False
        finally:
            subprocess.run(['docker','rm','--force',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        (directory/'stdout.log').write_bytes(stdout);(directory/'stderr.log').write_bytes(stderr)
        event=self.recorder.tool('sample','postprocess',code,'unknown' if code is None else 'pass' if code==0 else 'fail',
            context['input_version'],{'function':'sample','time':'current verified latest'},elapsed_seconds=time.monotonic()-started,
            stdout_artifact=self.recorder.artifact(directory/'stdout.log',context),stderr_artifact=self.recorder.artifact(directory/'stderr.log',context))
        if code!=0:raise ValueError('Sampling did not exit successfully: '+str(code))
        profile=next((work/'postProcessing').rglob('profile.xy'));pressure=next((work/'postProcessing').rglob('pressure.xy'))
        # Archive exact source sample bytes before the completion statement.
        for path in (profile,pressure):
            target=directory/path.name;target.write_bytes(path.read_bytes())
            self.recorder.artifact(target,context)
        return dict(profile_error=profile_error(profile),pressure_gradient_error=pressure_gradient_error(pressure))
