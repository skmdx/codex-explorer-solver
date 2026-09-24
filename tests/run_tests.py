#!/usr/bin/env python3
"""Run isolated AGY subprocess fixtures in parallel; shared-state tests sequentially.

No installed AGY/Codex, Google access or model inference is used by this suite.
"""
import concurrent.futures
import io
from pathlib import Path
import sys
import time
import unittest
TESTS=Path(__file__).resolve().parent
sys.path.insert(0,str(TESTS))

def flatten(suite):
    for item in suite:
        if isinstance(item,unittest.TestSuite):yield from flatten(item)
        else:yield item

def main():
    started=time.monotonic()
    cases=list(flatten(unittest.defaultTestLoader.discover(str(TESTS))))
    parallel=[x for x in cases if x.__class__.__name__=='AgyRunnerTests']
    serial=[x for x in cases if x.__class__.__name__!='AgyRunnerTests']
    def run(case):
        text=io.StringIO();result=unittest.TextTestRunner(stream=text,verbosity=2).run(unittest.TestSuite([case]))
        return result,text.getvalue()
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        for result,text in executor.map(run,parallel):
            print(text,end='',flush=True);results.append(result)
    result=unittest.TextTestRunner(stream=sys.stdout,verbosity=2).run(unittest.TestSuite(serial))
    results.append(result)
    total=sum(x.testsRun for x in results);failed=sum(len(x.failures)+len(x.errors) for x in results)
    skipped=sum(len(x.skipped) for x in results)
    print(f'\nAGGREGATE: {total} tests, {failed} failures/errors, {skipped} skipped, {time.monotonic()-started:.2f}s',flush=True)
    print('Real AGY/Codex/model calls: 0. AGY subprocesses are explicitly marked test doubles.',flush=True)
    return 1 if failed else 0
if __name__=='__main__':raise SystemExit(main())
