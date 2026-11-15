#!/usr/bin/env python3
"""Strict, gold-free evidence contracts. stdlib, no network or inference.

Level Q proves that an unaltered quote is locatable within a frozen text unit
and an allowlisted raw-body version. It does NOT prove proposition truth.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import hashlib,re,json

HEX64=re.compile(r'^[a-f0-9]{64}$')
class ProvenanceError(ValueError):pass

def hash_bytes(value:bytes)->str:return hashlib.sha256(value).hexdigest()

def require_digest(value:str)->None:
    if not isinstance(value,str) or not HEX64.fullmatch(value):raise ProvenanceError('Invalid SHA-256 digest')

@dataclass(frozen=True)
class SourceVersion:
    ags:str
    url:str
    body_sha256:str
    acquisition_time:str
    document_date:str|None = None
    source_type:str='unknown'
    def __post_init__(self):
        if not re.fullmatch(r'\d{8}',self.ags):raise ProvenanceError('Invalid AGS')
        if not self.url.startswith(('http://','https://')):raise ProvenanceError('Invalid source URL')
        require_digest(self.body_sha256)
        if not self.acquisition_time:raise ProvenanceError('Missing acquisition time')

@dataclass(frozen=True)
class QuoteAnchor:
    task_id:str
    ags:str
    url:str
    body_sha256:str
    unit_id:str
    quote:str
    start_char:int
    end_char:int
    evidence_role:str
    def __post_init__(self):
        if not self.task_id or not self.unit_id:raise ProvenanceError('Missing evidence ID')
        require_digest(self.body_sha256)
        if len(self.quote)<12:raise ProvenanceError('Quote too short for robust locator')
        if self.start_char<0 or self.end_char<=self.start_char:raise ProvenanceError('Invalid offsets')
        if self.end_char-self.start_char != len(self.quote):raise ProvenanceError('Offsets do not match quote length')

def verify_anchor(anchor:QuoteAnchor,units:dict[str,dict],catalog:set[tuple[str,str,str]])->dict:
    """No fallback to document name, host, city mention, or crawl AGS alone."""
    source_key=(anchor.ags,anchor.url,anchor.body_sha256)
    if source_key not in catalog:raise ProvenanceError('Source body not in admitted source catalog')
    u=units.get(anchor.unit_id)
    if u is None:raise ProvenanceError('Unit absent')
    if u.get('acquisition_ags')!=anchor.ags or u.get('source_url')!=anchor.url:
        raise ProvenanceError('Unit-source/AGS mismatch')
    if anchor.body_sha256 not in u.get('raw_hashes',[]):raise ProvenanceError('Unit body hash mismatch')
    if u['text'][anchor.start_char:anchor.end_char]!=anchor.quote:
        raise ProvenanceError('Literal quote and/or offsets fail')
    return {'status':'Q_LOCATOR_VERIFIED','claim_supported':'NOT_ASSESSED','territorial_scope':'UNKNOWN',
            'task_id':anchor.task_id,'unit_id':anchor.unit_id,'url':anchor.url,
            'body_sha256':anchor.body_sha256,'start_char':anchor.start_char,'end_char':anchor.end_char}

def load_catalog(records:list[dict],raw_dir:Path|None=None)->set[tuple[str,str,str]]:
    catalog=set()
    for v in records:
        src=SourceVersion(**{k:v[k] for k in ('ags','url','body_sha256','acquisition_time')},
                          document_date=v.get('document_date'),source_type=v.get('source_type','unknown'))
        if raw_dir is not None:
            path=raw_dir/src.body_sha256
            if not path.exists() or hash_bytes(path.read_bytes())!=src.body_sha256:
                raise ProvenanceError('Missing or altered raw source body '+src.body_sha256)
        key=(src.ags,src.url,src.body_sha256)
        if key in catalog:raise ProvenanceError('Duplicate source version record')
        catalog.add(key)
    return catalog

def require_safe_claim(status:str,document_date:str|None,cutoff:str,*,territory_validated:bool=False)->bool:
    """Structural gate only; TRUE does not certify veracity of a proposition."""
    if status!='verified' or not territory_validated:return False
    if not document_date or not cutoff or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',document_date):return False
    return document_date<=cutoff

def same_measure_two_sources(s1:SourceVersion,s2:SourceVersion,measure_key1:str,measure_key2:str,cutoff:str)->bool:
    if (not measure_key1 or measure_key1!=measure_key2 or not s1.document_date or not s2.document_date):return False
    if (s1.ags!=s2.ags or s1.body_sha256==s2.body_sha256 or not s1.document_date<s2.document_date):return False
    return s2.document_date<=cutoff
