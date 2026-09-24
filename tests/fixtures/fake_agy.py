#!/usr/bin/env python3
"""TEST DOUBLE ONLY. Does not contact Google or implement model reasoning."""
import json, os, pathlib, sys, time
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
assert '--dangerously-skip-permissions' not in args
assert '--continue' not in args and '--conversation' not in args and '-p' not in args
assert args[args.index('--input-format')+1]=='stream-json'
lines=sys.stdin.buffer.readlines();assert len(lines)==1
message=json.loads(lines[0]);assert message['event']=='user'
text=message['message']['content'];case=os.getenv('FAKE_CASE','ok')
def emit(value):print(json.dumps(value),flush=True)
tools=[] if agent=='es-reader' else ['view_file','grep_search']
if case=='write_tool':tools+=['write_to_file']
if case=='mcp_tool':tools+=['mcp_send_message']
if case=='bad_model':model='gemini-3.7-flash-medium'
if case=='bad_agent':agent='self'
init={'event':'init','conversation_id':'fixture-1','init':{'cwd':os.getcwd(),'model':model,'agent':agent,'tools':tools,'permission_mode':'request-review'}}
if case=='bypass':init['init']['permission_mode']='always-proceed'
if case!='no_init':emit(init)
if case=='timeout':time.sleep(10)
if case=='invalid_json':print('NOT JSON',flush=True);sys.exit(0)
if case=='nested':emit({'event':'step_update','step_update':{'subagent_info':{'subagents':[{}]},'step_index':1}})
if case=='unexpected_tool':emit({'event':'step_update','step_update':{'step_type':'tool','step_index':1,'tool_name':'run_command'}})
if case=='tool_limit':
    for n in range(11):emit({'event':'step_update','step_update':{'step_type':'tool','step_index':n,'tool_name':'view_file'}})
if case=='auth':
    emit({'event':'result','result':{'status':'ERROR','error':'authentication required','num_turns':0}});sys.exit(1)
if case=='fail_usage':
    emit({'event':'result','result':{'status':'ERROR','error':'simulated failure','num_turns':1,'usage':{'input_tokens':100,'output_tokens':30,'total_tokens':130}}});sys.exit(1)
root=pathlib.Path.cwd()
if case=='snapshot_changed':
    file=root/'src/example.py';file.chmod(0o600);file.write_text('changed\n')
if case=='other_changed':
    file=root/'src/other.py';file.chmod(0o600);file.write_text('changed\n')
if case=='original_changed':
    pathlib.Path(os.environ['FAKE_ORIGINAL_FILE']).write_text('externally changed\n')
ref={'path':'src/example.py','start':1,'end':2,'symbol':'f','evidence':'Implementation of f.'}
if case=='outside':ref['path']='src/not_exported.py'
if case=='fake_hash':ref['sha256']='0'*64
if case=='bad_range':ref['end']=999
wire={'version':1,'status':'ready','stop_reason':'evidence_ready','primary':[ref],'related':[],'unresolved':[]}
if case=='partial':wire.update(status='partial',stop_reason='budget',unresolved=['Caller not located.'])
if case=='not_found':wire.update(status='not_found',stop_reason='no_match',primary=[],unresolved=['No matching symbol in exported files.'])
if case=='blocked':wire.update(status='blocked',stop_reason='environment',primary=[],unresolved=['Fixture permission denial.'])
# Per-step usage deliberately duplicates the terminal counters: must not be added.
emit({'event':'step_update','step_update':{'step_type':'agent_response','state':'DONE','step_index':99,'usage':{'input_tokens':100,'output_tokens':30,'total_tokens':130}}})
usage={'input_tokens':100,'output_tokens':30,'thinking_tokens':10,'cache_read_tokens':70,'total_tokens':130}
if case=='no_usage':usage=None
if case=='bad_usage':usage['input_tokens']=True
if case=='large_cache':usage['cache_read_tokens']=1000
result={'conversation_id':'fixture-1','status':'SUCCESS','response':json.dumps(wire),'structured_output':wire,'num_turns':1,'usage':usage}
if case=='missing_structured':del result['structured_output']
if case=='two_turns':result['num_turns']=2
emit({'event':'result','result':result})
if case=='duplicate_result':emit({'event':'result','result':result})
