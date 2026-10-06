"""Task routing and atomic, persistent Gemini request budgets; stores no questions.

The ledger counts local attempts conservatively. Provider quota errors remain
authoritative because other applications can use the same Google project.
"""
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from src.core.source_policy import ROOT, load_policy


class RoutingStop(Exception):
    """No eligible model can safely finish the requested stage."""


def routing_config():
    return json.loads((ROOT / 'configs/model_routing.json').read_text(encoding='utf-8'))


def models_for(contract, payload, config, repair=False):
    name = contract.__name__
    base = config['pools'][config['contracts'][name]]
    strong = config['pools']['strong']
    # C qualification checks must never silently downgrade to a Lite model.
    if payload.get('level') == 'LEVEL_C' and name in config['sensitive_contracts']:
        return list(strong)
    complex_review = name in config['complex_contracts'] and (
        len(payload.get('question_parts', [])) >= config['complex_parts'] or
        len(payload.get('required_languages', [])) >= config['complex_languages'])
    if repair or complex_review:
        return list(dict.fromkeys(strong + base))
    return list(base)


def error_category(exc):
    """Distinguish daily exhaustion from RPM/TPM, outages and invalid model IDs."""
    detail = str(exc)
    for field in ('details', 'response_json'):
        value = getattr(exc, field, None)
        if value:
            detail += json.dumps(value, default=str)
    detail = detail.lower()
    code = getattr(exc, 'code', None) or getattr(exc, 'status_code', None)
    # Billing can also arrive as 429 RESOURCE_EXHAUSTED; inspect it first.
    if str(code) in ('401','403'):
        return 'access', 0
    if str(code)=='402' or 'prepayment credits are depleted' in detail:
        return 'billing', 0
    if str(code) == '429' or 'resource_exhausted' in detail:
        if any(s in detail for s in ('perday', 'per_day', 'per day', 'daily', 'rpd')):
            return 'daily', 0
        # Unknown 429 is NOT evidence that the daily quota is finished.
        match = re.search(r'"retrydelay"\s*:\s*"([\d.]+)s"|retry in ([\d.]+)s', detail)
        delay = float(next(g for g in match.groups() if g)) if match else None
        return 'rate', delay
    if (str(code)=='404' and ('not found' in detail or 'not_found' in detail) or
            str(code)=='400' and any(s in detail for s in ('model is not supported', 'not supported for generatecontent', 'does not support generatecontent'))):
        return 'unavailable', 0
    return 'service', 0


class QuotaLedger:
    def __init__(self, directory=None, config=None, clock=time.time):
        self.config = config or routing_config()
        self.clock = clock
        # All configured keys in this app share a Google-project budget by default.
        # Set this ID when hosting different Google projects on one filesystem.
        self.project = os.getenv('GEMINI_QUOTA_PROJECT', 'configured-project')
        directory = Path(directory or os.getenv('BAYAN_PRIVATE_STORE') or ROOT / load_policy()['private_store'])
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / 'model_quota.db'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS budgets (project TEXT, day TEXT, model TEXT, used INTEGER NOT NULL, cooldown REAL NOT NULL, disabled INTEGER NOT NULL, PRIMARY KEY(project,day,model))')
            db.execute('CREATE TABLE IF NOT EXISTS attempts (project TEXT, model TEXT, at REAL, task TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS attempt_times ON attempts(project,model,at)')
            db.execute('CREATE TABLE IF NOT EXISTS failures (project TEXT, model TEXT, at REAL, kind TEXT, code INTEGER)')
            db.execute('DELETE FROM failures WHERE at < ?', (self.clock()-86400*2,))
            db.execute('DELETE FROM attempts WHERE at < ?', (self.clock()-86400*2,))
            db.execute('DELETE FROM budgets WHERE day < ?', (self.day(self.clock()-86400*3),))
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=5)
        db.execute('PRAGMA busy_timeout=5000')
        return db

    def day(self, now):
        return datetime.fromtimestamp(now, ZoneInfo(self.config['quota_timezone'])).date().isoformat()

    def reset_at(self, now=None):
        now = self.clock() if now is None else now
        local = datetime.fromtimestamp(now, ZoneInfo(self.config['quota_timezone']))
        reset = datetime.combine(local.date()+timedelta(days=1), datetime.min.time(), local.tzinfo)
        return reset.astimezone(timezone.utc).isoformat()

    def rows(self, db, models, now):
        day = self.day(now)
        for model in models:
            limit = self.config['models'][model]
            db.execute('INSERT OR IGNORE INTO budgets VALUES(?,?,?,?,?,?)', (self.project, day, model, 0, 0, 0))
            used, cooldown, disabled = db.execute('SELECT used,cooldown,disabled FROM budgets WHERE project=? AND day=? AND model=?', (self.project, day, model)).fetchone()
            # A transient catalog/endpoint 404 must not blacklist an available
            # model for the entire quota day. Legacy exclusions have cooldown=0.
            if disabled and cooldown<=now:
                db.execute('UPDATE budgets SET disabled=0 WHERE project=? AND day=? AND model=?',
                    (self.project,day,model))
                disabled=0
            recent = [r[0] for r in db.execute('SELECT at FROM attempts WHERE project=? AND model=? AND at>? ORDER BY at', (self.project, model, now-60))]
            rpm=limit.get('rpm')
            wait = max(0, cooldown-now, recent[-rpm]+60-now if rpm and len(recent)>=rpm else 0)
            yield model, used, disabled, wait

    def check_billing(self, db, now):
        recent=db.execute("SELECT kind FROM failures WHERE project=? AND kind IN ('billing','access') AND at>? ORDER BY at DESC LIMIT 1",
            (self.project,now-self.config['billing_probe_interval_seconds'])).fetchone()
        if recent:raise RoutingStop('model_access_denied' if recent[0]=='access' else 'model_billing_unavailable')

    def ensure_available(self):
        with self.connect() as db:
            self.check_billing(db,self.clock())
            rows = list(self.rows(db, list(self.config['models']), self.clock()))
        if all(used >= self.config['models'][model]['rpd'] for model, used, _, _ in rows):
            raise RoutingStop('quota_finished')
        if not any(not disabled and used < self.config['models'][model]['rpd'] for model, used, disabled, _ in rows):
            raise RoutingStop('model_service_unavailable')

    def acquire(self, models, task, deadline, excluded=()):
        wait_budget = min(self.config['max_rate_wait_seconds'], max(0, deadline-time.monotonic()))
        until = time.monotonic()+wait_budget
        while True:
            now = self.clock()
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                self.check_billing(db,now)
                rows = list(self.rows(db, models, now))
                eligible = [(m, wait) for m, used, disabled, wait in rows
                    if not disabled and used < self.config['models'][m]['rpd'] and m not in excluded]
                for model, wait in eligible:
                    if wait <= 0:
                        db.execute('UPDATE budgets SET used=used+1 WHERE project=? AND day=? AND model=?', (self.project, self.day(now), model))
                        db.execute('INSERT INTO attempts VALUES(?,?,?,?)', (self.project, model, now, task))
                        return model
            if not eligible:
                if all(used >= self.config['models'][m]['rpd'] for m, used, _, _ in rows):
                    raise RoutingStop('quota_finished')
                raise RoutingStop('model_service_unavailable')
            delay = min(wait for _, wait in eligible)
            if delay > max(0, until-time.monotonic()):
                raise RoutingStop('model_rate_limited')
            time.sleep(min(delay+.01, max(0, until-time.monotonic())))

    def failure(self, model, exc):
        kind, delay = error_category(exc)
        now = self.clock()
        code = getattr(exc, 'code', None) or getattr(exc, 'status_code', None)
        # Never persist provider messages: they can contain keys or user content.
        code = int(str(code)) if str(code).isdigit() else None
        with self.connect() as db:
            list(self.rows(db, [model], now))
            db.execute('INSERT INTO failures VALUES(?,?,?,?,?)', (self.project,model,now,kind,code))
            if kind == 'daily':
                db.execute('UPDATE budgets SET used=? WHERE project=? AND day=? AND model=?', (self.config['models'][model]['rpd'], self.project, self.day(now), model))
            elif kind == 'unavailable':
                db.execute('UPDATE budgets SET disabled=1,cooldown=? WHERE project=? AND day=? AND model=?',
                    (now+self.config['unavailable_cooldown_seconds'], self.project, self.day(now), model))
            else:
                cooldown = now + ((delay if delay is not None else self.config['unknown_rate_cooldown_seconds']) if kind=='rate' else self.config['service_cooldown_seconds'])
                db.execute('UPDATE budgets SET cooldown=MAX(cooldown,?) WHERE project=? AND day=? AND model=?', (cooldown, self.project, self.day(now), model))
        return kind

    def mark_exhausted(self, model):
        """Record an operator-confirmed daily exhaustion, without an API call."""
        class DailyLimit(Exception):
            code = 429
        self.failure(model, DailyLimit('Daily request quota exhausted'))

    def snapshot(self):
        with self.connect() as db:
            rows = list(self.rows(db, list(self.config['models']), self.clock()))
        return {'reset_at': self.reset_at(), 'scope': 'Local conservative attempts; provider errors also enforced. External project usage is not directly observable.',
            'models': {m: {'used': used, 'remaining': max(0,self.config['models'][m]['rpd']-used),
                'rpd':self.config['models'][m]['rpd'], 'rpm':self.config['models'][m]['rpm'],
                'disabled':bool(disabled), 'wait_seconds':round(wait,2)} for m,used,disabled,wait in rows}}
