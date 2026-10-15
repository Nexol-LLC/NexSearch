import argparse,json,sys
sys.stdout.reconfigure(encoding='utf-8')
from network.updates import prepare,apply,rollback
def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','apply','rollback','check-daily']);p.add_argument('--archive');p.add_argument('--program');p.add_argument('--home');p.add_argument('--role');p.add_argument('--state');p.add_argument('--folder');args=p.parse_args()
 try:
  if args.action=='check-daily':
   import os
   os.environ['NEXOL_NO_BROWSER']='1'
   from network.daily_updates import check_update,apply_pending_on_exit
   from pathlib import Path
   trust=json.loads((Path(args.program)/'client-trust.json').read_text(encoding='utf-8'));result=check_update(args.program,args.home,trust)
   # A closed client can install immediately; an active client installs on exit.
   from network.launcher import Instance
   instance=Instance(Path(args.home))
   if instance.acquire():instance.close();apply_pending_on_exit(args.program,args.home)
   else:instance.close()
  else:
   result=prepare(args.archive,args.program,args.home,args.role) if args.action=='prepare' else apply(args.state) if args.action=='apply' else rollback(args.folder)
   if args.action=='apply' and args.home:
    from network.daily_updates import record_applied
    record_applied(args.home,args.state)
  print(json.dumps(result,ensure_ascii=False),flush=True)
 except Exception as e:
  if args.action=='apply' and args.home:
   from network.daily_updates import record_apply_error
   record_apply_error(args.home,e)
  print(json.dumps({'error':str(e)},ensure_ascii=False),flush=True);raise SystemExit(1)
if __name__=='__main__':main()
