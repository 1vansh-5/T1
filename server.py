import asyncio
import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from engine import diagnose
from netcoop import SCENARIOS, generate
import os

ROOT=Path(__file__).resolve().parent
DB=ROOT/'data'/'incidents.sqlite3'
app=FastAPI(title='NetCoop AI',version='2.0.0')
gate=asyncio.Semaphore(3)

class Request(BaseModel):
    model_config=ConfigDict(extra='forbid')
    scenario: str='congestion'
    telemetry: dict | None=None
    seed: int=Field(default=7,ge=0,le=1000000)
    mode: Literal['demo','gemini']='demo'

def validate(body):
    if body.telemetry is None and body.scenario not in SCENARIOS:
        raise HTTPException(422,'Unknown scenario')
    if len(json.dumps(body.telemetry))>250000:
        raise HTTPException(413,'Telemetry is limited to 250 KB')

def connection():
    DB.parent.mkdir(exist_ok=True)
    db=sqlite3.connect(DB)
    db.execute('CREATE TABLE IF NOT EXISTS incidents (id TEXT PRIMARY KEY, created REAL, payload TEXT)')
    return db

def save(result):
    with connection() as db:
        r=result['report']
        db.execute('INSERT OR REPLACE INTO incidents VALUES (?,?,?)',(r['incident_id'],r['created_at'],json.dumps(result)))
        db.execute('DELETE FROM incidents WHERE id NOT IN (SELECT id FROM incidents ORDER BY created DESC LIMIT 100)')

@app.get('/api/health')
def health():
    return {'status':'ok','gemini_configured':bool(os.getenv('GEMINI_API_KEY')),'model':os.getenv('GEMINI_MODEL','gemini-flash-latest'),'telemetry_source':'synthetic / user supplied','mcp_transport':'stdio'}

@app.get('/api/scenarios')
def scenarios():
    return SCENARIOS

@app.get('/api/telemetry/{scenario}')
def telemetry(scenario: str):
    if scenario not in SCENARIOS:
        raise HTTPException(404,'Unknown scenario')
    return generate(scenario)

async def execute(body,emit=None):
    async with gate:
        result=await diagnose('custom' if body.telemetry is not None else body.scenario,body.telemetry,body.seed,body.mode=='gemini',emit)
        await asyncio.to_thread(save,result)
        return result

@app.post('/api/diagnose')
async def run(body:Request):
    validate(body)
    return await execute(body)

@app.post('/api/diagnose/stream')
async def stream(body:Request):
    validate(body)
    async def events():
        queue=asyncio.Queue()
        async def work():
            try:
                result=await execute(body,queue.put)
                await queue.put({'event':'complete','data':result})
            except Exception:
                await queue.put({'event':'error','data':{'message':'Run failed; retry or check server logs.'}})
            finally:
                await queue.put(None)
        task=asyncio.create_task(work())
        try:
            while True:
                event=await queue.get()
                if event is None:
                    break
                yield json.dumps(event)+'\n'
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    return StreamingResponse(events(),media_type='application/x-ndjson',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

@app.get('/api/incidents')
def history():
    with connection() as db:
        return [json.loads(row[0])['report'] for row in db.execute('SELECT payload FROM incidents ORDER BY created DESC LIMIT 20')]

@app.get('/api/incidents/{incident}')
def detail(incident:str):
    with connection() as db:
        row=db.execute('SELECT payload FROM incidents WHERE id=?',(incident,)).fetchone()
    if not row:
        raise HTTPException(404,'Incident not found')
    return json.loads(row[0])

app.mount('/',StaticFiles(directory=ROOT/'static',html=True),name='dashboard')
