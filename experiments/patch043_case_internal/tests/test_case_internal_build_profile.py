"""Profiles built from relative inputs remain valid for the strict loader."""
from dataclasses import asdict
import json
import sys
from pathlib import Path

import pytest

from experiments.patch043_case_internal.scripts.case_internal_build_profile import main
from experiments.patch043_case_internal.retrieval import CaseInternalPolicy
from experiments.patch043_case_internal.profile import read_internal_case_profile


@pytest.mark.parametrize('rerank', ['band_3pct', 'cross_encoder'])
def test_relative_data_and_model_roots_load_as_absolute(tmp_path, monkeypatch, rerank):
    data=tmp_path/'bundle'
    root=tmp_path/'project'
    revision='a'*40
    required=('database/cases.sqlite3','chunks/cases.jsonl',
              'CORPUS_MANIFEST.json','DB_CONNECTION_AUDIT.json','INDEX_MANIFEST.json')
    for name in required:
        target=data/name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(name,encoding='utf-8')
    (data/'INDEX_MANIFEST.json').write_text(json.dumps({
        'model_id':'nlpai-lab/KURE-v1','model_revision':revision,
        'logical_sha256':'b'*64}),encoding='utf-8')

    models=root/'02_models'
    embedding=models/'kure-v1'/'config.json'
    embedding.parent.mkdir(parents=True)
    embedding.write_text('embedding',encoding='utf-8')
    from src.retrieval.case_profile import file_hash
    (models/'KURE_LOCAL_COPY.json').write_text(json.dumps({
        'revision':revision,'files':{'config.json':file_hash(embedding)}}),encoding='utf-8')

    selection={'policy':asdict(CaseInternalPolicy(rerank=rerank))}
    if rerank=='cross_encoder':
        model=models/'mmarco-minilm'/'config.json'
        model.parent.mkdir()
        model.write_text('reranker',encoding='utf-8')
        (models/'minilm_model_metadata.json').write_text(json.dumps({
            'id':'test-minilm','sha':'c'*40,'cardData':{'license':'test'}}),encoding='utf-8')
        run=tmp_path/'minilm-run'
        run.mkdir()
        (run/'INPUT_SEAL.json').write_text(json.dumps({
            'max_length':512,'batch_size':4,
            'model_files':{'config.json':file_hash(model)}}),encoding='utf-8')
        selection['model_run']=str(run)
    selection_path=tmp_path/'selection.json'
    selection_path.write_text(json.dumps(selection),encoding='utf-8')

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys,'argv',['case_internal_build_profile.py',
        '--data','bundle','--root','project','--selection','selection.json',
        '--output','profile.json'])
    main()
    profile=read_internal_case_profile(tmp_path/'profile.json')
    assert profile['data_root']==str(data.resolve())
    assert profile['embedding_local']['path']==str((models/'kure-v1').resolve())
    if rerank=='cross_encoder':
        assert profile['cross_encoder']['path']==str((models/'mmarco-minilm').resolve())
