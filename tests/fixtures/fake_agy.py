#!/usr/bin/env python3
"""TEST DOUBLE ONLY. Does not contact Google or implement model reasoning."""
import json, os, pathlib, signal, sys, time
args=sys.argv[1:]
if '--help' in args:
    print('--input-format --output-format --json-schema --model --agent --add-dir --print-timeout');sys.exit(0)
if '--version' in args:
    print('AGY TEST DOUBLE, NOT REAL ANTIGRAVITY');sys.exit(0)
if args == ['models']:
    print('gemini-3.8-flash-medium  Gemini 3.8 Flash (Medium)')
    if os.getenv('FAKE_NO_HIGH') != '1': print('gemini-3.8-flash-high Gemini 3.8 Flash (High)')
    sys.exit(0)
model=args[args.index('--model')+1];agent=args[args.index('--agent')+1]
assert '--dangerously-skip-permissions' in args
assert args[args.index('--mode')+1]==('accept-edits' if agent=='es-editor' else 'plan')
assert '--continue' not in args and '-p' not in args
if '--conversation' in args:
    assert args[args.index('--conversation')+1] == 'fixture-1'
assert args[args.index('--input-format')+1]=='stream-json'
lines=sys.stdin.buffer.readlines();assert len(lines)==1
message=json.loads(lines[0]);assert message['event']=='user'
text=message['message']['content'];case=os.getenv('FAKE_CASE','ok')
case=json.loads(os.getenv('FAKE_MODEL_CASES','{}')).get(model,case)
def emit(value):
    if case in ('inherited_quota','inherited_interruption') and value.get('event')=='result':
        print(json.dumps({'event':'step_update','step_update':{'step_type':'finish','state':'DONE','step_index':100}}),flush=True)
        value['result'].update(status='ERROR',error='Individual quota reached. Resets later.' if case=='inherited_quota'
                               else 'The stream was interrupted. Please continue the task you were working on.')
    print(json.dumps(value),flush=True)
if case=='model_unavailable':
    emit({'event':'result','result':{'status':'ERROR','error':'Unknown model','num_turns':0}});sys.exit(1)
tools=['finish'] if agent=='es-reader' else ['view_file','grep_search','finish']
if case=='write_tool':tools+=['write_to_file']
if case=='mcp_tool':tools+=['mcp_send_message']
if case=='bad_model':model='gemini-3.7-flash-medium'
if case=='bad_agent':agent='self'
init={'event':'init','conversation_id':'fixture-1','init':{'cwd':os.getcwd(),'model':model,'agent':agent,'tools':tools,'permission_mode':'request-review'}}
if case=='bypass':init['init']['permission_mode']='always-proceed'
if case!='no_init':emit(init)
if case=='native_quota':
    def interrupted(*_):
        emit({'event':'result','result':{'conversation_id':'fixture-1','status':'ERROR','num_turns':1,
              'error':'The stream was interrupted. Please continue the task you were working on.'}})
        sys.exit(0)
    signal.signal(signal.SIGTERM,interrupted)
    pathlib.Path(args[args.index('--log-file')+1]).write_text(
        'Run: attempt 1 failed (RESOURCE_EXHAUSTED (code 429): Individual quota reached. Resets in 2h3m4s.), retrying in 4s\n')
    time.sleep(10)
if case=='timeout':
    if os.getenv('FAKE_PID_FILE'):pathlib.Path(os.environ['FAKE_PID_FILE']).write_text(str(os.getpid()))
    time.sleep(10)
if case=='invalid_json':print('NOT JSON',flush=True);sys.exit(0)
if case=='nested':emit({'event':'step_update','step_update':{'subagent_info':{'subagents':[{}]},'step_index':1}})
if case=='unexpected_tool':emit({'event':'step_update','step_update':{'step_type':'tool','step_index':1,'tool_name':'run_command'}})
if case in ('write_step','mcp_step'):
    emit({'event':'step_update','step_update':{'step_type':'tool','step_index':1,'tool_name':'write_to_file' if case=='write_step' else 'mcp_send_message'}})
if case=='many_tools':
    for n in range(50):emit({'event':'step_update','step_update':{'step_type':'tool','step_index':n,'tool_name':'view_file'}})
if case=='auth':
    emit({'event':'result','result':{'status':'ERROR','error':'authentication required','num_turns':0}});sys.exit(1)
if case=='fail_usage':
    emit({'event':'result','result':{'status':'ERROR','error':'simulated failure','num_turns':1,'usage':{'input_tokens':100,'output_tokens':30,'total_tokens':130}}});sys.exit(1)
def review_usage():
    p=pathlib.Path('conversation_usage.json')
    previous=json.loads(p.read_text()) if '--conversation' in args and p.exists() else {}
    counts={key:previous.get(key,0)+value for key,value in
            {'input_tokens':100,'output_tokens':30,'total_tokens':130}.items()}
    p.write_text(json.dumps(counts))
    return counts
if case in ('quota','quota_after_progress'):
    if case=='quota_after_progress':
        pathlib.Path('conversation_state.txt').write_text('Verified checkpoint: version comparison precedes adoption.')
    emit({'event':'result','result':{'status':'ERROR','error':'Individual quota reached. Resets later.',
          'response':'Partial review.', 'num_turns':1,
          'usage':review_usage()}});sys.exit(1)
if agent in ('es-reviewer','es-editor'):
    response = 'Reviewed the supplied task.'
    if '--conversation' in args and pathlib.Path('conversation_state.txt').exists():
        assert 'Continue the original task' in text
        response += pathlib.Path('conversation_state.txt').read_text()
    if agent == 'es-editor':
        pathlib.Path(os.environ['FAKE_ORIGINAL_FILE']).write_text('edited\n')
        response = 'Edited the requested file.'
    if case == 'missing_response': response = ''
    if case == 'native_timeout':
        response = 'Partial review.'
        print('print timeout after 30m0s with turn in progress',file=sys.stderr)
    emit({'event':'result','result':{'conversation_id':'fixture-1','status':'SUCCESS',
          'response':response,'num_turns':1,'usage':review_usage()}})
    sys.exit(1 if case == 'nonzero_success' else 0)
root=pathlib.Path.cwd().parent/'sources'
manifest=json.loads((root.parent/'source-manifest.json').read_text())
source_files={entry['path']:root/entry['export_path'] for entry in manifest['files']}
if case=='snapshot_changed':
    file=source_files['src/example.py'];file.chmod(0o600);file.write_text('changed\n')
if case=='other_changed':
    file=source_files['src/other.py'];file.chmod(0o600);file.write_text('changed\n')
if case=='original_changed':
    pathlib.Path(os.environ['FAKE_ORIGINAL_FILE']).write_text('externally changed\n')
ref={'path':'src/example.py','start':1,'end':2,'symbol':'f','evidence':'Implementation of f.'}
if case=='absolute':ref['path']=str(source_files['src/example.py'])
if case=='outside':ref['path']='src/not_exported.py'
if case=='fake_hash':ref['sha256']='0'*64
if case=='bad_range':ref['end']=999
wire={'references':[ref],'unresolved':[]}
if case=='partial':wire.update(unresolved=['Caller not located.'])
if case=='ready_with_gap':wire.update(unresolved=['Caller not located.'])
if case=='not_found':wire.update(references=[],unresolved=['No matching symbol in exported files.'])
if case=='blocked':wire.update(references=[],unresolved=['Fixture permission denial.'])
# Per-step usage deliberately duplicates the terminal counters: must not be added.
emit({'event':'step_update','step_update':{'step_type':'agent_response','state':'DONE','step_index':99,'usage':{'input_tokens':100,'output_tokens':30,'total_tokens':130}}})
usage={'input_tokens':100,'output_tokens':30,'thinking_tokens':10,'cache_read_tokens':70,'total_tokens':130}
if '--conversation' in args: usage.update(review_usage())
if case=='no_usage':usage=None
if case=='bad_usage':usage['input_tokens']=True
if case=='large_cache':usage['cache_read_tokens']=1000
result={'conversation_id':'fixture-1','status':'SUCCESS','response':json.dumps(wire),'structured_output':wire,'num_turns':1,'usage':usage}
if case=='missing_structured':del result['structured_output']
if case=='native_timeout':
    del result['structured_output'];result['response']=''
    print('[agy] print timeout after 5m0s with turn in progress; returning partial output',file=sys.stderr)
if case=='two_turns':result['num_turns']=2
emit({'event':'result','result':result})
if case=='nonzero_success':sys.exit(1)
if case=='duplicate_result':emit({'event':'result','result':result})
