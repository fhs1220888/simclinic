"""A small, explicit lexical reference adapter; never masquerades as FAISS."""
import hashlib
import json
from pathlib import Path
import re


class LexicalReferences:
    def __init__(self,path,observer=None):
        self.path=Path(path)
        self.data=json.loads(self.path.read_text())
        self.sha256=hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.observer=observer

    def search(self,database_name,query,topk=1):
        if type(topk) is not int or topk<1:raise ValueError('topk must be positive')
        terms=set(re.findall(r'[a-z0-9]+',query.lower()))
        documents=self.data['collections'].get(database_name)
        if documents is None:raise ValueError('Unknown reference collection: '+database_name)
        ranked=[]
        for document in documents:
            index=set(re.findall(r'[a-z0-9]+',document['index'].lower()))
            overlap=len(terms & index)
            if overlap:
                ranked.append((1-overlap/max(1,len(terms)),document))
        ranked.sort(key=lambda entry:(entry[0],entry[1]['id']))
        if not ranked:raise ValueError('No matching curated reference; do not invent a retrieval hit.')
        result=[dict(document,score=score,retrieval_method='lexical-overlap-v1',corpus_sha256=self.sha256) for score,document in ranked[:topk]]
        if self.observer:self.observer(database_name,query,result)
        return result
