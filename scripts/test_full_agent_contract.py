"""Native software contract test; backend sentinel is not simulation evidence."""
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'integration/foam_agent_v210/native/full/src'))
from services import configure_llm
from services.input_writer import initial_write
from nodes.visualization_node import _guess_primary_field
from scripts.run_full_agent import REQUIREMENT


class CallSentinel:
    def invoke(self,*args,**kwargs):
        raise AssertionError('Malformed plan reached model generation before whole-plan validation.')


def main():
    assert _guess_primary_field(REQUIREMENT)=='U','Native renderer confused acceptance pressure with requested velocity.'
    configure_llm(CallSentinel())
    with tempfile.TemporaryDirectory(prefix='simclinic-native-contract-') as directory:
        case=Path(directory)/'case'
        # Put a valid item first: per-file-only validation would spend a call.
        plan=[dict(folder_name='system',file_name='controlDict'),dict(folder_name='.',file_name='Allrun')]
        try:
            initial_write(str(case),plan,'Test-only requirement','Test-only reference','simpleFoam')
        except ValueError as exc:
            assert 'Only initial fields' in str(exc),exc
        else:
            raise AssertionError('Malformed plan was accepted.')
        assert not case.exists(),'Rejected plan created a partial case.'
    print('Native malformed-plan contract passed: no model calls, no case creation (software test only).')


if __name__=='__main__':main()
