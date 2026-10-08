"""Prefer explicit rendering instructions over quantities in acceptance prose."""
import re


def choose_field(requirement):
    text = requirement or ''
    pattern = r'\b(?:render|plot|visuali[sz]e)\s+(?:the\s+)?(velocity|pressure|temperature|U|p|T)\b'
    mapping = {'velocity':'U','u':'U','pressure':'p','p':'p','temperature':'T','t':'T'}
    requested = {mapping[m.lower()] for m in re.findall(pattern,text,flags=re.IGNORECASE)}
    if len(requested)>1:
        raise ValueError('Multiple explicitly requested fields require separate render requests.')
    if requested:
        return requested.pop()
    if re.search(r'\b(?:pressure|p)\b',text,flags=re.IGNORECASE):return 'p'
    if re.search(r'\btemperature\b',text,flags=re.IGNORECASE):return 'T'
    return 'U'
