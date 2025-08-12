# -*- coding: utf-8 -*-
"""
Appli Cafétéria — backend 100% Google Sheets (pas de base locale)
- Conserve le classeur Google (partage facile avec collègues)
- Sert la page d'inscription et la page Caisse (utilisable sur une tablette)
- Envoie l'e-mail de clôture avec un lien direct vers la page Caisse (LAN)

Déploiement sans installation sur le PC cible : on produira un exécutable Linux (PyInstaller)
Fichiers nécessaires sur le PC:
  - ./Cafeteria  (l'exécutable généré)
  - ./credentials.json (clé Service Account)
Variables d'environnement requises :
  - SPREADSHEET_ID=... (ID du classeur)
  - HOST=0.0.0.0 (si la tablette doit y accéder via le réseau local, sinon 127.0.0.1)
  - PORT=8000 (optionnel)
  - SMTP_* (optionnels pour l'e-mail)

Sheets attendus dans le classeur (comme ton GAS) :
  - Paramètres : colonnes (A:E) -> date_iso, jour, menu, open, disabled (booléens)
  - Réservations : colonnes (A:C) -> date_iso, name, timestamp
  - Caisse : colonnes (A:H) -> date_iso, nom, type, base, boisson, chocolat, total, timestamp

Remarque : ton GAS utilisait la couleur de police rouge pour "disabled". Ici on prend une
colonne booléenne E=disabled pour simplifier.
"""
from __future__ import annotations
import os
from datetime import date, datetime
from typing import Dict, List, Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import gspread
from google.oauth2.service_account import Credentials

APP_TITLE = "CAFÉTÉRIA CO FLORENCE"
DAYS = ["Dimanche","Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi"]
PRICES = {"ELEVE": 8.0, "PROF": 12.0, "SANDWICH": 6.0, "BOISSON": 2.0, "CHOCOLAT": 1.5}
CASH_FLOAT = 150.0
MAX_RESAS = 40
MAX_MENUS = 45

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))

# ---------------- Sheets client ----------------
_scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.readonly"]
_creds = Credentials.from_service_account_file(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json"), scopes=_scopes)
_gc = gspread.authorize(_creds)
_ss = _gc.open_by_key(SPREADSHEET_ID) if SPREADSHEET_ID else None


def ws(name: str):
    global _ss
    if _ss is None:
        raise RuntimeError("SPREADSHEET_ID manquant. Définis la variable d'environnement.")
    try:
        return _ss.worksheet(name)
    except gspread.WorksheetNotFound:
        sh = _ss.add_worksheet(title=name, rows=1, cols=8)
        if name == "Caisse":
            sh.append_row(["date","nom","type","base","boisson","chocolat","total","timestamp"])  # header
        return sh


def today_iso() -> str:
    now = datetime.now()
    return date(now.year, now.month, now.day).isoformat()


def pretty_fr_header(iso: str) -> str:
    y, m, d = map(int, iso.split("-"))
    dt = date(y, m, d)
    days = ["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"]
    # Python: Monday=0..Sunday=6 ; ici on veut le bon label FR
    return f"{days[dt.weekday()]} {d:02d}.{m:02d}"


def to_iso_any(s: str) -> str:
    s = (s or "").strip()
    if len(s) == 10 and s[2] == "." and s[5] == ".":
        dd, mm, yyyy = s.split(".")
        return f"{int(yyyy):04d}-{int(mm):02d}-{int(dd):02d}"
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    try:
        return datetime.fromisoformat(s).date().isoformat()
    except Exception:
        return today_iso()


def norm_name(s: str) -> str:
    return " ".join((s or "").strip().upper().split())

# ---------------- FastAPI & Templates ----------------
app = FastAPI(title=APP_TITLE)
TEMPLATES = Jinja2Templates(directory="templates")

PAGE_HTML = """<!DOCTYPE html><html><head><meta charset='utf-8'><title>CAFÉTÉRIA CO FLORENCE</title>
<style>body{margin:0;padding:0;font-family:sans-serif;text-align:center}h1{color:red}</style></head>
<body>
  <h1>CAFÉTÉRIA CO FLORENCE</h1>
  <div id="jours"></div><div id="list" style="display:none;"></div>
<script>
let availableDays=[], reservationsMap={};
async function loadInitialData(){ const r = await fetch('/api/initial'); const data = await r.json(); availableDays=data.days; reservationsMap=data.reservations; renderHome(); }
function renderHome(){ const joursDiv=document.getElementById('jours'), listDiv=document.getElementById('list'); listDiv.style.display='none'; joursDiv.style.display='block'; joursDiv.innerHTML=''; availableDays.forEach(d=>{ const div=document.createElement('div'); div.style.cssText='display:inline-block;border:1px solid #ccc;padding:10px;margin:10px;border-radius:8px;min-width:200px;cursor:'+(d.open&&!d.disabled?'pointer':'default')+';opacity:'+(d.open?1:.4);'+(d.disabled?'color:red;border-color:red;':''); div.innerHTML='<strong>'+d.jour+'</strong><div>'+d.date+'</div><div style="white-space:pre-line">'+(d.menu||'')+'</div>'; if(d.open&&!d.disabled){ div.onclick=()=>showList(d.date);} joursDiv.appendChild(div); }); }
function showList(date){ const joursDiv=document.getElementById('jours'), listDiv=document.getElementById('list'); joursDiv.style.display='none'; listDiv.style.display='block'; listDiv.innerHTML=''; const dayObj=availableDays.find(d=>d.date===date); const h2=document.createElement('h2'); h2.textContent='Inscriptions pour '+(dayObj?dayObj.jour+' ':'')+date; listDiv.appendChild(h2); if(dayObj){ const m=document.createElement('div'); m.textContent=dayObj.menu||''; listDiv.appendChild(m); } const list=(reservationsMap[date]||[]); const actions=document.createElement('div'); const back=document.createElement('button'); back.textContent='← Retour'; back.onclick=renderHome; actions.appendChild(back); if(list.length<40){ const ins=document.createElement('button'); ins.textContent="S'inscrire"; ins.onclick=()=>openRegisterModal(date); actions.appendChild(ins); } listDiv.appendChild(actions); const grid=document.createElement('div'); grid.style.cssText='display:flex;gap:8px;justify-content:center;margin:10px auto;max-width:800px;'; for(let c=0;c<4;c++){ const col=document.createElement('div'); col.style.cssText='flex:1;display:flex;flex-direction:column;gap:4px;'; const slice=list.slice(c*10,c*10+10); if(!slice.length){ const e=document.createElement('div'); e.style.visibility='hidden'; e.textContent='—'; col.appendChild(e);} slice.forEach(n=>{ const p=document.createElement('div'); p.style.cssText='border:1px solid #ccc;border-radius:8px;padding:6px 10px;'; p.textContent=n; p.onclick=()=>openUnregisterModal(n,date); col.appendChild(p); }); grid.appendChild(col);} listDiv.appendChild(grid); }
function modal(html, bind){ const d=document.createElement('div'); d.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;'; const b=document.createElement('div'); b.style.cssText='background:#fff;padding:20px;border-radius:10px;min-width:300px;'; b.innerHTML=html; d.appendChild(b); document.body.appendChild(d); bind({close:()=>d.remove()}); }
function openRegisterModal(date){ modal('<h3>Votre nom</h3><input id="n" style="width:100%"><div style="text-align:right;margin-top:10px"><button id="ok">Valider</button></div>', ({close})=>{ document.getElementById('ok').onclick=async ()=>{ const name=document.getElementById('n').value.trim(); if(!name)return; const r=await fetch('/api/reserve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name, dateStr:date})}); alert(await r.text()); close(); loadInitialData(); }; }); }
function openUnregisterModal(name,date){ modal('<h3>Désinscrire '+name+' ?</h3><div style="text-align:right;margin-top:10px"><button id="ok">Confirmer</button></div>', ({close})=>{ document.getElementById('ok').onclick=async ()=>{ const r=await fetch('/api/unreserve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name, dateStr:date})}); alert(await r.text()); close(); loadInitialData(); }; }); }
window.addEventListener('load', loadInitialData);
</script></body></html>"""

CAISSE_HTML = """<!DOCTYPE html><html><head><meta charset='utf-8'><title>Caisse — Cafétéria</title>
<style>body{margin:0;padding:0;font-family:sans-serif}</style></head>
<body>
<header style='position:sticky;top:0;background:#f7f7f7;border-bottom:1px solid #ddd;padding:10px;display:flex;gap:8px;align-items:center;justify-content:space-between'>
  <h3 id='title'>Caisse</h3>
  <div>
    <button id='walkin'>+ Menu spontané</button>
    <button id='sand'>+ Sandwich (6)</button>
    <button id='bev'>+ Boisson (2)</button>
    <button id='choc'>+ Chocolat (1.5)</button>
    <button id='close'>Fermer</button>
  </div>
</header>
<section id='totals' style='padding:8px 12px;display:flex;gap:8px;flex-wrap:wrap'></section>
<section id='names' style='display:grid;grid-template-columns:repeat(4,1fr);gap:8px;padding:8px 12px'></section>
<div id='toast' style='position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:rgba(0,0,0,.85);color:#fff;padding:8px 12px;border-radius:8px;display:none'></div>
<script>
let dateIso=null; function chf(n){return (Math.round(n*100)/100).toFixed(2)+' CHF'}
function toast(m){const t=document.getElementById('toast');t.textContent=m;t.style.display='block';setTimeout(()=>t.style.display='none',1200)}
function fmtHeader(iso){if(!iso)return'';const p=iso.split('-').map(Number);const d=new Date(p[0],p[1]-1,p[2]);const days=['Dimanche','Lundi','Mardi','Mercredi','Jeudi','Vendredi','Samedi'];return 'Caisse - '+days[d.getDay()]+' '+String(p[2]).padStart(2,'0')+'.'+String(p[1]).padStart(2,'0')}
async function refresh(){ const r=await fetch('/api/caisse?date='+(dateIso||'')); const data=await r.json(); update(data); }
function update(data){ dateIso=data.date||dateIso; document.getElementById('title').textContent=fmtHeader(dateIso); const T=data.totals||{menus:0,eleves:0,profs:0,sandwiches:0,beverages:0,chocolates:0,amount:0}; const t=document.getElementById('totals'); t.innerHTML=''; ['Menus: '+T.menus+' (élèves '+T.eleves+', profs '+T.profs+')','Sandwiches: '+T.sandwiches,'Boissons: '+T.beverages,'Chocolats: '+T.chocolates,'Total cash: '+chf(T.amount)].forEach((x,i)=>{ const s=document.createElement('span'); s.style.cssText='border:1px solid #ddd;border-radius:999px;padding:4px 8px;background:#fff;'+(i==4?'font-weight:700;':''); s.textContent=x; t.appendChild(s); }); const list=(data.names||[]).slice(0,40); const box=document.getElementById('names'); box.innerHTML=''; for(let c=0;c<4;c++){ const col=document.createElement('div'); for(let i=0;i<10;i++){ const name=list[c*10+i]; const row=document.createElement('div'); row.style.cssText='display:flex;align-items:center;justify-content:space-between;padding:6px 8px;border:1px solid #ddd;border-radius:8px;background:#fff;'; if(name){ const left=document.createElement('div'); left.textContent=name; const btn=document.createElement('button'); btn.textContent='Valider'; btn.onclick=(()=>nm=>()=>openModal(nm))(name)(); row.appendChild(left); row.appendChild(btn); } else { row.style.visibility='hidden'; row.textContent='—'; } col.appendChild(row);} box.appendChild(col);} }
function openModal(name){ const ov=document.createElement('div'); ov.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center'; const b=document.createElement('div'); b.style.cssText='background:#fff;border-radius:10px;padding:16px;min-width:320px'; b.innerHTML=`<h3>${name||'Menu spontané'}</h3><div style='margin:6px 0'><label><input type='radio' name='u' value='ELEVE' checked> Élève (8)</label> <label><input type='radio' name='u' value='PROF'> Prof (12)</label></div><div style='margin:6px 0'><label><input type='radio' name='p' value='CASH' checked> Cash</label> <label><input type='radio' name='p' value='CARD'> Carte abo</label></div><div style='margin:6px 0'><label><input id='bev' type='checkbox'> Boisson +2</label> <label><input id='choc' type='checkbox'> Chocolat +1.5</label></div><div id='nameRow' style='display:${name?'none':'block'}'><input id='nameInput' placeholder='Nom (facultatif)' style='width:100%'></div><div style='text-align:right;margin-top:8px'><button id='cancel'>Annuler</button> <button id='ok'>Valider</button></div>`; ov.appendChild(b); document.body.appendChild(ov); document.getElementById('cancel').onclick=()=>ov.remove(); document.getElementById('ok').onclick=async ()=>{ const t=document.querySelector("input[name='u']:checked").value; const pay=document.querySelector("input[name='p']:checked").value; const bev=document.getElementById('bev').checked; const choc=document.getElementById('choc').checked; const nm=name||document.getElementById('nameInput').value.trim()||'Anonyme'; const r=await fetch('/api/checkout',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:nm,type:t,beverage:bev,chocolate:choc,dateIso,method:pay})}); if(!r.ok){ alert(await r.text()); ov.remove(); return; } const data=await r.json(); toast('Validé ✔'); update(data); ov.remove(); } }
async function qty(kind){ const n=prompt('Quantité ?','1'); if(!n)return; const r=await fetch('/api/add/'+kind,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({qty:parseInt(n||'1'),dateIso})}); update(await r.json()); toast('Ajouté'); }
async function closeDay(){ const ok=confirm('Fermer la caisse et envoyer la comptabilité ?'); if(!ok)return; const r=await fetch('/api/close',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dateIso})}); if((await r.json()).ok){ location.replace('/closed'); } }
window.addEventListener('load',async()=>{ const p=new URLSearchParams(location.search); dateIso=p.get('date')||''; await refresh(); document.getElementById('walkin').onclick=()=>openModal(null); document.getElementById('sand').onclick=()=>qty('sandwich'); document.getElementById('bev').onclick=()=>qty('beverage'); document.getElementById('choc').onclick=()=>qty('chocolate'); document.getElementById('close').onclick=closeDay; });
</script>
</body></html>"""

CLOSED_HTML = """<!DOCTYPE html><html><head><meta charset='utf-8'><title>Caisse — Fermée</title>
<style>body{margin:0;padding:0;font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh}</style></head>
<body><div style='text-align:center'><h2>La caisse est désormais fermée</h2><p>La comptabilité a été transmise.</p></div></body></html>"""

# On écrit les templates à la volée (compatible PyInstaller --add-data)
os.makedirs('templates', exist_ok=True)
open('templates/page.html','w',encoding='utf-8').write(PAGE_HTML)
open('templates/caisse.html','w',encoding='utf-8').write(CAISSE_HTML)
open('templates/closed.html','w',encoding='utf-8').write(CLOSED_HTML)

# ---------------- Pages ----------------
@app.get("/", response_class=HTMLResponse)
def home(req: Request):
    return TEMPLATES.TemplateResponse("page.html", {"request": req})

@app.get("/caisse", response_class=HTMLResponse)
def caisse(req: Request):
    return TEMPLATES.TemplateResponse("caisse.html", {"request": req})

@app.get("/closed", response_class=HTMLResponse)
def closed(req: Request):
    return TEMPLATES.TemplateResponse("closed.html", {"request": req})

# ---------------- API (Sheets) ----------------
class ReserveIn(BaseModel):
    name: str
    dateStr: str  # dd.MM.yyyy

class UnreserveIn(BaseModel):
    name: str
    dateStr: str

class CheckoutIn(BaseModel):
    name: str
    type: str  # ELEVE | PROF
    beverage: bool = False
    chocolate: bool = False
    dateIso: Optional[str] = None
    method: str = "CASH"  # CASH | CARD

class QtyIn(BaseModel):
    qty: int
    dateIso: Optional[str] = None

class CloseIn(BaseModel):
    dateIso: Optional[str] = None

@app.get("/api/initial")
def api_initial():
    today = date.today()
    # Lire Paramètres
    psh = ws("Paramètres")
    pvals = psh.get_all_values()
    rows = []
    for r in pvals[1:]:  # skip header
        if not r or len(r) < 5: continue
        iso, jour, menu, open_str, dis_str = r[:5]
        try:
            d = date.fromisoformat(iso)
        except Exception:
            continue
        rows.append({"date_iso": iso, "jour": jour, "menu": menu, "open": open_str.lower() in ("1","true","vrai","yes"), "disabled": dis_str.lower() in ("1","true","vrai","yes")})
    wanted = ["Lundi","Mardi","Jeudi","Vendredi"]

    def first_open(day_name: str):
        cand = [e for e in rows if e["jour"] == day_name and e["open"] and date.fromisoformat(e["date_iso"]) >= today]
        cand.sort(key=lambda x: x["date_iso"])  # ASC
        if cand:
            e = cand[0]
            y, m, d = map(int, e["date_iso"].split("-"))
            return {"date": f"{d:02d}.{m:02d}.{y:04d}", "jour": day_name, "menu": e["menu"], "open": True, "disabled": e["disabled"]}
        # sinon, prochaine date fictive pour affichage (fermée)
        # calcule prochaine occurrence du weekday
        wd = {"Lundi":0, "Mardi":1, "Mercredi":2, "Jeudi":3, "Vendredi":4, "Samedi":5, "Dimanche":6}[day_name]
        delta = (wd - today.weekday()) % 7 or 7
        fut = date.fromordinal(today.toordinal()+delta)
        return {"date": f"{fut.day:02d}.{fut.month:02d}.{fut.year:04d}", "jour": day_name, "menu": "", "open": False, "disabled": False}

    days_out = [first_open(w) for w in wanted]

    # Réservations map par dd.MM.yyyy
    rsh = ws("Réservations")
    rvals = rsh.get_all_values()[1:]
    reservations: Dict[str, List[str]] = {}
    for row in rvals:
        if len(row) < 2: continue
        iso, name = row[0], (row[1] or "").strip()
        if not iso or not name: continue
        try:
            y, m, d = map(int, iso.split("-"))
        except Exception:
            continue
        key = f"{d:02d}.{m:02d}.{y:04d}"
        reservations.setdefault(key, []).append(name)

    return {"days": days_out, "reservations": reservations}

@app.post("/api/reserve", response_class=PlainTextResponse)
def api_reserve(inp: ReserveIn):
    iso = to_iso_any(inp.dateStr)
    psh = ws("Paramètres")
    # vérifier ouvert
    pvals = psh.get_all_values()[1:]
    is_open = any((r and len(r)>=4 and r[0]==iso and r[3].lower() in ("1","true","vrai","yes")) for r in pvals)
    if not is_open:
        raise HTTPException(400, f"Le {inp.dateStr} est fermé, impossible de réserver.")
    # quota 40
    rsh = ws("Réservations")
    rvals = rsh.get_all_values()[1:]
    count = sum(1 for r in rvals if r and r[0]==iso)
    if count >= MAX_RESAS:
        raise HTTPException(400, f"Quota de 40 atteint pour le {inp.dateStr}.")
    rsh.append_row([iso, inp.name.strip(), datetime.utcnow().isoformat()])
    return f"Merci {inp.name}, réservation confirmée pour le {inp.dateStr} !"

@app.post("/api/unreserve", response_class=PlainTextResponse)
def api_unreserve(inp: UnreserveIn):
    iso = to_iso_any(inp.dateStr)
    name = (inp.name or "").strip()
    rsh = ws("Réservations")
    vals = rsh.get_all_values()
    # chercher la première occurrence et supprimer la ligne
    for i, row in enumerate(vals[1:], start=2):
        if len(row)>=2 and row[0]==iso and (row[1] or '').strip()==name:
            rsh.delete_rows(i)
            return f"Vous êtes désinscrit pour le {inp.dateStr}."
    raise HTTPException(400, f"Pas de réservation trouvée pour \"{name}\" le {inp.dateStr}.")

class Totals(BaseModel):
    menus: int = 0
    eleves: int = 0
    profs: int = 0
    sandwiches: int = 0
    beverages: int = 0
    chocolates: int = 0
    amount: float = 0.0

class CaisseOut(BaseModel):
    date: str
    closed: bool
    names: List[str]
    totals: Totals


def is_closed(iso: str) -> bool:
    csh = ws("Caisse")
    vals = csh.get_all_values()[1:]
    for r in vals:
        if len(r)>=3 and r[0]==iso and (r[2] or '')=='Closed':
            return True
    return False


def build_totals(iso: str):
    csh = ws("Caisse")
    vals = csh.get_all_values()[1:]
    t = Totals()
    paidCount: Dict[str,int] = {}
    for r in vals:
        if len(r) < 7 or r[0] != iso: 
            continue
        typ = r[2]
        base = float(r[3] or 0)
        bev  = float(r[4] or 0)
        choc = float(r[5] or 0)
        tot  = float(r[6] or 0)
        if typ == 'Closed':
            continue
        elif typ == 'Sandwich':
            t.sandwiches += 1
        elif typ == 'Boisson':
            t.beverages += 1
        elif typ == 'Chocolat':
            t.chocolates += 1
        else:
            t.menus += 1
            if 'eleve' in typ.lower(): t.eleves += 1
            else: t.profs += 1
            nm = norm_name(r[1] or '')
            if nm: paidCount[nm] = paidCount.get(nm,0) + 1
            if bev > 0: t.beverages += 1
            if choc > 0: t.chocolates += 1
        t.amount += tot
    return t, paidCount

@app.get("/api/caisse")
def api_caisse(date: Optional[str] = None):
    iso = date if (date and len(date)==10) else today_iso()
    if is_closed(iso):
        t,_ = build_totals(iso)
        return CaisseOut(date=iso, closed=True, names=[], totals=t)
    # ordre d'inscription : selon l'ordre dans la feuille (append)
    rsh = ws("Réservations")
    rows = rsh.get_all_values()[1:]
    ordered = [(r[1] or '').strip() for r in rows if len(r)>=2 and r[0]==iso and (r[1] or '').strip()]
    t, paidCount = build_totals(iso)
    # retirer ceux déjà validés
    remaining: List[str] = []
    paid_left = dict(paidCount)
    for name in ordered:
        k = norm_name(name)
        if paid_left.get(k,0)>0:
            paid_left[k]-=1
        else:
            remaining.append(name)
    return CaisseOut(date=iso, closed=False, names=remaining, totals=t)


def assert_open(iso: str):
    if is_closed(iso):
        raise HTTPException(400, f"Caisse fermée pour {iso}.")

def assert_capacity(iso: str):
    t,_ = build_totals(iso)
    if t.menus >= MAX_MENUS:
        raise HTTPException(400, f"Limite de 45 menus servis atteinte pour {pretty_fr_header(iso)}.")

@app.post("/api/checkout")
def api_checkout(inp: CheckoutIn):
    iso = inp.dateIso or today_iso()
    assert_open(iso)
    assert_capacity(iso)
    typ = (inp.type or 'PROF').upper()
    method = (inp.method or 'CASH').upper()
    base = PRICES['ELEVE'] if typ=='ELEVE' else PRICES['PROF']
    bev  = PRICES['BOISSON'] if inp.beverage else 0.0
    choc = PRICES['CHOCOLAT'] if inp.chocolate else 0.0
    total_cash = (bev+choc) if method=='CARD' else (base+bev+choc)
    label = ('Eleve' if typ=='ELEVE' else 'Prof') + (' (CARD)' if method=='CARD' else ' (CASH)')
    csh = ws('Caisse')
    csh.append_row([iso, inp.name.strip() or 'Anonyme', label, base, bev, choc, total_cash, datetime.utcnow().isoformat()])
    return api_caisse(iso)

@app.post("/api/add/sandwich")
def api_add_sandwich(inp: QtyIn):
    iso = inp.dateIso or today_iso(); assert_open(iso)
    n = max(1, int(inp.qty or 1))
    csh = ws('Caisse')
    rows = [[iso, '', 'Sandwich', PRICES['SANDWICH'], 0, 0, PRICES['SANDWICH'], datetime.utcnow().isoformat()] for _ in range(n)]
    csh.append_rows(rows)
    return api_caisse(iso)

@app.post("/api/add/beverage")
def api_add_beverage(inp: QtyIn):
    iso = inp.dateIso or today_iso(); assert_open(iso)
    n = max(1, int(inp.qty or 1))
    csh = ws('Caisse')
    rows = [[iso, '', 'Boisson', 0, PRICES['BOISSON'], 0, PRICES['BOISSON'], datetime.utcnow().isoformat()] for _ in range(n)]
    csh.append_rows(rows)
    return api_caisse(iso)

@app.post("/api/add/chocolate")
def api_add_chocolate(inp: QtyIn):
    iso = inp.dateIso or today_iso(); assert_open(iso)
    n = max(1, int(inp.qty or 1))
    csh = ws('Caisse')
    rows = [[iso, '', 'Chocolat', 0, 0, PRICES['CHOCOLAT'], PRICES['CHOCOLAT'], datetime.utcnow().isoformat()] for _ in range(n)]
    csh.append_rows(rows)
    return api_caisse(iso)

@app.post("/api/close")
def api_close(inp: CloseIn):
    iso = inp.dateIso or today_iso()
    # envoyer mail (facultatif)
    try:
        smtp_to = os.environ.get('SMTP_TO')
        if smtp_to:
            t,_ = build_totals(iso)
            send_summary_mail(iso, t)
    except Exception:
        pass
    # marquer fermeture
    csh = ws('Caisse')
    csh.append_row([iso, '', 'Closed', 0, 0, 0, 0, datetime.utcnow().isoformat()])
    return {"ok": True}

# ---------------- E-mail (optionnel) ----------------
def send_summary_mail(iso: str, t: Totals):
    import smtplib
    from email.mime.text import MIMEText
    host = os.environ.get('SMTP_HOST','localhost'); port = int(os.environ.get('SMTP_PORT','25'))
    user = os.environ.get('SMTP_USER'); pwd = os.environ.get('SMTP_PASS')
    frm = os.environ.get('SMTP_FROM','cafeteria@local'); to = os.environ.get('SMTP_TO')
    body = "\n".join([
        f"Date : {pretty_fr_header(iso)}",
        "",
        f"Menus : {t.menus} (élèves {t.eleves}, profs {t.profs})",
        f"Sandwiches : {t.sandwiches}",
        f"Boissons : {t.beverages}",
        f"Chocolats : {t.chocolates}",
        "",
        f"Fond de caisse initial : {CASH_FLOAT:.2f} CHF",
        f"Encaissements cash : {t.amount:.2f} CHF",
        f"Total en caisse attendu : {(CASH_FLOAT + t.amount):.2f} CHF",
        "",
        f"Lien caisse : http://{os.environ.get('PUBLIC_HOST', 'localhost')}:{PORT}/caisse?date={iso}",
    ])
    msg = MIMEText(body)
    msg['Subject'] = f"Comptabilité cafétéria — {pretty_fr_header(iso)}"
    msg['From'] = frm; msg['To'] = to
    with smtplib.SMTP(host, port) as s:
        if os.environ.get('SMTP_STARTTLS'): s.starttls()
        if user and pwd: s.login(user, pwd)
        s.send_message(msg)

# ---------------- Dev main ----------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app_sheets:app", host=HOST, port=PORT, reload=False)
