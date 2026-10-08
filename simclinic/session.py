"""Incremental, scoped collection of the actual native graph and its attempts."""
import hashlib
import json
from pathlib import Path
import shutil
import time
import uuid
from .core.record import validate
from .core.report import write_json


def plain(value):
    if hasattr(value,'model_dump'):return plain(value.model_dump())
    if isinstance(value,dict):return {k:plain(v) for k,v in value.items() if k not in ('config','llm_service')}
    if isinstance(value,(list,tuple)):return [plain(v) for v in value]
    return value


class SessionRecorder:
    def __init__(self,directory,task,environment,agent):
        self.directory=Path(directory).resolve()
        self.directory.mkdir(parents=True,exist_ok=True)
        self.started=time.monotonic();self.sequence=0;self.context={};self.last_tools=[]
        self.previous_failed={};self.attempts=[];self.model_calls=[];self.seen_native=set()
        self.tools_complete=True;self.decisions_complete=True;self.references_complete=True;self.acceptance_complete=False
        self.terminal_seen=False
        self.data=dict(schema_version='1.0',record_id='session-'+uuid.uuid4().hex,session_id=uuid.uuid4().hex,
            run_id=None,input_version=None,synthetic=False,task=task,environment=environment,agent=agent,
            coverage=dict(tools=False,decisions=False,references=False,acceptance=False),
            collection_status='in_progress',events=[],artifacts=[],attempts=self.attempts,
            metrics=dict(wall_seconds=None,solver_seconds=None,model_tokens=None,model_cost=None))
        self.trace=[];self.save()

    def save(self):
        validate(self.data)
        path=self.directory/'record.tmp.json';write_json(path,self.data);path.replace(self.directory/'record.json')

    def artifact(self,path,context=None):
        path=Path(path).resolve()
        if self.directory not in path.parents or not path.is_file():raise ValueError('Evidence must be inside session directory.')
        identity=context if context is not None else self.context
        entry=dict(id='a'+str(len(self.data['artifacts'])+1),path=str(path.relative_to(self.directory)),
            run_id=identity.get('run_id'),input_version=identity.get('input_version'),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        self.data['artifacts'].append(entry)
        return entry['id']

    def event(self,kind,context=None,**fields):
        identity=context if context is not None else self.context
        entry=dict(id='e'+str(len(self.data['events'])+1),kind=kind,session_id=self.data['session_id'],
            run_id=identity.get('run_id'),input_version=identity.get('input_version'))
        entry.update(fields);self.data['events'].append(entry);self.save()
        return entry

    def model(self,metadata,directory):
        self.model_calls.append(dict(metadata))
        path=Path(directory)/'metadata.json'
        request=Path(directory)/'request.json'
        self.event('tool',operation='model.generate',phase='other',critical=False,
            outcome=metadata['outcome'],exit_code=metadata['exit_code'],
            input_fingerprint=hashlib.sha256(request.read_bytes()).hexdigest(),
            error_category=metadata.get('error_category'),arguments={'request_sha256':hashlib.sha256(request.read_bytes()).hexdigest()},
            source_artifact=self.artifact(path),model_metadata=metadata)

    def tool(self,operation,phase,code,outcome,fingerprint,arguments,**extra):
        prior=self.previous_failed.get(operation)
        category=extra.pop('error_category',None if outcome=='pass' else 'process_exit' if code else 'unknown_result')
        result=self.event('tool',operation=operation,phase=phase,exit_code=code,outcome=outcome,
            input_fingerprint=fingerprint,error_category=category,
            arguments=arguments,retry_of=prior,**extra)
        if outcome=='fail' or (code is not None and code!=0):self.previous_failed[operation]=result['id']
        elif outcome=='pass':self.previous_failed.pop(operation,None)
        return result

    def capture_attempt(self,case,context):
        case=Path(case);run=context['run_id'];self.context={k:context[k] for k in ('run_id','input_version')}
        self.data.update(self.context)
        source=case/'.simclinic/native'/run
        target=self.directory/'attempts'/run
        shutil.copytree(source,target/'native',dirs_exist_ok=True)
        logs=target/'logs';logs.mkdir(exist_ok=True)
        for path in case.glob('log.*'):
            if path.is_file():shutil.copy2(path,logs/path.name)
        for name in ('Allrun.out','Allrun.err'):
            if (case/name).is_file():shutil.copy2(case/name,logs/name)
        execution=json.loads((source/'execution.json').read_text())
        self.attempts.append(dict(**self.context,execution=execution,input_snapshot=str((target/'native/input-snapshot').relative_to(self.directory))))
        command_dirs=list((target/'native/command-calls').glob('*'))
        calls=[]
        for directory in command_dirs:
            path=directory/'metadata.json'
            if not path.is_file():self.tools_complete=False;path=directory/'started.json'
            if path.is_file():calls.append((json.loads(path.read_text()),directory,path))
        calls.sort(key=lambda item:item[0]['started'])
        self.last_tools=[]
        for call,directory,path in calls:
            event=self.tool(call['operation'],call['phase'],call['exit_code'],call['outcome'],call['input_fingerprint'],
                {'argv':call['argv']},source_artifact=self.artifact(path),elapsed_seconds=call['elapsed_seconds'],
                input_version=call['input_version'])
            self.last_tools.append(event['id'])
            gate=directory/'decision.json'
            if gate.is_file():
                decision=json.loads(gate.read_text())
                self.event('decision',handles=[event['id']],decision_type='preflight_gate',
                    action=decision['decision'],source_artifact=self.artifact(gate))
            elif call['operation']=='checkMesh':
                self.decisions_complete=False
        event=self.tool('Allrun','other',execution['exit_code'],'fail' if execution['exit_code'] else 'pass',
            context['input_version'],{'script':'Allrun'},source_artifact=self.artifact(target/'native/execution.json'))
        self.last_tools.append(event['id'])
        self.tools_complete &= bool(execution.get('policy_validated')) and execution['exit_code']!=124
        operations=[item[0]['operation'] for item in calls]
        expected=['blockMesh','checkMesh','simpleFoam']
        self.tools_complete &= bool(calls) and operations==expected[:len(operations)]
        self.tools_complete &= all(item[0]['exit_code'] is not None for item in calls)
        if execution['exit_code']==0:
            self.tools_complete &= operations==expected and all(item[0]['outcome']=='pass' for item in calls)
        self.capture_native_outputs(case,context)

    def capture_native_outputs(self,case,context):
        case=Path(case);run=context['run_id']
        source=case/'.simclinic/native'/run
        native=json.loads((source/'record.json').read_text())
        target=self.directory/'attempts'/run
        mapping={}
        for item in native['artifacts']:
            path=case/item['path'];destination=target/'outputs'/item['path']
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
                self.references_complete=False;continue
            if (run,item['id']) not in self.seen_native:
                destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,destination)
                mapping[item['id']]=self.artifact(destination,context)
                self.seen_native.add((run,item['id']))
            else:
                existing=next(a for a in self.data['artifacts'] if a['path']==str(destination.relative_to(self.directory)))
                mapping[item['id']]=existing['id']
        for item in native['events']:
            key=(run,'event-'+item['id'])
            if key in self.seen_native or item.get('operation')=='Allrun':continue
            self.seen_native.add(key)
            if item['kind']=='reference':
                artifact=mapping.get(item['artifact_id'])
                if artifact:self.event('reference',context=context,artifact_id=artifact,consumer=item.get('consumer'))
                else:self.references_complete=False
            elif item['kind']=='tool':
                immutable=target/('native-event-'+item['id']+'.json')
                write_json(immutable,item)
                self.tool(item['operation'],item['phase'],item['exit_code'],item['outcome'],item['input_fingerprint'],
                    item.get('arguments',{}),source_artifact=self.artifact(immutable,context))

    def observe(self,stage,node,state):
        self.sequence+=1
        payload=plain(state)
        context=state.get('simclinic_context') or self.context
        path=self.directory/'observations'/('node-'+str(self.sequence).zfill(3)+'.json')
        write_json(path,dict(stage=stage,node=node,payload=payload))
        source=self.artifact(path,context)
        self.trace.append(dict(sequence=self.sequence,stage=stage,node=node,source_artifact=source))
        if stage=='end' and node=='local_runner':
            self.capture_attempt(state['case_dir'],state['simclinic_context'])
        elif stage=='end' and node=='visualization':
            self.capture_native_outputs(state['case_dir'],context)
            if not state.get('pyvista_visualization',{}).get('success'):
                self.tools_complete=False
        if stage=='route':
            self.event('decision',context=context,handles=self.last_tools if node in ('route_after_runner','route_after_reviewer') else [],
                decision_type='routing',action=state['route_destination'],source_artifact=source)
        elif stage=='end' and node in ('planner','input_writer','reviewer'):
            self.event('decision',context=context,handles=self.last_tools if node in ('reviewer','input_writer') else [],
                decision_type=node,source_artifact=source,
                cause_hypothesis=state.get('review_analysis') if node=='reviewer' else None,
                proposed_rewrite_plan=state.get('rewrite_plan') if node=='reviewer' else None)
        elif stage=='end' and node=='completion':
            self.terminal_seen=True
            claim=state['completion_claim']
            self.event('claim',context=context,claim_type='completion',success=claim['success'],
                source_artifact=source,emitter=claim['emitter'],status=claim['status'])
        else:
            self.event('observation',context=context,observation_type='node_boundary',stage=stage,node=node,source_artifact=source)

    def finish(self,completed=True):
        completed=completed and self.terminal_seen
        self.data['collection_status']='complete' if completed else 'aborted'
        self.data['coverage']=dict(tools=completed and self.tools_complete,decisions=completed and self.decisions_complete,
                                   references=completed and self.references_complete,acceptance=completed and self.acceptance_complete)
        self.data['collection_scope']=dict(tools='Policy-validated serial Allrun, actual model calls, native rendering and registered task assessment.',
            decisions='Registered native routing, mesh gate, reviewer/input writer outputs and explicit task-completion node; no hidden reasoning or external agents.',
            acceptance='Fixed declared channel task; actual evidence read and measured by registered task gate.')
        tokens=[c.get('total_tokens') for c in self.model_calls]
        solver=[e.get('elapsed_seconds') for e in self.data['events'] if e['kind']=='tool' and e.get('phase')=='solve']
        self.data['metrics']=dict(wall_seconds=time.monotonic()-self.started,
            solver_seconds=sum(solver) if solver and all(x is not None for x in solver) else None,
            model_tokens=sum(tokens) if all(x is not None for x in tokens) else None,model_cost=None)
        self.data['node_trace']=self.trace;self.data['model_calls']=self.model_calls
        self.save();return self.data
