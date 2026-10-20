from flask import Flask, render_template_string, request, redirect, url_for, send_file, flash, session, get_flashed_messages
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from io import BytesIO
from threading import Lock
import pandas as pd
import json

app = Flask(__name__)
app.config["SECRET_KEY"] = "troque-por-uma-chave-secreta"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///os.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

FORMATO_DATA = "%d/%m/%Y %H:%M:%S"
PREFIXO_ANO_OS = "26"
NUMERO_OS_INICIAL = 1770
os_lock = Lock()
estoque_lock = Lock()


class OrdemServico(db.Model):
    __tablename__ = "ordens_servico"
    id = db.Column(db.Integer, primary_key=True)
    numero_os = db.Column(db.String(20), unique=True, nullable=False)
    solicitante = db.Column(db.String(100), nullable=False)
    setor = db.Column(db.String(100), default="")
    titulo = db.Column(db.String(200), default="")
    descricao = db.Column(db.Text, default="")
    prioridade = db.Column(db.String(30), default="Média")
    responsavel = db.Column(db.String(100), default="")
    abertura = db.Column(db.String(50), nullable=False)
    finalizacao = db.Column(db.String(50), default="")
    status = db.Column(db.String(50), default="Aberta")
    tempo_total = db.Column(db.String(50), default="")
    tipo_manutencao = db.Column(db.String(30), default="")
    funcionarios = db.Column(db.String(200), default="")
    horas_trabalhadas = db.Column(db.Float, default=0.0)


class ItemEstoque(db.Model):
    __tablename__ = "itens_estoque"
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(50), unique=True, nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    categoria = db.Column(db.String(100), default="Geral")
    localizacao = db.Column(db.String(100), default="")
    quantidade = db.Column(db.Integer, nullable=False, default=0)
    estoque_minimo = db.Column(db.Integer, nullable=False, default=2)
    ultima_atualizacao = db.Column(db.String(50), nullable=False)


class HistoricoMovimentacao(db.Model):
    __tablename__ = "historico_movimentacoes"
    id = db.Column(db.Integer, primary_key=True)
    peca_codigo = db.Column(db.String(50), nullable=False)
    peca_nome = db.Column(db.String(150), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)
    quantidade = db.Column(db.Integer, nullable=False)
    solicitante = db.Column(db.String(100), default="")
    data_hora = db.Column(db.String(50), nullable=False)


class MaquinaSpare(db.Model):
    __tablename__ = "maquinas_spare"
    id = db.Column(db.Integer, primary_key=True)
    maquina = db.Column(db.String(150), nullable=False)
    modelo = db.Column(db.String(100), nullable=False)
    setor = db.Column(db.String(100), default="")
    pecas_necessarias = db.Column(db.Text, default="")
    pecas = db.relationship("PecaSpare", backref="maquina_spare", cascade="all, delete-orphan")


class PecaSpare(db.Model):
    __tablename__ = "pecas_spare"
    id = db.Column(db.Integer, primary_key=True)
    maquina_spare_id = db.Column(db.Integer, db.ForeignKey("maquinas_spare.id"), nullable=False)
    nome_peca = db.Column(db.String(150), nullable=False)
    codigo_peca = db.Column(db.String(50), default="")
    quantidade = db.Column(db.Integer, default=1)
    observacao = db.Column(db.String(200), default="")


with app.app_context():
    db.create_all()
    inspector = db.inspect(db.engine)
    colunas_os = [c['name'] for c in inspector.get_columns('ordens_servico')]
    with db.engine.connect() as conn:
        if 'tipo_manutencao' not in colunas_os:
            conn.execute(db.text("ALTER TABLE ordens_servico ADD COLUMN tipo_manutencao VARCHAR(30) DEFAULT ''"))
        if 'funcionarios' not in colunas_os:
            conn.execute(db.text("ALTER TABLE ordens_servico ADD COLUMN funcionarios VARCHAR(200) DEFAULT ''"))
        if 'horas_trabalhadas' not in colunas_os:
            conn.execute(db.text("ALTER TABLE ordens_servico ADD COLUMN horas_trabalhadas FLOAT DEFAULT 0.0"))
            
        colunas_spare = [c['name'] for c in inspector.get_columns('maquinas_spare')]
        if 'pecas_necessarias' not in colunas_spare:
            conn.execute(db.text("ALTER TABLE maquinas_spare ADD COLUMN pecas_necessarias TEXT DEFAULT ''"))
        conn.commit()


def agora_formatado():
    return datetime.now().strftime(FORMATO_DATA)


def gerar_numero_os():
    numeros = OrdemServico.query.with_entities(OrdemServico.numero_os).all()
    maior = NUMERO_OS_INICIAL - 1
    for (numero,) in numeros:
        try:
            maior = max(maior, int(numero.split("-")[0]))
        except (ValueError, AttributeError, IndexError):
            pass
    return f"{maior + 1}-{PREFIXO_ANO_OS}"


def calcular_tempo_total(abertura, finalizacao):
    inicio = datetime.strptime(abertura, FORMATO_DATA)
    fim = datetime.strptime(finalizacao, FORMATO_DATA)
    segundos = max(0, int((fim - inicio).total_seconds()))
    dias, resto = divmod(segundos, 86400)
    horas, resto = divmod(resto, 3600)
    minutos, _ = divmod(resto, 60)
    partes = []
    if dias:
        partes.append(f"{dias}d")
    if horas:
        partes.append(f"{horas}h")
    partes.append(f"{minutos}min")
    return " ".join(partes)


def tempo_hhmm_para_horas_decimais(hhmm_str):
    if not hhmm_str or ":" not in hhmm_str:
        return 0.0
    try:
        partes = hhmm_str.split(":")
        h = int(partes[0])
        m = int(partes[1])
        return round(h + (m / 60.0), 2)
    except (ValueError, IndexError):
        return 0.0


def horas_decimais_para_hhmm(horas_float):
    if not horas_float or horas_float <= 0:
        return "0h 00min"
    h = int(horas_float)
    m = int(round((horas_float - h) * 60))
    if m == 60:
        h += 1
        m = 0
    return f"{h}h {m:02d}min"


def renderizar(template, **contexto):
    return render_template_string(
        template, css=CSS_BASE, js=JS_BASE,
        flash_msgs=get_flashed_messages(), **contexto
    )


CSS_BASE = r"""
:root{--bg:#070707;--s1:#111;--s2:#171717;--bd:#2b2b2b;--tx:#f5f5f5;--mt:#a3a3a3;--or:#f97316;--or2:#fb923c;--er:#ef4444;--ok:#22c55e;--grad:linear-gradient(135deg,#000 0%,#7c2d12 55%,#f97316 100%);--btn:linear-gradient(135deg,#ea580c,#f97316 60%,#fb923c);--r:16px}
*{box-sizing:border-box}
html{scroll-behavior:smooth}body{margin:0;padding:24px;min-height:100vh;font-family:"Segoe UI",Arial,sans-serif;color:var(--tx);background:radial-gradient(900px 500px at 85% -10%,rgba(249,115,22,.25),transparent 60%),var(--bg);background-attachment:fixed}
.container{max-width:1600px;margin:auto;animation:fadeIn .5s ease-in-out}.muted{color:var(--mt)}
.cabecalho{background:var(--grad);padding:28px;border-radius:var(--r);margin-bottom:24px;border:1px solid #7c2d1240;box-shadow:0 15px 45px #f9731625;display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:16px;position:relative;overflow:hidden}
.cabecalho::after{content:"";position:absolute;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle,rgba(255,255,255,0.08) 0%,transparent 70%);pointer-events:none;transform:rotate(30deg)}
.cabecalho h1{margin:0 0 8px;font-size:26px;letter-spacing:0.5px}.cabecalho p{margin:0;color:#fed7aa}.cabecalho-lado{display:flex;flex-direction:column;align-items:flex-end;gap:10px}.relogio{background:#0008;padding:8px 14px;border-radius:99px;font-size:13px;border:1px solid #ffffff15;box-shadow:0 4px 12px rgba(0,0,0,0.3)}
.nav-abas{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap}.aba,.botao-sair{display:inline-block;padding:10px 18px;background:#0006;border:1px solid #ffffff25;color:white;border-radius:10px;text-decoration:none;font-size:14px;font-weight:bold;transition:all .3s cubic-bezier(0.4,0,0.2,1)}.aba:hover,.botao-sair:hover{transform:translateY(-3px);border-color:var(--or);box-shadow:0 6px 20px rgba(249,115,22,0.3)}.aba.ativa{background:var(--btn);box-shadow:0 4px 15px rgba(249,115,22,0.4)}.botao-sair{border-color:#ef444499;color:#fecaca}
.resumo{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:18px;margin-bottom:24px}.resumo-card,.card{background:linear-gradient(170deg,var(--s2),var(--s1));border:1px solid var(--bd);border-radius:var(--r);padding:22px;margin-bottom:24px;box-shadow:0 8px 30px rgba(0,0,0,0.3);transition:all .3s cubic-bezier(0.4,0,0.2,1)}.resumo-card:hover{transform:translateY(-4px);border-color:var(--or);box-shadow:0 12px 40px rgba(249,115,22,0.25)}
.resumo-card{position:relative;cursor:default}.resumo-card[data-atalho]{cursor:pointer}.resumo-card span{display:block;color:var(--mt);font-size:13px;margin-bottom:8px;text-transform:uppercase;letter-spacing:0.5px}.resumo-card strong{font-size:32px;color:var(--or2);text-shadow:0 2px 10px rgba(249,115,22,0.2)}.resumo-card small{display:block;margin-top:6px;color:var(--mt)}.icone{position:absolute;right:18px;top:16px;font-size:28px;opacity:.6;transition:transform .3s}.resumo-card:hover .icone{transform:scale(1.2) rotate(10deg);opacity:1}.progresso{height:8px;background:#262626;border-radius:99px;margin-top:12px;overflow:hidden;box-shadow:inset 0 1px 3px rgba(0,0,0,0.5)}.progresso span{display:block;height:100%;background:var(--btn);transition:width 1s cubic-bezier(0.4,0,0.2,1)}
.card h2{margin:0 0 20px;font-size:20px;display:flex;gap:12px;align-items:center}.card h2:before{content:"";width:6px;height:24px;border-radius:4px;background:var(--btn);box-shadow:0 0 12px var(--or)}details.card>summary{list-style:none;cursor:pointer;font-size:20px;font-weight:bold;transition:color .2s}details.card>summary:hover{color:var(--or2)}details.card>summary::-webkit-details-marker{display:none}details.card>summary:after{content:"▾";float:right;color:var(--or2);transition:transform .3s}details.card[open]>summary:after{transform:rotate(180deg)}details.card>.conteudo{margin-top:20px;animation:fadeIn .4s ease-in-out}
.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px 20px}.form-grid.tres{grid-template-columns:repeat(3,minmax(0,1fr))}.campo{min-width:0}.campo.completo{grid-column:1/-1}label{display:block;font-weight:bold;font-size:13px;margin-bottom:8px;color:#d4d4d4}
input,textarea,select{display:block;width:100%;padding:12px 14px;border:1px solid var(--bd);border-radius:10px;font:inherit;background:#0d0d0d;color:var(--tx);transition:all .3s}input:focus,textarea:focus,select:focus{outline:none;border-color:var(--or);box-shadow:0 0 0 4px rgba(249,115,22,0.25);background:#121212}input[readonly]{color:var(--or2);font-weight:bold;background:#141414}textarea{min-height:100px;resize:vertical}.seg{display:flex;gap:10px;flex-wrap:wrap}.seg input{display:none}.seg label{margin:0;flex:1;min-width:80px;text-align:center;padding:12px;border:1px solid var(--bd);border-radius:10px;background:#0d0d0d;color:var(--mt);cursor:pointer;transition:all .3s}.seg input:checked+label{background:var(--btn);color:white;border-color:var(--or);box-shadow:0 4px 15px rgba(249,115,22,0.3);transform:translateY(-2px)}
button,.botao{display:inline-block;padding:11px 18px;border:0;border-radius:10px;font-size:13px;font-weight:bold;font-family:inherit;text-decoration:none;text-align:center;cursor:pointer;color:white;transition:all .3s cubic-bezier(0.4,0,0.2,1);box-shadow:0 4px 12px rgba(0,0,0,0.2)}.salvar,.pesquisar,.finalizar,.adicionar{background:var(--btn);box-shadow:0 4px 16px rgba(249,115,22,0.3)}.excel{background:linear-gradient(135deg,#065f46,#10b981);box-shadow:0 4px 16px rgba(16,185,129,0.3)}.limpar{background:#262626;color:#ddd;border:1px solid var(--bd)}.retirar{background:linear-gradient(135deg,#92400e,#f59e0b);box-shadow:0 4px 16px rgba(245,158,11,0.3)}.excluir{background:linear-gradient(135deg,#7f1d1d,#ef4444);box-shadow:0 4px 16px rgba(239,68,68,0.3)}.editar-peca{background:linear-gradient(135deg,#374151,#525252);border:1px solid #737373}.movimentar{background:var(--btn)}button:hover,.botao:hover{transform:translateY(-3px);filter:brightness(1.1);box-shadow:0 8px 25px rgba(0,0,0,0.4)}button:active,.botao:active{transform:translateY(-1px)}
.acoes-topo{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:16px}.busca{display:flex;gap:10px;flex:1;min-width:280px}.campo-busca{position:relative;flex:1}.campo-busca input{padding-left:40px}.campo-busca:before{content:"🔍";position:absolute;left:14px;top:50%;transform:translateY(-50%)}
.filtros{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin-top:18px}.rotulo{font-size:12px;text-transform:uppercase;color:var(--mt);letter-spacing:0.5px}
.chip{padding:8px 16px;border-radius:99px;background:#0d0d0d;border:1px solid var(--bd);color:var(--mt);transition:all .3s}.chip:hover{border-color:var(--or);color:var(--tx);transform:translateY(-2px)}.chip.ativo{background:var(--btn);color:white;border-color:var(--or);box-shadow:0 4px 15px rgba(249,115,22,0.3)}
.filtros select{width:auto;padding:8px 14px;border-radius:99px;font-size:13px}.contagem{margin-left:auto;font-size:13px;color:var(--mt)}.contagem strong{color:var(--or2)}
.tabela-wrapper{width:100%;overflow-x:auto;border-radius:12px;border:1px solid var(--bd);box-shadow:0 4px 20px rgba(0,0,0,0.4)}table{width:100%;min-width:1000px;border-collapse:collapse;background:var(--s1)}th,td{padding:14px 12px;border-bottom:1px solid #222;text-align:left;font-size:13px;vertical-align:middle}th{background:#0d0d0d;color:var(--or2);white-space:nowrap;text-transform:uppercase;font-size:11px;letter-spacing:.8px}th.ordenavel{cursor:pointer;transition:color .2s}th.ordenavel:hover{color:white}tbody tr.linha{transition:background .2s}tbody tr.linha:hover{background:rgba(249,115,22,0.08)!important}tbody tr.clicavel{cursor:pointer}tr.detalhe{display:none}tr.detalhe td{background:#0d0d0d;padding:18px 24px;border-left:4px solid var(--or);animation:fadeIn .3s ease-in-out}tr.detalhe p{white-space:pre-wrap}.vazio{text-align:center;padding:35px!important;color:var(--mt)}tr.sem-resultado{display:none}
.status{display:inline-block;padding:6px 12px;border-radius:20px;font-size:12px;font-weight:bold;white-space:nowrap;box-shadow:0 2px 8px rgba(0,0,0,0.2)}.aberta{background:rgba(250,204,21,0.15);color:#fde047;border:1px solid rgba(250,204,21,0.3)}.finalizada,.estoque-ok{background:rgba(34,197,94,0.15);color:#4ade80;border:1px solid rgba(34,197,94,0.3)}.estoque-baixo{background:rgba(239,68,68,0.2);color:#f87171;border:1px solid rgba(239,68,68,0.3)}
.prioridade{font-weight:bold}.prioridade-critica{color:#f87171;text-shadow:0 0 10px rgba(239,68,68,0.3)}.prioridade-alta{color:#fb923c}.prioridade-media{color:#facc15}.prioridade-baixa{color:#4ade80}
.tipo-badge{display:inline-block;padding:4px 10px;border-radius:6px;font-size:11px;font-weight:bold}
.tipo-preventiva{background:rgba(59,130,246,0.15);color:#60a5fa;border:1px solid rgba(59,130,246,0.3)}
.tipo-corretiva{background:rgba(249,115,22,0.15);color:#fb923c;border:1px solid rgba(249,115,22,0.3)}
.acoes{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.acoes form{margin:0}.acoes button{padding:8px 12px;font-size:12px}.nivel{min-width:120px}.barra{height:7px;background:#262626;border-radius:99px;margin-top:8px;overflow:hidden;box-shadow:inset 0 1px 3px rgba(0,0,0,0.5)}.barra span{display:block;height:100%;background:linear-gradient(90deg,#16a34a,#4ade80);transition:width .6s ease}.barra.baixo span{background:linear-gradient(90deg,#b91c1c,#f87171)}
.rodape{text-align:center;color:#666;font-size:12px;padding:20px}
.toasts{position:fixed;top:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:12px;max-width:380px}
.toast{background:#141414;border:1px solid var(--bd);border-left:5px solid var(--or);border-radius:12px;padding:16px 40px 16px 18px;font-size:14px;box-shadow:0 15px 40px rgba(0,0,0,0.6);position:relative;animation:slideInRight .4s cubic-bezier(0.175,0.885,0.32,1.275)}
.toast.erro{border-left-color:var(--er)}
.toast .x{position:absolute;right:8px;top:6px;background:none;color:var(--mt);padding:4px 8px;font-size:16px;cursor:pointer;transition:color .2s}
.toast .x:hover{color:white}
.overlay{display:none;position:fixed;inset:0;z-index:9000;background:rgba(0,0,0,0.75);backdrop-filter:blur(6px);align-items:center;justify-content:center;padding:16px;animation:fadeIn .3s ease-in-out}
.overlay.aberto{display:flex}
.modal{width:100%;max-width:480px;background:linear-gradient(170deg,#1b1b1b,#0e0e0e);border:1px solid rgba(249,115,22,0.4);border-radius:18px;padding:26px;box-shadow:0 25px 70px rgba(0,0,0,0.8);animation:scaleUp .3s cubic-bezier(0.175,0.885,0.32,1.275)}
.modal h3{margin:0 0 8px;font-size:22px;color:var(--or2)}.modal p{margin:0 0 18px}.modal .campo{margin-top:16px}.modal-acoes{display:flex;gap:12px;justify-content:flex-end;margin-top:22px}
.stepper{display:flex;gap:10px}.stepper button{width:46px;padding:0;font-size:22px;background:#262626}.stepper input{text-align:center;font-size:18px;font-weight:bold}
.saldo{margin-top:14px!important;padding:12px;background:#0d0d0d;border:1px dashed var(--bd);border-radius:10px;color:var(--mt)}.saldo strong{color:var(--or2)}
.checkbox-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:8px}
.checkbox-grid label{display:flex;align-items:center;gap:8px;font-weight:normal;color:var(--tx);cursor:pointer;background:#0d0d0d;padding:10px;border:1px solid var(--bd);border-radius:8px;transition:all .2s}
.checkbox-grid label:hover{border-color:var(--or)}
.checkbox-grid input[type="checkbox"]{width:auto;accent-color:var(--or);cursor:pointer}
input[type="time"]::-webkit-calendar-picker-indicator{filter:invert(1)}
@keyframes fadeIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
@keyframes slideInRight{from{opacity:0;transform:translateX(50px)}to{opacity:1;transform:translateX(0)}}
@keyframes scaleUp{from{opacity:0;transform:scale(0.9)}to{opacity:1;transform:scale(1)}}
@media(max-width:800px){body{padding:12px}.resumo{grid-template-columns:repeat(2,minmax(0,1fr))}.form-grid,.form-grid.tres{grid-template-columns:1fr}.campo.completo{grid-column:auto}.card{padding:18px}.busca{min-width:100%}.cabecalho-lado{align-items:flex-start}.contagem{margin-left:0;width:100%}.toasts{left:12px;right:12px;max-width:none}}
"""

JS_BASE = r"""
(function(){
const $=(s,r)=>(r||document).querySelector(s), $$=(s,r)=>Array.from((r||document).querySelectorAll(s));
const norm=t=>(t||'').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'');
const caixa=document.createElement('div');caixa.className='toasts';document.body.appendChild(caixa);
window.toast=function(msg,tipo){const t=document.createElement('div');t.className='toast '+(tipo||'ok');const s=document.createElement('span');s.textContent=msg;const x=document.createElement('button');x.type='button';x.className='x';x.textContent='×';x.onclick=()=>t.remove();t.append(s,x);caixa.appendChild(t);setTimeout(()=>t.remove(),5000)};
(window.__FLASH__||[]).forEach((m,i)=>setTimeout(()=>window.toast(m,/erro|não|nao|inválid|insuficiente|incorret|obrigat/i.test(m)?'erro':'ok'),i*250));
window.confirmar=function(msg,rotulo,perigo){return new Promise(resolve=>{const ov=document.createElement('div');ov.className='overlay aberto';const box=document.createElement('div');box.className='modal';const h=document.createElement('h3');h.textContent='Confirmar ação';const p=document.createElement('p');p.textContent=msg;const a=document.createElement('div');a.className='modal-acoes';const n=document.createElement('button');n.className='limpar';n.textContent='Cancelar';const y=document.createElement('button');y.className=perigo?'excluir':'salvar';y.textContent=rotulo||'Confirmar';a.append(n,y);box.append(h,p,a);ov.append(box);document.body.append(ov);const end=v=>{ov.remove();resolve(v)};n.onclick=()=>end(false);y.onclick=()=>end(true);ov.onclick=e=>{if(e.target===ov)end(false)};document.addEventListener('keydown',function esc(e){if(e.key==='Escape'){document.removeEventListener('keydown',esc);end(false)}})})};
document.addEventListener('submit',e=>{const f=e.target;if(!f.dataset||!f.dataset.confirm||f.dataset.ok)return;e.preventDefault();window.confirmar(f.dataset.confirm,f.dataset.confirmBtn,f.dataset.perigo==='1').then(ok=>{if(ok){f.dataset.ok='1';f.submit()}})});
const rel=$('#relogio');if(rel){const tic=()=>rel.textContent=new Date().toLocaleString('pt-BR',{weekday:'short',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'});tic();setInterval(tic,1000)}
$$('[data-count]').forEach(el=>{const alvo=parseInt(el.dataset.count,10)||0;el.textContent=alvo});
$$('textarea[data-max]').forEach(ta=>{const c=document.createElement('div');c.style.cssText='text-align:right;color:#a3a3a3;font-size:11px';ta.insertAdjacentElement('afterend',c);ta.maxLength=+ta.dataset.max;const up=()=>c.textContent=ta.value.length+' / '+ta.dataset.max;ta.addEventListener('input',up);up()});
function initTabela(){const tabela=$('#tabela');if(!tabela)return;const tbody=tabela.tBodies[0],linhas=$$('tr.linha',tbody),vazio=$('tr.sem-resultado',tbody),contador=$('#contador');const estado={q:'',f:{}};linhas.forEach(tr=>{tr._det=tr.nextElementSibling&&tr.nextElementSibling.classList.contains('detalhe')?tr.nextElementSibling:null;tr._txt=norm(tr.textContent+' '+(tr._det?tr._det.textContent:''))});
function aplicar(){let n=0;linhas.forEach(tr=>{let ok=!estado.q||tr._txt.includes(estado.q);Object.keys(estado.f).forEach(k=>{if(estado.f[k]&&tr.dataset[k]!==estado.f[k])ok=false});tr.style.display=ok?'':'none';if(tr._det)tr._det.style.display=ok&&tr._det.classList.contains('aberto')?'table-row':'none';if(ok)n++});if(vazio)vazio.style.display=n===0&&linhas.length?'table-row':'none';if(contador)contador.innerHTML='<strong>'+n+'</strong> de '+linhas.length+' registros'}
if(tabela.classList.contains('detalhavel'))linhas.forEach(tr=>{if(!tr._det)return;tr.classList.add('clicavel');tr.addEventListener('click',e=>{if(e.target.closest('button,a,form,input,select'))return;tr._det.classList.toggle('aberto');tr._det.style.display=tr._det.classList.contains('aberto')?'table-row':'none'})});
$$('th[data-tipo]',tabela).forEach(th=>{th.classList.add('ordenavel');th.addEventListener('click',()=>{const asc=th.dataset.dir!=='asc';$$('th[data-tipo]',tabela).forEach(x=>delete x.dataset.dir);th.dataset.dir=asc?'asc':'desc';const i=th.cellIndex,tipo=th.dataset.tipo;const val=tr=>{const t=tr.cells[i].textContent.trim();if(!t||t==='-')return null;if(tipo==='num'){const n=parseFloat(t);return isNaN(n)?null:n}if(tipo==='data'){const m=t.match(/(\d+)\/(\d+)\/(\d+)\s+(\d+):(\d+):(\d+)/);return m?new Date(m[3],m[2]-1,m[1],m[4],m[5],m[6]).getTime():null}return norm(t)};linhas.sort((a,b)=>{const x=val(a),y=val(b);if(x===null)return 1;if(y===null)return -1;const c=typeof x==='number'?x-y:x.localeCompare(y,'pt-BR',{numeric:true});return asc?c:-c}).forEach(tr=>{tbody.appendChild(tr);if(tr._det)tbody.appendChild(tr._det)});aplicar()})});
aplicar();const q=$('#q');if(q){q.addEventListener('input',()=>{estado.q=norm(q.value.trim());aplicar()});if(q.value){estado.q=norm(q.value);aplicar()}document.addEventListener('keydown',e=>{if(e.key==='/'&&!['input','textarea','select'].includes((e.target.tagName||'').toLowerCase())){e.preventDefault();q.focus()}})}
$$('[data-filtro]').forEach(el=>{if(el.tagName==='SELECT')el.addEventListener('change',()=>{estado.f[el.dataset.filtro]=el.value;aplicar()});else el.addEventListener('click',()=>{$$('[data-filtro="'+el.dataset.filtro+'"]').forEach(x=>x.classList.remove('ativo'));el.classList.add('ativo');estado.f[el.dataset.filtro]=el.dataset.valor;aplicar()})});
$$('[data-atalho]').forEach(c=>c.addEventListener('click',()=>{const a=$(c.dataset.atalho);if(a){a.click();$('#secao-tabela')?.scrollIntoView({behavior:'smooth'})}}))}
initTabela();

const modalFinalizar = $('#modalFinalizar');
if(modalFinalizar){
    const formFinalizar = $('#formFinalizar');
    $$('.btn-abrir-finalizar').forEach(btn => {
        btn.addEventListener('click', () => {
            formFinalizar.action = btn.dataset.url;
            $('#osNumeroFinalizar').textContent = btn.dataset.numero;
            $$('input[name="funcionarios"]', formFinalizar).forEach(cb => cb.checked = false);
            $('#tempoTrabalhado').value = '01:00';
            modalFinalizar.classList.add('aberto');
        });
    });
    const fecharFin = () => modalFinalizar.classList.remove('aberto');
    $$('[data-fechar-fin]', modalFinalizar).forEach(b => b.addEventListener('click', fecharFin));
    modalFinalizar.addEventListener('mousedown', e => { if(e.target === modalFinalizar) fecharFin(); });
    document.addEventListener('keydown', e => { if(e.key === 'Escape') fecharFin(); });
}
})();
"""

HTML = r"""
<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>CENTRAL O.S JAMAICA</title><style>{{css|safe}}</style></head><body><div class="container">
<header class="cabecalho"><div><h1>CENTRAL DE ORDENS DE SERVIÇOS DE MANUTENÇÃO JAMAICA</h1><p>Gerenciamento, acompanhamento e controle de O.S.</p>
<div class="nav-abas">
    <a href="{{url_for('dashboard')}}" class="aba ativa">📋 Ordens de Serviço</a>
    <a href="{{url_for('dashboard_estoque')}}" class="aba">📦 Controle de Estoque</a>
    <a href="{{url_for('dashboard_spare')}}" class="aba">🔧 Spare Parts</a>
    <a href="{{url_for('indicadores')}}" class="aba">📊 Indicadores & Gráficos</a>
    <a href="{{url_for('equipe_eficiencia')}}" class="aba">👥 Equipe & Eficiência</a>
</div></div><div class="cabecalho-lado"><span class="relogio" id="relogio"></span></div></header>
<section class="resumo">
<div class="resumo-card" data-atalho='[data-filtro="status"][data-valor=""]'><span class="icone">📋</span><span>Total de O.S.</span><strong data-count="{{total}}">{{total}}</strong><div class="progresso"><span style="width:{{pct_final}}%"></span></div><small>{{pct_final}}% concluídas</small></div>
<div class="resumo-card" data-atalho='[data-filtro="status"][data-valor="aberta"]'><span class="icone">🟡</span><span>O.S. abertas</span><strong data-count="{{abertas}}">{{abertas}}</strong></div>
<div class="resumo-card" data-atalho='[data-filtro="status"][data-valor="finalizada"]'><span class="icone">✅</span><span>O.S. finalizadas</span><strong data-count="{{finalizadas}}">{{finalizadas}}</strong></div>
<div class="resumo-card"><span class="icone">🔖</span><span>Próxima O.S.</span><strong style="font-size:24px">{{proxima}}</strong></div></section>
<details class="card" open><summary>Abrir nova Ordem de Serviço</summary><div class="conteudo"><form action="{{url_for('criar')}}" method="POST"><div class="form-grid">
<div class="campo"><label>Número da O.S.</label><input value="{{proxima}}" readonly></div><div class="campo"><label>Solicitante *</label><input name="solicitante" maxlength="100" required placeholder="Nome do solicitante"></div>
<div class="campo"><label>Setor</label><input name="setor" maxlength="100" placeholder="Setor solicitante"></div><div class="campo"><label>Causa</label><input name="titulo" maxlength="200" placeholder="Resumo do problema"></div>
<div class="campo"><label>Prioridade</label><div class="seg"><input type="radio" name="prioridade" id="p1" value="Baixa"><label for="p1">Baixa</label><input type="radio" name="prioridade" id="p2" value="Média" checked><label for="p2">Média</label><input type="radio" name="prioridade" id="p3" value="Alta"><label for="p3">Alta</label><input type="radio" name="prioridade" id="p4" value="Crítica"><label for="p4">Crítica</label></div></div><div class="campo"><label>Responsável</label><input name="responsavel" maxlength="100" placeholder="Responsável pelo atendimento"></div>
<div class="campo completo"><label>Descrição</label><textarea name="descricao" data-max="1000" placeholder="Descreva o serviço solicitado"></textarea></div><div class="campo completo"><button class="salvar" type="submit">+ Abrir O.S.</button></div></div></form></div></details>
<section class="card" id="secao-tabela"><div class="acoes-topo"><form class="busca" action="{{url_for('dashboard')}}" method="GET"><div class="campo-busca"><input id="q" name="busca" value="{{busca}}" autocomplete="off" placeholder="Pesquisar por número, solicitante, setor, causa..."></div><a class="botao limpar" href="{{url_for('dashboard')}}">Limpar</a></form><a class="botao excel" href="{{url_for('excel')}}">⬇ Exportar Excel</a></div>
<div class="filtros"><span class="rotulo">Status</span><button type="button" class="chip ativo" data-filtro="status" data-valor="">Todas</button><button type="button" class="chip" data-filtro="status" data-valor="aberta">Abertas</button><button type="button" class="chip" data-filtro="status" data-valor="finalizada">Finalizadas</button><span class="rotulo">Prioridade</span><select data-filtro="prioridade"><option value="">Todas</option><option value="critica">Crítica</option><option value="alta">Alta</option><option value="media">Média</option><option value="baixa">Baixa</option></select><span class="contagem" id="contador"></span></div></section>
<section class="card"><h2>Ordens de Serviço registradas</h2><p class="muted">Clique em uma linha para ver a descrição e nos títulos das colunas para ordenar.</p><div class="tabela-wrapper"><table id="tabela" class="detalhavel"><thead><tr><th data-tipo="num">OS</th><th data-tipo="texto">Solicitante</th><th data-tipo="texto">Setor</th><th data-tipo="texto">Causa</th><th data-tipo="texto">Prioridade</th><th data-tipo="texto">Responsável</th><th data-tipo="texto">Tipo</th><th data-tipo="texto">Status</th><th data-tipo="data">Abertura</th><th data-tipo="data">Finalização</th><th>Tempo total</th><th>Ações</th></tr></thead><tbody>
{% for os in ordens %}<tr class="linha" data-status="{{'finalizada' if os.status=='Finalizada' else 'aberta'}}" data-prioridade="{{os.prioridade|lower|replace('í','i')|replace('é','e')}}"><td><strong>{{os.numero_os}}</strong></td><td>{{os.solicitante}}</td><td>{{os.setor or '-'}}</td><td>{{os.titulo or '-'}}</td><td><span class="prioridade prioridade-{{os.prioridade|lower|replace('í','i')|replace('é','e')}}">{{os.prioridade}}</span></td><td>{{os.responsavel or '-'}}</td><td>{% if os.tipo_manutencao %}<span class="tipo-badge tipo-{{os.tipo_manutencao|lower}}">{{os.tipo_manutencao}}</span>{% else %}<span class="muted">-</span>{% endif %}</td><td><span class="status {{'finalizada' if os.status=='Finalizada' else 'aberta'}}">{{os.status}}</span></td><td>{{os.abertura}}</td><td>{{os.finalizacao or '-'}}</td><td>{{os.tempo_total or '-'}}</td><td><div class="acoes">{% if os.status!='Finalizada' %}<button type="button" class="finalizar btn-abrir-finalizar" data-url="{{url_for('finalizar',id=os.id)}}" data-numero="{{os.numero_os}}">Finalizar</button>{% endif %}<form action="{{url_for('excluir',id=os.id)}}" method="POST" data-perigo="1" data-confirm="Tem certeza que deseja excluir a O.S. {{os.numero_os}}?" data-confirm-btn="Excluir"><button class="excluir">Excluir</button></form></div></td></tr><tr class="detalhe"><td colspan="12"><strong style="color:var(--or2)">Descrição do serviço</strong><p>{{os.descricao or 'Sem descrição informada.'}}</p>
{% if os.funcionarios %}<p style="margin-top:8px"><strong>Funcionários:</strong> {{os.funcionarios}} | <strong>Tempo Trabalhado:</strong> {{fmt_hhmm(os.horas_trabalhadas)}}</p>{% endif %}
</td></tr>{% else %}<tr><td colspan="12" class="vazio">Nenhuma Ordem de Serviço encontrada.</td></tr>{% endfor %}<tr class="sem-resultado"><td colspan="12" class="vazio">Nenhum resultado para os filtros aplicados.</td></tr></tbody></table></div></section><footer class="rodape">Central de O.S. — Sistema de controle de serviços</footer></div>

<div class="overlay" id="modalFinalizar">
    <div class="modal">
        <h3>🔧 Finalizar Ordem de Serviço <span id="osNumeroFinalizar" style="color:var(--or2)"></span></h3>
        <p class="muted">Selecione o tipo, os funcionários que trabalharam e o tempo gasto.</p>
        <form id="formFinalizar" method="POST">
            <div class="campo">
                <label>Tipo de Manutenção *</label>
                <div class="seg">
                    <input type="radio" id="tipoPrev" name="tipo_manutencao" value="Preventiva" checked>
                    <label for="tipoPrev">🛡️ Preventiva</label>
                    <input type="radio" id="tipoCorr" name="tipo_manutencao" value="Corretiva">
                    <label for="tipoCorr">⚡ Corretiva</label>
                </div>
            </div>
            <div class="campo">
                <label>Funcionários Envolvidos *</label>
                <div class="checkbox-grid">
                    <label><input type="checkbox" name="funcionarios" value="Cesar"> Cesar (Mecânico)</label>
                    <label><input type="checkbox" name="funcionarios" value="Jefferson"> Jefferson (Eletricista)</label>
                    <label><input type="checkbox" name="funcionarios" value="Erivaldo"> Erivaldo (Mecânico)</label>
                    <label><input type="checkbox" name="funcionarios" value="Edivaldo"> Edivaldo (Eletricista)</label>
                    <label><input type="checkbox" name="funcionarios" value="Ailton"> Ailton (Mecânico)</label>
                    <label><input type="checkbox" name="funcionarios" value="Douglas"> Douglas (Eletricista)</label>
                </div>
            </div>
            <div class="campo">
                <label>Horas Trabalhadas (HH:MM) *</label>
                <input type="time" name="tempo_trabalhado" id="tempoTrabalhado" required value="01:00">
            </div>
            <div class="modal-acoes">
                <button type="button" class="limpar" data-fechar-fin>Cancelar</button>
                <button type="submit" class="salvar">Confirmar e Finalizar</button>
            </div>
        </form>
    </div>
</div>

<script>window.__FLASH__={{flash_msgs|tojson}};</script><script>{{js|safe}}</script></body></html>
"""

HTML_LOGIN_ESTOQUE = r"""
<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LOGIN - ESTOQUE JAMAICA</title><style>{{css|safe}}</style></head><body class="login"><div class="container-login"><div class="cabecalho"><h1>🔒 ESTOQUE DE MANUTENÇÃO</h1><p>Acesso Restrito</p></div><div class="card-login"><form action="{{url_for('estoque_login')}}" method="POST"><div class="campo"><label>Usuário</label><input name="usuario" required autofocus autocomplete="username" placeholder="Digite o usuário"></div><div class="campo"><label>Senha</label><div class="senha-wrap"><input type="password" id="senha" name="senha" required autocomplete="current-password" placeholder="Digite a senha"><button type="button" class="olho" id="verSenha">👁️</button></div></div><button class="salvar" type="submit">Entrar no Estoque</button><a href="{{url_for('dashboard')}}" class="botao limpar">Voltar para Ordens de Serviço</a></form></div></div><script>window.__FLASH__={{flash_msgs|tojson}};</script><script>{{js|safe}}</script></body></html>
"""

HTML_ESTOQUE = r"""
<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CONTROLE DE ESTOQUE - JAMAICA</title><style>{{css|safe}}</style></head><body><div class="container">
<header class="cabecalho"><div><h1>CONTROLE DE ESTOQUE DE MANUTENÇÃO</h1><p>Gerenciamento de peças, entradas, retiradas e estoque mínimo</p>
<div class="nav-abas">
    <a href="{{url_for('dashboard')}}" class="aba">📋 Ordens de Serviço</a>
    <a href="{{url_for('dashboard_estoque')}}" class="aba ativa">📦 Controle de Estoque</a>
    <a href="{{url_for('dashboard_spare')}}" class="aba">🔧 Spare Parts</a>
    <a href="{{url_for('indicadores')}}" class="aba">📊 Indicadores & Gráficos</a>
    <a href="{{url_for('equipe_eficiencia')}}" class="aba">👥 Equipe & Eficiência</a>
</div></div><div class="cabecalho-lado"><span class="relogio" id="relogio"></span><a href="{{url_for('estoque_logout')}}" class="botao-sair">Sair do Estoque</a></div></header>
<section class="resumo"><div class="resumo-card"><span class="icone">🔩</span><span>Total de Peças</span><strong data-count="{{total_pecas}}">{{total_pecas}}</strong></div><div class="resumo-card"><span class="icone">📦</span><span>Volume em Estoque</span><strong data-count="{{total_quantidade}}">{{total_quantidade}}</strong></div><div class="resumo-card" data-atalho='[data-filtro="baixo"][data-valor="1"]'><span class="icone">🚨</span><span>Estoque Baixo</span><strong style="color:#f87171" data-count="{{itens_baixo}}">{{itens_baixo}}</strong><small>Clique para filtrar</small></div><div class="resumo-card"><span class="icone">🔁</span><span>Movimentações</span><strong data-count="{{total_movimentacoes}}">{{total_movimentacoes}}</strong></div></section>
<details class="card"><summary>Cadastrar Nova Peça no Estoque</summary><div class="conteudo"><form action="{{url_for('cadastrar_peca')}}" method="POST"><div class="form-grid tres"><div class="campo"><label>Código / SKU *</label><input name="codigo" required maxlength="50" placeholder="Ex: PEC-001" style="text-transform:uppercase"></div><div class="campo"><label>Nome da Peça *</label><input name="nome" required maxlength="150" placeholder="Ex: Rolamento 6204"></div><div class="campo"><label>Categoria</label><input name="categoria" placeholder="Ex: Mecânica, Elétrica"></div><div class="campo"><label>Localização</label><input name="localizacao" placeholder="Ex: Prateleira B-3"></div><div class="campo"><label>Quantidade Inicial *</label><input type="number" name="quantidade" min="0" value="1" required></div><div class="campo"><label>Estoque Mínimo</label><input type="number" name="estoque_minimo" min="1" value="2" required></div><div class="campo completo"><button class="salvar">+ Cadastrar Peça</button></div></div></form></div></details>
<section class="card" id="secao-tabela"><div class="acoes-topo"><form class="busca" action="{{url_for('dashboard_estoque')}}" method="GET"><div class="campo-busca"><input id="q" name="busca" value="{{busca}}" autocomplete="off" placeholder="Pesquisar por código, nome, categoria ou localização"></div><a class="botao limpar" href="{{url_for('dashboard_estoque')}}">Limpar</a></form><a class="botao excel" href="{{url_for('excel_estoque')}}">⬇ Exportar Excel (Estoque)</a></div><div class="filtros"><span class="rotulo">Exibir</span><button type="button" class="chip ativo" data-filtro="baixo" data-valor="">Todas as peças</button><button type="button" class="chip" data-filtro="baixo" data-valor="1">🚨 Somente estoque baixo</button><span class="contagem" id="contador"></span></div></section>
<section class="card"><h2>Peças em Estoque</h2><div class="tabela-wrapper"><table id="tabela"><thead><tr><th data-tipo="texto">Código</th><th data-tipo="texto">Peça</th><th data-tipo="texto">Categoria</th><th data-tipo="texto">Localização</th><th data-tipo="num">Quantidade</th><th data-tipo="num">Mín.</th><th>Status</th><th data-tipo="data">Última Atualização</th><th>Ações</th></tr></thead><tbody>
{% for p in pecas %}{% set baixo=p.quantidade<=p.estoque_minimo %}{% set pct=[((p.quantidade/((p.estoque_minimo*3) or 1))*100)|round|int,100]|min %}<tr class="linha" data-baixo="{{'1' if baixo else ''}}"><td><strong>{{p.codigo}}</strong></td><td>{{p.nome}}</td><td>{{p.categoria or '-'}}</td><td>{{p.localizacao or '-'}}</td><td class="nivel"><strong>{{p.quantidade}}</strong><div class="barra {{'baixo' if baixo else ''}}"><span style="width:{{pct}}%"></span></div></td><td>{{p.estoque_minimo}}</td><td>{% if baixo %}<span class="status estoque-baixo">Baixo Estoque</span>{% else %}<span class="status estoque-ok">Normal</span>{% endif %}</td><td>{{p.ultima_atualizacao}}</td><td><div class="acoes">
<button type="button" class="editar-peca" data-url="{{url_for('editar_peca',id=p.id)}}" data-codigo="{{p.codigo}}" data-nome="{{p.nome}}" data-categoria="{{p.categoria}}" data-localizacao="{{p.localizacao}}" data-estoque-minimo="{{p.estoque_minimo}}">✏️ Editar</button>
<button type="button" class="movimentar" data-url="{{url_for('movimentar_estoque',id=p.id)}}" data-nome="{{p.nome}}" data-codigo="{{p.codigo}}" data-qtd="{{p.quantidade}}" data-min="{{p.estoque_minimo}}">⇄ Movimentar</button>
<form action="{{url_for('excluir_peca',id=p.id)}}" method="POST" data-perigo="1" data-confirm="Tem certeza que deseja excluir a peça {{p.nome}} ({{p.codigo}})?" data-confirm-btn="Excluir"><button class="excluir" title="Excluir peça">✕</button></form>
</div></td></tr>{% else %}<tr><td colspan="9" class="vazio">Nenhuma peça cadastrada no estoque.</td></tr>{% endfor %}<tr class="sem-resultado"><td colspan="9" class="vazio">Nenhuma peça corresponde aos filtros aplicados.</td></tr></tbody></table></div></section>
<section class="card"><h2>Últimas Movimentações (Entradas e Retiradas)</h2><div class="tabela-wrapper"><table style="min-width:700px"><thead><tr><th>Data / Hora</th><th>Tipo</th><th>Código</th><th>Peça</th><th>Quantidade</th><th>Solicitante / Responsável</th></tr></thead><tbody>{% for m in movimentacoes %}<tr><td>{{m.data_hora}}</td><td>{% if m.tipo=='Retirada' %}<span style="color:#f59e0b;font-weight:bold">▼ Retirada</span>{% else %}<span style="color:#34d399;font-weight:bold">▲ Entrada</span>{% endif %}</td><td>{{m.peca_codigo}}</td><td>{{m.peca_nome}}</td><td><strong>{{m.quantidade}}</strong></td><td>{{m.solicitante or '-'}}</td></tr>{% else %}<tr><td colspan="6" class="vazio">Nenhuma movimentação registrada.</td></tr>{% endfor %}</tbody></table></div></section><footer class="rodape">Controle de Estoque — Sistema de Manutenção</footer></div>
<div class="overlay" id="modalMov"><div class="modal"><h3 id="movTitulo"></h3><p class="muted" id="movSub"></p><form id="formMov" method="POST"><div class="seg"><input type="radio" id="tipoRet" name="tipo" value="Retirada" checked><label for="tipoRet">▼ Retirar</label><input type="radio" id="tipoEnt" name="tipo" value="Entrada"><label for="tipoEnt">▲ Adicionar</label></div><div class="campo"><label>Quantidade</label><div class="stepper"><button type="button" data-step="-1">−</button><input type="number" name="qtd" id="movQtd" min="1" value="1" required><button type="button" data-step="1">+</button></div></div><p class="saldo" id="boxSaldo">Saldo após a operação: <strong id="movSaldo">0</strong></p><div class="campo"><label id="movLblSol">Solicitante *</label><input type="text" name="solicitante" id="movSol" maxlength="100" autocomplete="off"></div><div class="modal-acoes"><button type="button" class="limpar" data-fechar>Cancelar</button><button type="submit" class="salvar">Confirmar</button></div></form></div></div>
<div class="overlay" id="modalEditarPeca">
    <div class="modal">
        <h3>✏️ Editar cadastro da peça</h3>
        <p class="muted">Altere as informações do item sem perder o histórico.</p>
        <form id="formEditarPeca" method="POST">
            <div class="campo"><label>Código / SKU *</label><input type="text" name="codigo" id="editarCodigoPeca" maxlength="50" required autocomplete="off" style="text-transform:uppercase"></div>
            <div class="campo"><label>Nome da Peça *</label><input type="text" name="nome" id="editarNomePeca" maxlength="150" required autocomplete="off"></div>
            <div class="campo"><label>Categoria</label><input type="text" name="categoria" id="editarCategoriaPeca" maxlength="100" autocomplete="off"></div>
            <div class="campo"><label>Localização</label><input type="text" name="localizacao" id="editarLocalizacaoPeca" maxlength="100" autocomplete="off"></div>
            <div class="campo"><label>Estoque Mínimo *</label><input type="number" name="estoque_minimo" id="editarEstoqueMinimoPeca" min="1" required></div>
            <div class="modal-acoes"><button type="button" class="limpar" id="cancelarEdicaoPeca">Cancelar</button><button type="submit" class="salvar">Salvar alterações</button></div>
        </form>
    </div>
</div>
<script>window.__FLASH__={{flash_msgs|tojson}};</script><script>{{js|safe}}</script></body></html>
"""

HTML_SPARE = r"""
<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>SPARE PARTS - MÁQUINAS E PEÇAS</title><style>{{css|safe}}</style></head><body><div class="container">
<header class="cabecalho"><div><h1>CADASTRO DE SPARE PARTS (PEÇAS DE REPOSIÇÃO)</h1><p>Gerenciamento de máquinas, modelos e seus componentes de reposição</p>
<div class="nav-abas">
    <a href="{{url_for('dashboard')}}" class="aba">📋 Ordens de Serviço</a>
    <a href="{{url_for('dashboard_estoque')}}" class="aba">📦 Controle de Estoque</a>
    <a href="{{url_for('dashboard_spare')}}" class="aba ativa">🔧 Spare Parts</a>
    <a href="{{url_for('indicadores')}}" class="aba">📊 Indicadores & Gráficos</a>
    <a href="{{url_for('equipe_eficiencia')}}" class="aba">👥 Equipe & Eficiência</a>
</div></div><div class="cabecalho-lado"><span class="relogio" id="relogio"></span></div></header>

<details class="card" open><summary>Cadastrar Máquina para Spare Parts</summary><div class="conteudo">
<form action="{{url_for('cadastrar_maquina_spare')}}" method="POST"><div class="form-grid tres">
<div class="campo"><label>Máquina *</label><input name="maquina" required maxlength="150" placeholder="Ex: Prensa Hidráulica"></div>
<div class="campo"><label>Modelo *</label><input name="modelo" required maxlength="100" placeholder="Ex: PH-50 Tons"></div>
<div class="campo"><label>Setor</label><input name="setor" maxlength="100" placeholder="Ex: Estampagem"></div>
<div class="campo completo"><button class="salvar" type="submit">+ Cadastrar Máquina</button></div>
</div></form></div></details>

<section class="card" id="secao-tabela">
<div class="acoes-topo">
    <form class="busca" action="{{url_for('dashboard_spare')}}" method="GET">
        <div class="campo-busca"><input id="q" name="busca" value="{{busca}}" autocomplete="off" placeholder="Pesquisar por máquina, modelo, setor ou peças necessárias..."></div>
        <a class="botao limpar" href="{{url_for('dashboard_spare')}}">Limpar</a>
    </form>
</div>
</section>

<section class="card">
<h2>Relação de Máquinas e Peças para Reposição</h2>
<p class="muted">Clique em uma linha para expandir e gerenciar as peças de reposição (Spare Parts) da máquina.</p>
<div class="tabela-wrapper">
<table id="tabela" class="detalhavel">
<thead><tr><th>Máquina</th><th>Modelo</th><th>Setor</th><th>Peças Necessárias no Estoque</th><th>Qtd. Peças Cadastradas</th><th>Ações</th></tr></thead>
<tbody>
{% for m in maquinas %}
<tr class="linha">
    <td><strong>{{m.maquina}}</strong></td>
    <td>{{m.modelo}}</td>
    <td>{{m.setor or '-'}}</td>
    <td>{{m.pecas_necessarias or '-'}}</td>
    <td><span class="status estoque-ok">{{m.pecas|length}} peça(s)</span></td>
    <td>
        <form action="{{url_for('excluir_maquina_spare', id=m.id)}}" method="POST" data-perigo="1" data-confirm="Deseja excluir a máquina {{m.maquina}} e todas as suas peças de reposição?" data-confirm-btn="Excluir">
            <button class="excluir">Excluir</button>
        </form>
    </td>
</tr>
<tr class="detalhe">
    <td colspan="6">
        <div style="padding:10px 0">
            {% if m.pecas_necessarias %}
            <div style="margin-bottom:15px;padding:12px;background:#141414;border-left:4px solid var(--or);border-radius:6px">
                <strong style="color:var(--or2)">📦 Peças Necessárias no Estoque para esta Máquina:</strong>
                <p style="margin:5px 0 0 0;white-space:pre-wrap">{{m.pecas_necessarias}}</p>
            </div>
            {% endif %}

            <h4 style="margin:0 0 10px;color:var(--or2)">⚙️ Peças para Reposição Cadastradas (Spare Parts)</h4>
            
            {% if m.pecas %}
            <table style="margin-bottom:15px;background:#141414">
                <thead><tr><th>Nome da Peça</th><th>Código / SKU</th><th>Qtd. Necessária</th><th>Observação</th><th>Ação</th></tr></thead>
                <tbody>
                {% for p in m.pecas %}
                <tr>
                    <td><strong>{{p.nome_peca}}</strong></td>
                    <td>{{p.codigo_peca or '-'}}</td>
                    <td>{{p.quantidade}}</td>
                    <td>{{p.observacao or '-'}}</td>
                    <td>
                        <form action="{{url_for('excluir_peca_spare', id=p.id)}}" method="POST">
                            <button class="excluir" style="padding:4px 8px;font-size:11px">✕</button>
                        </form>
                    </td>
                </tr>
                {% endfor %}
                </tbody>
            </table>
            {% else %}
            <p class="muted">Nenhuma peça de reposição individual vinculada a esta máquina.</p>
            {% endif %}

            <details style="margin-top:10px;background:#0a0a0a;padding:12px;border-radius:8px;border:1px solid var(--bd)">
                <summary style="cursor:pointer;color:var(--or2);font-weight:bold">+ Adicionar Peça para Reposição</summary>
                <form action="{{url_for('adicionar_peca_spare', id=m.id)}}" method="POST" style="margin-top:12px">
                    <div class="form-grid tres">
                        <div class="campo"><label>Nome da Peça *</label><input name="nome_peca" required placeholder="Ex: Retificador 24V"></div>
                        <div class="campo"><label>Código / SKU</label><input name="codigo_peca" placeholder="Ex: RET-01"></div>
                        <div class="campo"><label>Quantidade</label><input type="number" name="quantidade" value="1" min="1"></div>
                        <div class="campo completo"><label>Observação</label><input name="observacao" placeholder="Ex: Manter 1 em estoque de segurança"></div>
                        <div class="campo completo"><button class="salvar" type="submit">+ Salvar Peça</button></div>
                    </div>
                </form>
            </details>
        </div>
    </td>
</tr>
{% else %}
<tr><td colspan="6" class="vazio">Nenhuma máquina cadastrada para Spare Parts.</td></tr>
{% endfor %}
</tbody>
</table>
</div>
</section>

<footer class="rodape">Central de O.S. — Spare Parts</footer></div>
<script>window.__FLASH__={{flash_msgs|tojson}};</script><script>{{js|safe}}</script></body></html>
"""

HTML_INDICADORES = r"""
<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>INDICADORES DE MANUTENÇÃO - JAMAICA</title><style>{{css|safe}}
.chart-container{position:relative;width:100%;max-width:450px;height:380px;margin:0 auto}
.grid-indicadores{display:grid;grid-template-columns:1fr 1fr;gap:20px;align-items:start}
@media(max-width:900px){.grid-indicadores{grid-template-columns:1fr}}
.filtro-mes-wrap{display:flex;gap:10px;align-items:center;margin-bottom:20px;flex-wrap:wrap}
.filtro-mes-wrap select{width:auto;padding:10px 16px;border-radius:10px}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head><body><div class="container">
<header class="cabecalho"><div><h1>INDICADORES E PORCENTAGEM DE MANUTENÇÃO</h1><p>Acompanhamento de manutenções preventivas e corretivas</p>
<div class="nav-abas">
    <a href="{{url_for('dashboard')}}" class="aba">📋 Ordens de Serviço</a>
    <a href="{{url_for('dashboard_estoque')}}" class="aba">📦 Controle de Estoque</a>
    <a href="{{url_for('dashboard_spare')}}" class="aba">🔧 Spare Parts</a>
    <a href="{{url_for('indicadores')}}" class="aba ativa">📊 Indicadores & Gráficos</a>
    <a href="{{url_for('equipe_eficiencia')}}" class="aba">👥 Equipe & Eficiência</a>
</div></div><div class="cabecalho-lado"><span class="relogio" id="relogio"></span></div></header>

<section class="resumo">
<div class="resumo-card"><span class="icone">🛡️</span><span>Total Preventivas</span><strong data-count="{{total_preventiva}}">{{total_preventiva}}</strong><small>{{pct_geral_prev}}% do total geral</small></div>
<div class="resumo-card"><span class="icone">⚡</span><span>Total Corretivas</span><strong data-count="{{total_corretiva}}">{{total_corretiva}}</strong><small>{{pct_geral_corr}}% do total geral</small></div>
<div class="resumo-card"><span class="icone">📈</span><span>Total Finalizadas</span><strong data-count="{{total_fin}}">{{total_fin}}</strong></div>
<div class="resumo-card"><span class="icone">📅</span><span>Meses Analisados</span><strong data-count="{{total_meses}}">{{total_meses}}</strong></div></section>

<div class="grid-indicadores">
    <section class="card">
        <h2>🥧 Gráfico de Pizza (Proporção)</h2>
        <div class="filtro-mes-wrap">
            <label for="selectMes" style="margin:0;font-size:13px;color:var(--mt)">Filtrar por Mês:</label>
            <select id="selectMes">
                <option value="geral">📅 Visão Geral (Todos os Meses)</option>
                {% for m in dados_tabela %}
                <option value="{{m.chave}}">{{m.mes_nome}}</option>
                {% endfor %}
            </select>
        </div>
        <div class="chart-container">
            <canvas id="graficoPizza"></canvas>
        </div>
    </section>

    <section class="card">
        <h2>💡 Insights & Desempenho Operacional</h2>
        <p class="muted">Análise inteligente dos indicadores de manutenção:</p>
        <div style="margin-top:15px;line-height:1.7;font-size:14px">
            <p>• <strong>Objetivo de Confiabilidade:</strong> O padrão de excelência industrial recomenda manter o índice de manutenções preventivas acima de <strong>70%</strong> para mitigar falhas inesperadas.</p>
            <p>• <strong>Status Acumulado:</strong> Atualmente, o indicador global aponta <strong>{{pct_geral_prev}}%</strong> preventivas contra <strong>{{pct_geral_corr}}%</strong> corretivas.</p>
        </div>
    </section>
</div>

<section class="card">
    <h2>Tabela Detalhada por Mês</h2>
    <div class="tabela-wrapper">
        <table>
            <thead>
                <tr>
                    <th>Mês / Ano</th>
                    <th>Total O.S. Finalizadas</th>
                    <th>Preventivas (Qtd)</th>
                    <th>Preventivas (%)</th>
                    <th>Corretivas (Qtd)</th>
                    <th>Corretivas (%)</th>
                </tr>
            </thead>
            <tbody>
                {% for m in dados_tabela %}
                <tr>
                    <td><strong>{{m.mes_nome}}</strong></td>
                    <td>{{m.total}}</td>
                    <td><span class="tipo-badge tipo-preventiva">{{m.prev_qtd}}</span></td>
                    <td><strong>{{m.prev_pct}}%</strong></td>
                    <td><span class="tipo-badge tipo-corretiva">{{m.corr_qtd}}</span></td>
                    <td><strong>{{m.corr_pct}}%</strong></td>
                </tr>
                {% else %}
                <tr>
                    <td colspan="6" class="vazio">Nenhuma O.S. finalizada com tipo de manutenção registrado ainda.</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</section>

<footer class="rodape">Central de O.S. — Indicadores de Manutenção</footer></div>

<script>
window.__FLASH__={{flash_msgs|tojson}};
const dadosTabela = {{ dados_tabela|tojson }};
const totalGeralPrev = {{ total_preventiva }};
const totalGeralCorr = {{ total_corretiva }};

document.addEventListener("DOMContentLoaded", function() {
    const ctx = document.getElementById('graficoPizza').getContext('2d');
    let chartInstance = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Preventiva', 'Corretiva'],
            datasets: [{
                data: [totalGeralPrev, totalGeralCorr],
                backgroundColor: ['rgba(59, 130, 246, 0.85)', 'rgba(249, 115, 22, 0.85)'],
                borderColor: ['#3b82f6', '#f97316'],
                borderWidth: 2,
                hoverOffset: 8
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'bottom', labels: { color: '#f5f5f5', font: { family: 'Segoe UI', size: 13 }, padding: 20 } }
            },
            cutout: '55%'
        }
    });

    const selectMes = document.getElementById('selectMes');
    selectMes.addEventListener('change', function() {
        const val = this.value;
        if(val === 'geral') {
            chartInstance.data.datasets[0].data = [totalGeralPrev, totalGeralCorr];
        } else {
            const encontrado = dadosTabela.find(item => item.chave === val);
            if(encontrado) {
                chartInstance.data.datasets[0].data = [encontrado.prev_qtd, encontrado.corr_qtd];
            } else {
                chartInstance.data.datasets[0].data = [0, 0];
            }
        }
        chartInstance.update();
    });
});
</script>
<script>{{js|safe}}</script></body></html>
"""

HTML_EQUIPE = r"""
<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>EQUIPE E EFICIÊNCIA - JAMAICA</title><style>{{css|safe}}
.chart-container{position:relative;width:100%;max-width:100%;height:320px;margin:0 auto}
.grid-duplas{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px;margin-bottom:24px}
@media(max-width:1100px){.grid-duplas{grid-template-columns:1fr}}
.dupla-card{background:linear-gradient(170deg,var(--s2),var(--s1));border:1px solid var(--bd);border-radius:var(--r);padding:22px;box-shadow:0 8px 30px rgba(0,0,0,0.3)}
.dupla-card h3{margin:0 0 10px;font-size:18px;color:var(--or2)}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head><body><div class="container">
<header class="cabecalho"><div><h1>EQUIPE DE MANUTENÇÃO & EFICIÊNCIA DAS DUPLAS</h1><p>Controle de horas trabalhadas por dupla e eficiência diária</p>
<div class="nav-abas">
    <a href="{{url_for('dashboard')}}" class="aba">📋 Ordens de Serviço</a>
    <a href="{{url_for('dashboard_estoque')}}" class="aba">📦 Controle de Estoque</a>
    <a href="{{url_for('dashboard_spare')}}" class="aba">🔧 Spare Parts</a>
    <a href="{{url_for('indicadores')}}" class="aba">📊 Indicadores & Gráficos</a>
    <a href="{{url_for('equipe_eficiencia')}}" class="aba ativa">👥 Equipe & Eficiência</a>
</div></div><div class="cabecalho-lado"><span class="relogio" id="relogio"></span></div></header>

<div class="grid-duplas">
    <div class="dupla-card">
        <h3>🛡️ Dupla 1</h3>
        <p><strong>Mecânico:</strong> Cesar (05:00 - 14:00)</p>
        <p><strong>Eletricista:</strong> Jefferson (05:00 - 14:00)</p>
        <hr style="border-color:var(--bd);margin:15px 0">
        <p>Total Trabalhado: <strong style="color:var(--or2)">{{ dupla1_tempo_str }}</strong></p>
        <p>Eficiência da Dupla: <strong style="color:#4ade80">{{ dupla1_eficiencia }}%</strong></p>
        <div class="progresso" style="margin-top:10px"><span style="width:{{ dupla1_eficiencia_clamp }}%"></span></div>
    </div>
    
    <div class="dupla-card">
        <h3>⚡ Dupla 2</h3>
        <p><strong>Mecânico:</strong> Erivaldo (07:00 - 16:00)</p>
        <p><strong>Eletricista:</strong> Edivaldo (07:00 - 16:00)</p>
        <hr style="border-color:var(--bd);margin:15px 0">
        <p>Total Trabalhado: <strong style="color:var(--or2)">{{ dupla2_tempo_str }}</strong></p>
        <p>Eficiência da Dupla: <strong style="color:#4ade80">{{ dupla2_eficiencia }}%</strong></p>
        <div class="progresso" style="margin-top:10px"><span style="width:{{ dupla2_eficiencia_clamp }}%"></span></div>
    </div>

    <div class="dupla-card">
        <h3>⚙️ Dupla 3</h3>
        <p><strong>Mecânico:</strong> Ailton (15:30 - 01:04)</p>
        <p><strong>Eletricista:</strong> Douglas (15:30 - 01:04)</p>
        <hr style="border-color:var(--bd);margin:15px 0">
        <p>Total Trabalhado: <strong style="color:var(--or2)">{{ dupla3_tempo_str }}</strong></p>
        <p>Eficiência da Dupla: <strong style="color:#4ade80">{{ dupla3_eficiencia }}%</strong></p>
        <div class="progresso" style="margin-top:10px"><span style="width:{{ dupla3_eficiencia_clamp }}%"></span></div>
    </div>
</div>

<section class="card">
    <h2>📊 Gráfico de Eficiência Comparativa das Duplas (%)</h2>
    <div class="chart-container">
        <canvas id="graficoEficiencia"></canvas>
    </div>
</section>

<footer class="rodape">Central de O.S. — Equipe & Eficiência</footer></div>

<script>
window.__FLASH__={{flash_msgs|tojson}};
document.addEventListener("DOMContentLoaded", function() {
    const ctx = document.getElementById('graficoEficiencia').getContext('2d');
    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: ['Dupla 1 CESAR E JEFFERSON', 'Dupla 2 ERIVALDO E EDIVALDO', 'Dupla 3 AILTON E DOUGLAS'],
            datasets: [{
                label: 'Eficiência (%)',
                data: [{{ dupla1_eficiencia }}, {{ dupla2_eficiencia }}, {{ dupla3_eficiencia }}],
                backgroundColor: ['rgba(59, 130, 246, 0.85)', 'rgba(34, 197, 94, 0.85)', 'rgba(249, 115, 22, 0.85)'],
                borderColor: ['#3b82f6', '#22c55e', '#f97316'],
                borderWidth: 2,
                borderRadius: 8
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: { beginAtZero: true, max: 100, ticks: { color: '#f5f5f5' }, grid: { color: '#262626' } },
                x: { ticks: { color: '#f5f5f5' }, grid: { display: false } }
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            return ` Eficiência: ${context.parsed.y}%`;
                        }
                    }
                }
            }
        }
    });
});
</script>
<script>{{js|safe}}</script></body></html>
"""


@app.route("/")
def dashboard():
    busca = request.args.get("busca", "").strip()
    consulta = OrdemServico.query
    if busca:
        termo = f"%{busca}%"
        consulta = consulta.filter(db.or_(
            OrdemServico.numero_os.ilike(termo), OrdemServico.solicitante.ilike(termo),
            OrdemServico.setor.ilike(termo), OrdemServico.titulo.ilike(termo),
            OrdemServico.descricao.ilike(termo), OrdemServico.responsavel.ilike(termo)))
    ordens = consulta.order_by(OrdemServico.id.desc()).all()
    abertas = OrdemServico.query.filter_by(status="Aberta").count()
    finalizadas = OrdemServico.query.filter_by(status="Finalizada").count()
    total = OrdemServico.query.count()
    return renderizar(HTML, ordens=ordens, abertas=abertas, finalizadas=finalizadas,
                     total=total, pct_final=round(finalizadas / total * 100) if total else 0,
                     proxima=gerar_numero_os(), busca=busca, fmt_hhmm=horas_decimais_para_hhmm)


@app.route("/criar", methods=["POST"])
def criar():
    solicitante = request.form.get("solicitante", "").strip()
    if not solicitante:
        flash("Informe o nome do solicitante.")
        return redirect(url_for("dashboard"))
    prioridade = request.form.get("prioridade", "Média")
    if prioridade not in ["Baixa", "Média", "Alta", "Crítica"]:
        prioridade = "Média"
    try:
        with os_lock:
            nova = OrdemServico(numero_os=gerar_numero_os(), solicitante=solicitante,
                setor=request.form.get("setor", "").strip(), titulo=request.form.get("titulo", "").strip(),
                descricao=request.form.get("descricao", "").strip(), prioridade=prioridade,
                responsavel=request.form.get("responsavel", "").strip(), abertura=agora_formatado(),
                finalizacao="", status="Aberta", tempo_total="", tipo_manutencao="",
                funcionarios="", horas_trabalhadas=0.0)
            db.session.add(nova)
            db.session.commit()
        flash(f"O.S. {nova.numero_os} criada com sucesso!")
    except Exception:
        db.session.rollback()
        app.logger.exception("Erro ao criar ordem de serviço.")
        flash("Não foi possível criar a O.S. Verifique o erro no terminal.")
    return redirect(url_for("dashboard"))


@app.route("/finalizar/<int:id>", methods=["POST"])
def finalizar(id):
    ordem = db.session.get(OrdemServico, id)
    if ordem is None:
        flash("Ordem de Serviço não encontrada.")
    elif ordem.status == "Finalizada":
        flash(f"A O.S. {ordem.numero_os} já está finalizada.")
    else:
        tipo_manutencao = request.form.get("tipo_manutencao", "Preventiva")
        funcs_selecionados = request.form.getlist("funcionarios")
        tempo_str = request.form.get("tempo_trabalhado", "01:00").strip()
        
        horas_decimais = tempo_hhmm_para_horas_decimais(tempo_str)

        if not funcs_selecionados:
            flash("Selecione pelo menos um funcionário.")
            return redirect(url_for("dashboard"))

        try:
            ordem.finalizacao = agora_formatado()
            ordem.status = "Finalizada"
            ordem.tipo_manutencao = tipo_manutencao
            ordem.funcionarios = ", ".join(funcs_selecionados)
            ordem.horas_trabalhadas = horas_decimais
            ordem.tempo_total = calcular_tempo_total(ordem.abertura, ordem.finalizacao)
            db.session.commit()
            flash(f"O.S. {ordem.numero_os} finalizada com sucesso!")
        except Exception:
            db.session.rollback()
            app.logger.exception("Erro ao finalizar ordem de serviço.")
            flash("Não foi possível finalizar a O.S.")
    return redirect(url_for("dashboard"))


@app.route("/excluir/<int:id>", methods=["POST"])
def excluir(id):
    ordem = db.session.get(OrdemServico, id)
    if ordem is None:
        flash("Ordem de Serviço não encontrada.")
    else:
        numero = ordem.numero_os
        try:
            db.session.delete(ordem)
            db.session.commit()
            flash(f"O.S. {numero} excluída com sucesso!")
        except Exception:
            db.session.rollback()
            app.logger.exception("Erro ao excluir ordem de serviço.")
            flash("Não foi possível excluir a O.S.")
    return redirect(url_for("dashboard"))


@app.route("/indicadores")
def indicadores():
    ordens_fin = OrdemServico.query.filter_by(status="Finalizada").all()
    meses_dict = {}
    meses_nomes_pt = {
        "01": "Janeiro", "02": "Fevereiro", "03": "Março", "04": "Abril",
        "05": "Maio", "06": "Junho", "07": "Julho", "08": "Agosto",
        "09": "Setembro", "10": "Outubro", "11": "Novembro", "12": "Dezembro"
    }

    total_preventiva = 0
    total_corretiva = 0

    for o in ordens_fin:
        tipo = o.tipo_manutencao or "Preventiva"
        if tipo == "Preventiva":
            total_preventiva += 1
        else:
            total_corretiva += 1

        try:
            dt = datetime.strptime(o.finalizacao, FORMATO_DATA)
        except Exception:
            try:
                dt = datetime.strptime(o.abertura, FORMATO_DATA)
            except Exception:
                dt = datetime.now()
        
        chave_mes = dt.strftime("%Y-%m")
        if chave_mes not in meses_dict:
            ano, mes_num = chave_mes.split("-")
            nome_mes = f"{meses_nomes_pt.get(mes_num, mes_num)} de {ano}"
            meses_dict[chave_mes] = {"chave": chave_mes, "mes_nome": nome_mes, "Preventiva": 0, "Corretiva": 0}
        
        if tipo == "Preventiva":
            meses_dict[chave_mes]["Preventiva"] += 1
        else:
            meses_dict[chave_mes]["Corretiva"] += 1

    meses_ordenados = sorted(meses_dict.values(), key=lambda x: x["chave"])

    dados_tabela = []
    for m in meses_ordenados:
        prev = m["Preventiva"]
        corr = m["Corretiva"]
        total_mes = prev + corr
        prev_pct = round((prev / total_mes) * 100, 1) if total_mes > 0 else 0
        corr_pct = round((corr / total_mes) * 100, 1) if total_mes > 0 else 0

        dados_tabela.append({
            "chave": m["chave"],
            "mes_nome": m["mes_nome"],
            "total": total_mes,
            "prev_qtd": prev,
            "prev_pct": prev_pct,
            "corr_qtd": corr,
            "corr_pct": corr_pct
        })

    total_fin = total_preventiva + total_corretiva
    pct_geral_prev = round((total_preventiva / total_fin) * 100, 1) if total_fin > 0 else 0
    pct_geral_corr = round((total_corretiva / total_fin) * 100, 1) if total_fin > 0 else 0

    return renderizar(HTML_INDICADORES,
        total_preventiva=total_preventiva,
        total_corretiva=total_corretiva,
        total_fin=total_fin,
        total_meses=len(meses_ordenados),
        pct_geral_prev=pct_geral_prev,
        pct_geral_corr=pct_geral_corr,
        dados_tabela=dados_tabela
    )


@app.route("/equipe-eficiencia")
def equipe_eficiencia():
    ordens_fin = OrdemServico.query.filter_by(status="Finalizada").all()
    
    dupla1_horas = sum(o.horas_trabalhadas for o in ordens_fin if "Cesar" in (o.funcionarios or "") or "Jefferson" in (o.funcionarios or ""))
    dupla2_horas = sum(o.horas_trabalhadas for o in ordens_fin if "Erivaldo" in (o.funcionarios or "") or "Edivaldo" in (o.funcionarios or ""))
    dupla3_horas = sum(o.horas_trabalhadas for o in ordens_fin if "Ailton" in (o.funcionarios or "") or "Douglas" in (o.funcionarios or ""))

    jornada_referencia = 9.0
    
    dupla1_eficiencia = min(100.0, round((dupla1_horas / jornada_referencia) * 100, 1))
    dupla2_eficiencia = min(100.0, round((dupla2_horas / jornada_referencia) * 100, 1))
    dupla3_eficiencia = min(100.0, round((dupla3_horas / jornada_referencia) * 100, 1))

    return renderizar(HTML_EQUIPE,
        dupla1_tempo_str=horas_decimais_para_hhmm(dupla1_horas), dupla1_eficiencia=dupla1_eficiencia, dupla1_eficiencia_clamp=int(dupla1_eficiencia),
        dupla2_tempo_str=horas_decimais_para_hhmm(dupla2_horas), dupla2_eficiencia=dupla2_eficiencia, dupla2_eficiencia_clamp=int(dupla2_eficiencia),
        dupla3_tempo_str=horas_decimais_para_hhmm(dupla3_horas), dupla3_eficiencia=dupla3_eficiencia, dupla3_eficiencia_clamp=int(dupla3_eficiencia)
    )


def gerar_excel(df, nome_planilha, prefixo):
    memoria = BytesIO()
    with pd.ExcelWriter(memoria, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=nome_planilha)
        planilha = writer.sheets[nome_planilha]
        planilha.freeze_panes = "A2"
        planilha.auto_filter.ref = planilha.dimensions
        for coluna in planilha.columns:
            letra = coluna[0].column_letter
            maior = max(len(str(c.value or "")) for c in coluna)
            planilha.column_dimensions[letra].width = min(max(maior + 2, 12), 40)
    memoria.seek(0)
    nome = f"{prefixo}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    return send_file(memoria, as_attachment=True, download_name=nome,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/excel")
def excel():
    ordens = OrdemServico.query.order_by(OrdemServico.id.asc()).all()
    colunas = ["OS", "Solicitante", "Setor", "Título", "Descrição", "Prioridade",
               "Responsável", "Tipo Manutenção", "Funcionários", "Tempo Trabalhado", "Status", "Abertura", "Finalização", "Tempo total"]
    dados = [{"OS": o.numero_os, "Solicitante": o.solicitante, "Setor": o.setor or "",
              "Título": o.titulo or "", "Descrição": o.descricao or "", "Prioridade": o.prioridade,
              "Responsável": o.responsavel or "", "Tipo Manutenção": o.tipo_manutencao or "",
              "Funcionários": o.funcionarios or "", "Tempo Trabalhado": horas_decimais_para_hhmm(o.horas_trabalhadas or 0),
              "Status": o.status, "Abertura": o.abertura,
              "Finalização": o.finalizacao or "", "Tempo total": o.tempo_total or ""} for o in ordens]
    try:
        return gerar_excel(pd.DataFrame(dados, columns=colunas), "Ordens de Serviço", "ordens_servico")
    except Exception:
        app.logger.exception("Erro ao exportar ordens para Excel.")
        flash("Não foi possível gerar o Excel. Verifique o terminal.")
        return redirect(url_for("dashboard"))


@app.route("/estoque/login", methods=["GET", "POST"])
def estoque_login():
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        senha = request.form.get("senha", "").strip()
        if usuario == "manutencao" and senha == "manutencao123":
            session["logado_estoque"] = True
            return redirect(url_for("dashboard_estoque"))
        flash("Usuário ou senha incorretos.")
    return renderizar(HTML_LOGIN_ESTOQUE)


@app.route("/estoque/logout")
def estoque_logout():
    session.pop("logado_estoque", None)
    return redirect(url_for("estoque_login"))


@app.route("/estoque")
def dashboard_estoque():
    if not session.get("logado_estoque"):
        return redirect(url_for("estoque_login"))
    busca = request.args.get("busca", "").strip()
    consulta = ItemEstoque.query
    if busca:
        termo = f"%{busca}%"
        consulta = consulta.filter(db.or_(ItemEstoque.codigo.ilike(termo), ItemEstoque.nome.ilike(termo),
            ItemEstoque.categoria.ilike(termo), ItemEstoque.localizacao.ilike(termo)))
    pecas = consulta.order_by(ItemEstoque.id.desc()).all()
    movimentacoes = HistoricoMovimentacao.query.order_by(HistoricoMovimentacao.id.desc()).limit(50).all()
    total_pecas = ItemEstoque.query.count()
    total_quantidade = db.session.query(db.func.sum(ItemEstoque.quantidade)).scalar() or 0
    itens_baixo = sum(1 for p in ItemEstoque.query.all() if p.quantidade <= p.estoque_minimo)
    return renderizar(HTML_ESTOQUE, pecas=pecas, movimentacoes=movimentacoes,
        total_pecas=total_pecas, total_quantidade=total_quantidade, itens_baixo=itens_baixo,
        total_movimentacoes=HistoricoMovimentacao.query.count(), busca=busca)


@app.route("/estoque/cadastrar", methods=["POST"])
def cadastrar_peca():
    if not session.get("logado_estoque"):
        return redirect(url_for("estoque_login"))
    codigo = request.form.get("codigo", "").strip().upper()
    nome = request.form.get("nome", "").strip()
    categoria = request.form.get("categoria", "").strip()
    localizacao = request.form.get("localizacao", "").strip()
    try:
        quantidade = int(request.form.get("quantidade", 0))
        estoque_minimo = int(request.form.get("estoque_minimo", 2))
    except ValueError:
        flash("Valores numéricos inválidos.")
        return redirect(url_for("dashboard_estoque"))
    if not codigo or not nome:
        flash("Código e Nome da peça são obrigatórios.")
        return redirect(url_for("dashboard_estoque"))
    if quantidade < 0 or estoque_minimo < 1:
        flash("Quantidade não pode ser negativa e estoque mínimo deve ser maior que zero.")
        return redirect(url_for("dashboard_estoque"))
    if ItemEstoque.query.filter_by(codigo=codigo).first():
        flash(f"Já existe uma peça cadastrada com o código {codigo}.")
        return redirect(url_for("dashboard_estoque"))
    try:
        with estoque_lock:
            peca = ItemEstoque(codigo=codigo, nome=nome, categoria=categoria or "Geral",
                localizacao=localizacao, quantidade=quantidade, estoque_minimo=estoque_minimo,
                ultima_atualizacao=agora_formatado())
            db.session.add(peca)
            db.session.commit()
        flash(f"Peça {nome} ({codigo}) cadastrada com sucesso!")
    except Exception:
        db.session.rollback()
        app.logger.exception("Erro ao cadastrar peça.")
        flash("Erro ao cadastrar peça no estoque.")
    return redirect(url_for("dashboard_estoque"))


@app.route("/estoque/editar/<int:id>", methods=["POST"])
def editar_peca(id):
    if not session.get("logado_estoque"):
        return redirect(url_for("estoque_login"))
    peca = db.session.get(ItemEstoque, id)
    if peca is None:
        flash("Peça não encontrada.")
        return redirect(url_for("dashboard_estoque"))
    
    codigo = request.form.get("codigo", "").strip().upper()
    nome = request.form.get("nome", "").strip()
    categoria = request.form.get("categoria", "").strip()
    localizacao = request.form.get("localizacao", "").strip()
    try:
        estoque_minimo = int(request.form.get("estoque_minimo", 2))
    except ValueError:
        flash("Estoque mínimo inválido.")
        return redirect(url_for("dashboard_estoque"))

    if not codigo or not nome:
        flash("Código e Nome da peça são obrigatórios.")
        return redirect(url_for("dashboard_estoque"))
    
    outro = ItemEstoque.query.filter_by(codigo=codigo).first()
    if outro and outro.id != peca.id:
        flash(f"Já existe outra peça cadastrada com o código {codigo}.")
        return redirect(url_for("dashboard_estoque"))

    try:
        peca.codigo = codigo
        peca.nome = nome
        peca.categoria = categoria or "Geral"
        peca.localizacao = localizacao
        peca.estoque_minimo = max(1, estoque_minimo)
        peca.ultima_atualizacao = agora_formatado()
        db.session.commit()
        flash(f"Peça {nome} ({codigo}) atualizada com sucesso!")
    except Exception:
        db.session.rollback()
        app.logger.exception("Erro ao editar peça.")
        flash("Não foi possível atualizar os dados da peça.")
    return redirect(url_for("dashboard_estoque"))


@app.route("/estoque/movimentar/<int:id>", methods=["POST"])
def movimentar_estoque(id):
    if not session.get("logado_estoque"):
        return redirect(url_for("estoque_login"))
    peca = db.session.get(ItemEstoque, id)
    if not peca:
        flash("Peça não encontrada.")
        return redirect(url_for("dashboard_estoque"))
    tipo = request.form.get("tipo")
    solicitante = request.form.get("solicitante", "").strip()
    try:
        qtd = int(request.form.get("qtd", 0))
    except ValueError:
        flash("Quantidade inválida.")
        return redirect(url_for("dashboard_estoque"))
    if qtd <= 0:
        flash("A quantidade deve ser maior que zero.")
        return redirect(url_for("dashboard_estoque"))
    try:
        with estoque_lock:
            if tipo == "Retirada":
                if not solicitante:
                    flash("Informe o solicitante da retirada.")
                    return redirect(url_for("dashboard_estoque"))
                if peca.quantidade < qtd:
                    flash(f"Estoque insuficiente! Estoque atual de {peca.nome}: {peca.quantidade}")
                    return redirect(url_for("dashboard_estoque"))
                peca.quantidade -= qtd
                msg = f"Retirada de {qtd} unidade(s) de {peca.nome} realizada."
            elif tipo == "Entrada":
                peca.quantidade += qtd
                msg = f"Adicionadas {qtd} unidade(s) ao estoque de {peca.nome}."
            else:
                flash("Tipo de movimentação inválido.")
                return redirect(url_for("dashboard_estoque"))
            peca.ultima_atualizacao = agora_formatado()
            db.session.add(HistoricoMovimentacao(peca_codigo=peca.codigo, peca_nome=peca.nome,
                tipo=tipo, quantidade=qtd, solicitante=solicitante or "Almoxarifado",
                data_hora=agora_formatado()))
            db.session.commit()
        flash(msg)
    except Exception:
        db.session.rollback()
        app.logger.exception("Erro na movimentação de estoque.")
        flash("Erro ao processar movimentação.")
    return redirect(url_for("dashboard_estoque"))


@app.route("/estoque/excluir/<int:id>", methods=["POST"])
def excluir_peca(id):
    if not session.get("logado_estoque"):
        return redirect(url_for("estoque_login"))
    peca = db.session.get(ItemEstoque, id)
    if not peca:
        flash("Peça não encontrada.")
        return redirect(url_for("dashboard_estoque"))
    nome = peca.nome
    try:
        db.session.delete(peca)
        db.session.commit()
        flash(f"Peça {nome} excluída do estoque com sucesso!")
    except Exception:
        db.session.rollback()
        app.logger.exception("Erro ao excluir peça.")
        flash("Não foi possível excluir a peça.")
    return redirect(url_for("dashboard_estoque"))


@app.route("/estoque/excel")
def excel_estoque():
    if not session.get("logado_estoque"):
        return redirect(url_for("estoque_login"))
    pecas = ItemEstoque.query.order_by(ItemEstoque.id.asc()).all()
    colunas = ["Código", "Nome da Peça", "Categoria", "Localização", "Quantidade em Estoque",
               "Estoque Mínimo", "Status", "Última Atualização"]
    dados = [{"Código": p.codigo, "Nome da Peça": p.nome, "Categoria": p.categoria,
        "Localização": p.localizacao, "Quantidade em Estoque": p.quantidade,
        "Estoque Mínimo": p.estoque_minimo,
        "Status": "Baixo Estoque" if p.quantidade <= p.estoque_minimo else "Normal",
        "Última Atualização": p.ultima_atualizacao} for p in pecas]
    try:
        return gerar_excel(pd.DataFrame(dados, columns=colunas), "Estoque Manutenção", "estoque_manutencao")
    except Exception:
        app.logger.exception("Erro ao exportar Excel do estoque.")
        flash("Erro ao gerar arquivo Excel do estoque.")
        return redirect(url_for("dashboard_estoque"))


@app.route("/spare-parts")
def dashboard_spare():
    busca = request.args.get("busca", "").strip()
    consulta = MaquinaSpare.query
    if busca:
        termo = f"%{busca}%"
        consulta = consulta.filter(db.or_(
            MaquinaSpare.maquina.ilike(termo), MaquinaSpare.modelo.ilike(termo),
            MaquinaSpare.setor.ilike(termo), MaquinaSpare.pecas_necessarias.ilike(termo)
        ))
    maquinas = consulta.order_by(MaquinaSpare.id.desc()).all()
    return renderizar(HTML_SPARE, maquinas=maquinas, busca=busca)


@app.route("/spare-parts/cadastrar", methods=["POST"])
def cadastrar_maquina_spare():
    maquina = request.form.get("maquina", "").strip()
    modelo = request.form.get("modelo", "").strip()
    setor = request.form.get("setor", "").strip()
    pecas_necessarias = request.form.get("pecas_necessarias", "").strip()

    if not maquina or not modelo:
        flash("Nome da Máquina e Modelo são obrigatórios.")
        return redirect(url_for("dashboard_spare"))

    try:
        nova = MaquinaSpare(maquina=maquina, modelo=modelo, setor=setor, pecas_necessarias=pecas_necessarias)
        db.session.add(nova)
        db.session.commit()
        flash(f"Máquina '{maquina}' (Modelo: {modelo}) cadastrada com sucesso!")
    except Exception:
        db.session.rollback()
        flash("Erro ao cadastrar máquina para spare parts.")
    return redirect(url_for("dashboard_spare"))


@app.route("/spare-parts/excluir/<int:id>", methods=["POST"])
def excluir_maquina_spare(id):
    maq = db.session.get(MaquinaSpare, id)
    if maq:
        nome = maq.maquina
        db.session.delete(maq)
        db.session.commit()
        flash(f"Registro de Spare Parts da máquina '{nome}' excluído com sucesso!")
    return redirect(url_for("dashboard_spare"))


@app.route("/spare-parts/<int:id>/adicionar-peca", methods=["POST"])
def adicionar_peca_spare(id):
    maq = db.session.get(MaquinaSpare, id)
    if not maq:
        flash("Máquina não encontrada.")
        return redirect(url_for("dashboard_spare"))

    nome_peca = request.form.get("nome_peca", "").strip()
    codigo_peca = request.form.get("codigo_peca", "").strip().upper()
    try:
        quantidade = int(request.form.get("quantidade", 1))
    except ValueError:
        quantidade = 1
    observacao = request.form.get("observacao", "").strip()

    if not nome_peca:
        flash("O nome da peça para reposição é obrigatório.")
        return redirect(url_for("dashboard_spare"))

    try:
        peca = PecaSpare(maquina_spare_id=maq.id, nome_peca=nome_peca, codigo_peca=codigo_peca, quantidade=quantidade, observacao=observacao)
        db.session.add(peca)
        db.session.commit()
        flash(f"Peça '{nome_peca}' adicionada à lista de reposição!")
    except Exception:
        db.session.rollback()
        flash("Erro ao adicionar peça de reposição.")
    return redirect(url_for("dashboard_spare"))


@app.route("/spare-parts/peca/excluir/<int:id>", methods=["POST"])
def excluir_peca_spare(id):
    peca = db.session.get(PecaSpare, id)
    if peca:
        db.session.delete(peca)
        db.session.commit()
        flash("Peça de reposição removida com sucesso!")
    return redirect(url_for("dashboard_spare"))


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)