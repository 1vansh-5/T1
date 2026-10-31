import argparse
import asyncio
import json
from pathlib import Path
from engine import diagnose
from netcoop import SCENARIOS

if __name__=='__main__':
    p=argparse.ArgumentParser(description='NetCoop AI evidence-based diagnosis')
    p.add_argument('--scenario',choices=SCENARIOS,default='congestion')
    p.add_argument('--input',type=Path)
    p.add_argument('--gemini',action='store_true')
    p.add_argument('--output',type=Path)
    a=p.parse_args()
    raw=json.loads(a.input.read_text(encoding='utf-8')) if a.input else None
    result=asyncio.run(diagnose('custom' if raw is not None else a.scenario,raw,use_llm=a.gemini))
    text=json.dumps(result,indent=2)
    if a.output:a.output.write_text(text,encoding='utf-8')
    else:print(text)
