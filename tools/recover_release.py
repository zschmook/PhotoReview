"""Validate a previously tested GitHub artifact and stage its original source tag."""
import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
REPOSITORY='zschmook/PhotoReview'
REQUIRED_STEPS={'Build single Windows EXE','Test packaged app with generated photos','Save tested EXE for release recovery'}


def validate_saved_build(tag,run_id,api,expected_commit):
    if not re.fullmatch(r'v\d+\.\d+(?:\.\d+)?',tag):raise ValueError('Invalid version tag')
    if not str(run_id).isdigit():raise ValueError('Build run ID must be numeric')
    run=api('/actions/runs/'+str(run_id))
    if run.get('repository',{}).get('full_name')!=REPOSITORY or run.get('path')!='.github/workflows/release-windows.yml':raise ValueError('Run is not this repository\'s Windows release workflow')
    if run.get('status')!='completed':raise ValueError('Wait for the original build to finish')
    if run.get('event')=='push':
        if run.get('head_sha')!=expected_commit:raise ValueError('Build source does not match the requested tag')
    elif run.get('event')=='workflow_dispatch':
        if run.get('display_title')!='Photo Review '+tag:raise ValueError('Manual build does not identify the requested release')
    else:raise ValueError('Unsupported release build trigger')
    jobs=api('/actions/runs/'+str(run_id)+'/jobs?per_page=100').get('jobs',[])
    if not any(REQUIRED_STEPS<={step.get('name') for step in job.get('steps',[]) if step.get('conclusion')=='success'} for job in jobs):raise ValueError('The EXE did not pass all required build/test/save steps')
    artifacts=api('/actions/runs/'+str(run_id)+'/artifacts?per_page=100').get('artifacts',[])
    matches=[a for a in artifacts if a.get('name')=='photoreview-tested-'+tag and not a.get('expired') and a.get('size_in_bytes',0)>0]
    if len(matches)!=1:raise ValueError('Expected one unexpired tested EXE artifact')
    return int(matches[0]['id'])


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--tag',required=True);parser.add_argument('--run-id',required=True);args=parser.parse_args()
    if not re.fullmatch(r'v\d+\.\d+(?:\.\d+)?',args.tag):raise ValueError('Invalid version tag')
    token=os.environ.get('GITHUB_TOKEN');assert token,'GITHUB_TOKEN is required'
    commit=subprocess.check_output(['git','rev-list','-n','1',args.tag],cwd=ROOT,text=True).strip()
    def api(path):
        request=Request('https://api.github.com/repos/'+REPOSITORY+path,headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2026-03-10','User-Agent':'PhotoReview-release-recovery'})
        with urlopen(request,timeout=30) as response:return json.load(response)
    artifact_id=validate_saved_build(args.tag,args.run_id,api,commit)
    source=ROOT/'.build'/'release-recovery-source'
    source.parent.mkdir(exist_ok=True)
    subprocess.run(['git','worktree','add','--detach',str(source),args.tag],cwd=ROOT,check=True)
    values={node.targets[0].id:ast.literal_eval(node.value) for node in ast.parse((source/'photo_review'/'version.py').read_text()).body if isinstance(node,ast.Assign)}
    version=values['VERSION']
    if args.tag!='v'+version:raise ValueError('Source version and requested tag differ')
    (source/'tools'/'release_windows.py').write_bytes((ROOT/'tools'/'release_windows.py').read_bytes())
    windows_version='.'.join((version.split('.')+['0']*4)[:4])
    with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as output:output.write(f'artifact_id={artifact_id}\nwindows_version={windows_version}\n')
    print(f'Validated tested artifact {artifact_id} for {args.tag}, source commit {commit}. No recompilation.')


if __name__=='__main__':main()
