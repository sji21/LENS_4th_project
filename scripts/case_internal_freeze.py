"""Freeze existing-checkout code and verified case inputs before new HO authoring."""
import argparse,json,subprocess,shutil,sys,platform,importlib.metadata
from datetime import datetime,timezone
from pathlib import Path
from case_internal_run import sha,write


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True)
    ap.add_argument('--profile',type=Path,required=True);ap.add_argument('--audit',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    report=json.loads(args.audit.read_text('utf-8'))
    assert report['actual_connection_pass'] and report['cld_hit20_pass']
    assert report['profile_sha256']==sha(args.profile)
    prereg=args.root/'05_new_ho/HO100_sampling_preregistration.json'
    scoring=args.root/'05_new_ho/RETURNED_BODY_SCORING_PREREG.json'
    assert prereg.is_file() and scoring.is_file()
    repo=Path(__file__).resolve().parents[1]
    tests_path=args.root/'04_application/unit_tests_final.json';tests=json.loads(tests_path.read_text('utf-8'))
    assert tests['failed']==0 and tests['passed']>0
    assert all(sha(repo/n)==v for n,v in tests['code_hashes'].items()),'code changed after tests'
    binding_path=args.root/'04_application/FINAL_DEV_REVIEW_BINDING.json'
    binding=json.loads(binding_path.read_text('utf-8'))
    assert binding['all_requested_fields_equal'] and binding['all_body_source_traces_equal']
    assert binding['retrieval_profile']['sha256']==sha(args.profile)
    args.output.mkdir(parents=True,exist_ok=False)
    tracked=subprocess.check_output(['git','ls-files','-z'],cwd=repo).decode('utf-8').split('\0')
    added=[str(p.relative_to(repo)) for folder in ('src/retrieval','src/evaluation','scripts','tests')
        for p in (repo/folder).glob('*case_internal*.py')]
    paths=sorted(set(n for n in tracked+added if n))
    files={};code=args.output/'code'
    for name in paths:
        source=repo/name
        if not source.is_file():raise FileNotFoundError(source)
        target=code/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        files[name]=sha(source);assert sha(target)==files[name]
    profile=json.loads(args.profile.read_text('utf-8'));data=Path(profile['data_root'])
    distributions=sorted({(d.metadata['Name'],d.version) for d in importlib.metadata.distributions() if d.metadata['Name']})
    write(args.output/'python_environment.json',{'executable':sys.executable,'version':sys.version,
        'platform':platform.platform(),'installed_distributions':[{'name':n,'version':v} for n,v in distributions],
        'scope':'Observed environment; a clean installation on another computer has not been claimed.'})
    write(args.output/'CODE_MANIFEST.json',files)
    manifest={'frozen_at':datetime.now(timezone.utc).isoformat(),'development_checkout':str(repo),
        'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo).decode().strip(),
        'new_worktree_created':False,'code_manifest_sha256':sha(args.output/'CODE_MANIFEST.json'),
        'profile_path':str(args.profile),'profile_sha256':sha(args.profile),'profile':profile,
        'application_audit_path':str(args.audit),'application_audit_sha256':sha(args.audit),
        'unit_tests_path':str(tests_path),'unit_tests_sha256':sha(tests_path),
        'dev_body_review_binding_path':str(binding_path),'dev_body_review_binding_sha256':sha(binding_path),
        'duplicate_audit_tool_sha256':sha(args.root/'05_new_ho/duplicate_question_audit.py'),
        'duplicate_reference_manifest_sha256':sha(args.root/'05_new_ho/duplicate_reference_v1/duplicate_reference_manifest.json'),
        'sampling_preregistration_sha256':sha(prereg),'body_scoring_preregistration_sha256':sha(scoring),
        'index_connection_audit_sha256':sha(data/'INDEX_CONNECTION_AUDIT.json'),
        'index_hash_kind':'Pinned logical documents/metadata/vectors; Chroma runtime files can change during read access.',
        'new_ho_authoring_may_start':True,'new_ho_execution_has_started':False,
        'evaluation_rule':'Question-only actual k=2 once; seal raw output before joining Gold; no post-HO policy/data edits.',
        'python_environment_sha256':sha(args.output/'python_environment.json')}
    write(args.output/'DEVELOPMENT_FREEZE.json',manifest)
    print(json.dumps({'freeze':str(args.output/'DEVELOPMENT_FREEZE.json'),'files':len(files),'sha256':sha(args.output/'DEVELOPMENT_FREEZE.json')}))


if __name__=='__main__':main()
