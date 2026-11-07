from contextlib import asynccontextmanager
import threading
import httpx

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text

from .agent import stream_chat
from .config import API_KEY, DEFAULT_SYMBOLS, MODEL, NSE_SYMBOLS, US_SYMBOLS
from .ipo import indian_ipos
from .db import Conversation, Message, Scan, Session, Setting, ResearchRun, engine, init_db
from .schemas import ChatRequest, ScanRequest, WatchlistRequest, WatchlistSymbolRequest
from .journal import router as journal_router
from .strategies import STRATEGIES
from .universes import get_universe, universe_options
from .research import PROCESS_ID

chat_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


app = FastAPI(title='Signal Desk API', version='1.0.0', lifespan=lifespan, docs_url='/api/docs', openapi_url='/api/openapi.json', redoc_url=None)
app.include_router(journal_router)


def scan_dict(scan):
    return {'id': scan.id, 'status': scan.status, 'created_at': scan.created_at.isoformat(), 'request': scan.request, 'result': scan.result, 'error': scan.error}


@app.get('/api/health')
def health():
    with engine.connect() as conn:
        conn.execute(text('SELECT 1'))
    return {'status': 'ok', 'database': 'connected', 'ai_configured': bool(API_KEY), 'model': MODEL}


@app.get('/api/config')
def config():
    return {'model': MODEL, 'ai_configured': bool(API_KEY), 'default_market': 'IN', 'strategies': [{'id': k, **v} for k, v in STRATEGIES.items()], 'screen_universes': universe_options(), 'universes': {'US leaders': US_SYMBOLS, 'NSE leaders': NSE_SYMBOLS}}


@app.get('/api/universes/{universe}')
def index_universe(universe: str):
    if universe not in {u['id'] for u in universe_options()}:
        raise HTTPException(404, 'Index universe not found.')
    try:
        return get_universe(universe)
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, 'NSE Indices constituent data is unavailable or incomplete. Retry later or screen your saved watchlist.')


@app.get('/api/ipos')
def ipos(market: str = Query('IN', pattern='^(IN|US)$')):
    if market != 'IN':
        raise HTTPException(422, 'IPO research is available for the Indian market only.')
    return indian_ipos()


def watchlist_value(db, market):
    setting = db.get(Setting, 'watchlist_' + market)
    if setting:
        return setting.value
    legacy = db.get(Setting, 'watchlist')
    symbols = [s for s in legacy.value['symbols'] if s.endswith(('.NS', '.BO')) == (market == 'IN')] if legacy else []
    return {'market': market, 'symbols': symbols or (NSE_SYMBOLS if market == 'IN' else US_SYMBOLS)}


@app.get('/api/watchlist')
def watchlist(market: str = Query('IN', pattern='^(IN|US)$')):
    with Session() as db:
        return watchlist_value(db, market)


@app.put('/api/watchlist')
def save_watchlist(request: WatchlistRequest):
    with Session.begin() as db:
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key': 'watchlist_' + request.market})
        setting = db.get(Setting, 'watchlist_' + request.market)
        if setting:
            setting.value = request.model_dump()
        else:
            db.add(Setting(key='watchlist_' + request.market, value=request.model_dump()))
    return request


@app.post('/api/watchlist/symbols')
def add_watchlist_symbol(request: WatchlistSymbolRequest):
    with Session.begin() as db:
        key = 'watchlist_' + request.market
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key': key})
        current = watchlist_value(db, request.market)['symbols']
        symbols = list(dict.fromkeys([*current, request.symbol]))
        if len(symbols) > 20:
            raise HTTPException(422, 'Your watchlist has 20 stocks. Remove one before adding another.')
        value = {'market': request.market, 'symbols': symbols}
        setting = db.get(Setting, key)
        if setting:
            setting.value = value
        else:
            db.add(Setting(key=key, value=value))
        return value


@app.post('/api/scans', status_code=202)
def start_scan(request: ScanRequest):
    if request.universe != 'watchlist':
        data = index_universe(request.universe)
        request = ScanRequest(**{**request.model_dump(), 'symbols': data['symbols'], 'universe_source': data['source'], 'universe_fetched_at': data['fetched_at']})
    with Session.begin() as db:
        queued = db.scalars(select(Scan).where(Scan.status.in_(['queued', 'running'])).limit(4)).all()
        if len(queued) >= 4:
            raise HTTPException(429, 'Four scans are already in progress. Wait for one to finish.')
        scan = Scan(request=request.model_dump())
        db.add(scan)
        db.flush()
        return scan_dict(scan)


@app.get('/api/scans')
def list_scans():
    with Session() as db:
        scans = db.scalars(select(Scan).order_by(Scan.created_at.desc()).limit(30)).all()
        return [scan_dict(s) for s in scans]


@app.get('/api/scans/{scan_id}')
def get_scan(scan_id: str):
    with Session() as db:
        scan = db.get(Scan, scan_id)
        if scan is None:
            raise HTTPException(404, 'Scan not found.')
        return scan_dict(scan)


@app.get('/api/conversations')
def conversations():
    with Session() as db:
        return [{'id': c.id, 'title': c.title, 'created_at': c.created_at.isoformat()} for c in db.scalars(select(Conversation).order_by(Conversation.created_at.desc()).limit(50))]


@app.get('/api/research/{run_id}')
def research_report(run_id: str):
    with Session.begin() as db:
        run = db.get(ResearchRun, run_id)
        if not run:
            raise HTTPException(404, 'Research run not found.')
        if run.status == 'running' and run.reports.get('process_id') != PROCESS_ID:
            run.status = 'incomplete'
        return {'id': run.id, 'scan_id': run.scan_id, 'status': run.status, 'created_at': run.created_at.isoformat(), 'reports': run.reports}


@app.post('/api/conversations', status_code=201)
def new_conversation():
    with Session.begin() as db:
        c = Conversation()
        db.add(c)
        db.flush()
        return {'id': c.id, 'title': c.title}


@app.get('/api/conversations/{conversation_id}/messages')
def messages(conversation_id: str):
    with Session() as db:
        if db.get(Conversation, conversation_id) is None:
            raise HTTPException(404, 'Conversation not found.')
        rows = db.scalars(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)).all()
        runs = db.scalars(select(ResearchRun).where(ResearchRun.conversation_id == conversation_id).order_by(ResearchRun.created_at)).all()
        result, user_at = [], None
        for m in rows:
            if m.role == 'user':
                user_at = m.created_at
            run = next((r for r in reversed(runs) if user_at and user_at <= r.created_at <= m.created_at), None) if m.role == 'assistant' else None
            result.append({'id': m.id, 'role': m.role, 'content': m.content, 'scan_id': m.scan_id, 'research_id': run.id if run else None})
        return result


@app.post('/api/chat')
async def chat(request: ChatRequest):
    validated = ScanRequest(market=request.market, symbols=request.symbols, strategies=request.strategies)
    request.symbols, request.strategies = validated.symbols, validated.strategies
    if not chat_lock.acquire(blocking=False):
        raise HTTPException(409, 'An assistant response is already running. Please wait.')
    try:
        with Session.begin() as db:
            c = db.get(Conversation, request.conversation_id)
            if c is None:
                raise HTTPException(404, 'Conversation not found.')
            if request.scan_id:
                linked = db.get(Scan, request.scan_id)
                if linked is None:
                    raise HTTPException(404, 'Scan not found.')
                try:
                    linked_market = ScanRequest(**linked.request).market
                except ValueError:
                    raise HTTPException(422, 'This legacy mixed-market scan cannot be used as context. Run a new screen.')
                if linked_market != request.market:
                    raise HTTPException(422, 'Selected scan belongs to another market. Run a screen in the selected market.')
            if c.title == 'New conversation':
                c.title = request.message[:80]
            db.add(Message(conversation_id=c.id, role='user', content=request.message))
    except Exception:
        chat_lock.release()
        raise

    async def generate():
        try:
            async for chunk in stream_chat(request):
                yield chunk
        finally:
            chat_lock.release()
    return StreamingResponse(generate(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
