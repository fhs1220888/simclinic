"""Exercise the actual native writer; synthetic replies are software tests only."""
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'integration/foam_agent_v210/native/content/src'))
from services import configure_llm
from services.input_writer import initial_write
from simclinic.input_content import InputContentError


class Reply:
    def __init__(self, text):self.text = text
    def invoke(self,*args,**kwargs):return self.text


def main():
    body = 'FoamFile\n{version 2.0; format ascii; class dictionary; object controlDict;}\napplication simpleFoam;\n'
    checks = []
    for name, text, accepted in [
        ('raw',body,True),
        ('matching-envelope',json.dumps(dict(file_name='controlDict',folder_name='system',content=body)),True),
        ('wrong-path',json.dumps(dict(file_name='controlDict',folder_name='constant',content=body)),False),
        ('literal-newlines',body.replace('\n','\\n'),False),
        ('ambiguous-json','{"content":"first","content":"second"}',False),
    ]:
        with tempfile.TemporaryDirectory(prefix='simclinic-content-contract-') as temporary:
            configure_llm(Reply(text))
            case = Path(temporary)/'case'
            target = case/'system/controlDict'
            target.parent.mkdir(parents=True)
            target.write_text('Existing test-only file must not be overwritten on rejection.')
            before = target.read_bytes()
            try:
                result = initial_write(str(case),[dict(file_name='controlDict',folder_name='system')],
                    'Synthetic contract-only task','Synthetic reference','simpleFoam')
            except InputContentError:
                assert not accepted,name
                assert target.read_bytes() == before,name
                checks.append(dict(name=name,rejected=True,existing_file_unchanged=True))
            else:
                assert accepted,name
                assert target.read_text() == body,name
                assert result['foamfiles'].list_foamfile[0].content == body,name
                checks.append(dict(name=name,accepted=True,file_and_downstream_context_match=True))
    print(json.dumps(dict(synthetic=True,new_model_calls=0,new_simulations=0,checks=checks),indent=2))


if __name__=='__main__':main()
