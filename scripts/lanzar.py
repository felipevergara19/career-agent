#!/usr/bin/env python3
"""Lanza un comando del sistema (/buscar, /adaptar-cv, ...) como agente headless.

Usa la herramienta y el modelo definidos en agentes.yaml (o los que pases por flag),
y deja un log legible en logs/ para seguirlo en vivo (p. ej. en otro panel de Herdr):

  python3 scripts/lanzar.py adaptar-cv OF-004
  python3 scripts/lanzar.py buscar ai-automation --tool cursor --model composer-2.5
  python3 scripts/lanzar.py tracker "postulé a OF-013" --dry-run
  python3 scripts/lanzar.py --verificar          # comprueba agentes.yaml vs .claude/agents
  tail -f logs/*.log                             # ver a los agentes trabajar

Código de salida: el del agente (0 = OK).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"
CONFIG = ROOT / "agentes.yaml"

HEADLESS = (
    "Estás corriendo en modo NO interactivo, lanzado por scripts/lanzar.py en paralelo con otros agentes. "
    "No hagas preguntas: si falta información, toma la opción conservadora y repórtala en el resumen final. "
    "Respeta estrictamente la propiedad de archivos de AGENTS.md §2.4 y usa scripts/estado.py para todo cambio de estado.\n\n"
)

# Permisos mínimos para Claude Code headless (sin --dangerously-skip-permissions).
CLAUDE_TOOLS = [
    "Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch", "Agent",
    "Bash(python3 scripts/estado.py:*)",
    "Bash(python3 scripts/rastrear_ats.py:*)",
    "Bash(python3 scripts/evaluador_ia.py:*)",
    "Bash(python3 scripts/drive_sync.py:*)",
    "Bash(python3 .agents/skills/adaptador-cv/scripts/render_cv.py:*)",
    "Bash(python3 .agents/skills/perfil-tracker/scripts/actualizar_excel.py:*)",
    "Bash(ls:*)", "Bash(mkdir:*)", "Bash(git status:*)", "Bash(git diff:*)", "Bash(date:*)",
]


def cargar_config() -> dict:
  return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def construir_prompt(comando: str, argumentos: str) -> str:
  archivo = ROOT / ".claude" / "commands" / f"{comando}.md"
  if not archivo.is_file():
    raise SystemExit(f"Error: no existe el comando '{comando}' ({archivo.relative_to(ROOT)})")
  texto = archivo.read_text(encoding="utf-8")
  if texto.startswith("---\n"):
    texto = texto[texto.find("\n---\n", 4) + 5:]
  return HEADLESS + texto.replace("$ARGUMENTS", argumentos or "(sin argumentos)").strip() + "\n"


def construir_cmd(herramienta: str, modelo: str, prompt: str) -> tuple[list[str], str | None]:
  """Devuelve (argv, stdin)."""
  if herramienta == "claude":
    return ([
        "claude", "-p", "--model", modelo, "--output-format", "stream-json", "--verbose",
        "--permission-mode", "acceptEdits", "--allowedTools", *CLAUDE_TOOLS,
    ], prompt)
  if herramienta == "cursor":
    return ([
        "cursor-agent", "-p", "--model", modelo, "--output-format", "stream-json",
        "--trust", "--force", prompt,
    ], None)
  raise SystemExit(f"Error: herramienta '{herramienta}' no soportada (claude | cursor)")


# ------------------------------------------------------------ formato de eventos
def _resumen_args(args) -> str:
  if isinstance(args, dict):
    for k in ("command", "file_path", "path", "pattern", "query", "url", "description", "prompt", "subagent_type"):
      if args.get(k):
        return str(args[k]).replace("\n", " ")[:140]
    return json.dumps(args, ensure_ascii=False)[:140]
  return str(args)[:140]


def formatear(ev: dict) -> list[str]:
  t = ev.get("type")
  out = []
  if t == "system" and ev.get("subtype") == "init":
    out.append(f"⚙️  inicio · modelo={ev.get('model')} · cwd={ev.get('cwd', '')}")
  elif t == "assistant":
    for c in (ev.get("message") or {}).get("content") or []:
      if c.get("type") == "text" and c.get("text", "").strip():
        out.append("💬 " + c["text"].strip().replace("\n", " ")[:400])
      elif c.get("type") == "tool_use":
        out.append(f"🔧 {c.get('name')}: {_resumen_args(c.get('input'))}")
  elif t == "tool_call" and ev.get("subtype") == "started":  # Cursor
    call = ev.get("tool_call") or {}
    for nombre, cuerpo in call.items():
      if not nombre.endswith("ToolCall"):  # metadatos: toolCallId, startedAtMs, hooks…
        continue
      args = cuerpo.get("args") if isinstance(cuerpo, dict) else cuerpo
      out.append(f"🔧 {nombre.replace('ToolCall', '')}: {_resumen_args(args)}")
  elif t == "result":
    dur = (ev.get("duration_ms") or 0) / 1000
    costo = ev.get("total_cost_usd")
    extra = f" · ${costo:.3f}" if isinstance(costo, (int, float)) else ""
    estado = "✅" if not ev.get("is_error") and ev.get("subtype", "success") == "success" else "❌"
    out.append(f"{estado} fin · {dur:.0f}s{extra}")
    if ev.get("result"):
      out.append("📋 RESUMEN FINAL:\n" + str(ev["result"]).strip())
  return out


def ejecutar(comando: str, argumentos: str = "", herramienta: str | None = None, modelo: str | None = None,
             dry_run: bool = False, silencioso: bool = False, etiqueta: str | None = None) -> int:
  cfg = cargar_config()
  rol = cfg["comandos"].get(comando)
  if not rol:
    raise SystemExit(f"Error: comando '{comando}' no está en agentes.yaml → comandos")
  herramienta = herramienta or cfg["roles"][rol]["herramienta"]
  modelo = modelo or cfg["roles"][rol]["modelos"][herramienta]
  prompt = construir_prompt(comando, argumentos)
  argv, stdin = construir_cmd(herramienta, modelo, prompt)
  etiqueta = etiqueta or f"{comando}{(':' + argumentos.split()[0]) if argumentos else ''}"
  cabecera = f"[{etiqueta} · {herramienta}/{modelo}]"

  if dry_run:
    print(f"{cabecera} DRY-RUN → {' '.join(argv[:6])} …")
    return 0
  if not shutil.which(argv[0]):
    print(f"{cabecera} Error: '{argv[0]}' no está en el PATH")
    return 127

  LOGS.mkdir(exist_ok=True)
  ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
  slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in etiqueta)[:60]
  log_path = LOGS / f"{ts}-{slug}-{herramienta}.log"
  raw_path = log_path.with_suffix(".jsonl")

  def emitir(linea: str, log):
    log.write(linea + "\n")
    log.flush()
    if not silencioso:
      print(f"{cabecera} {linea}", flush=True)

  with log_path.open("w", encoding="utf-8") as log, raw_path.open("w", encoding="utf-8") as raw:
    emitir(f"▶️  {comando} {argumentos}".strip() + f" · log: {log_path.relative_to(ROOT)}", log)
    proc = subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    if stdin:
      proc.stdin.write(stdin)
      proc.stdin.close()
    for linea in proc.stdout:
      raw.write(linea)
      linea = linea.strip()
      if not linea:
        continue
      try:
        eventos = formatear(json.loads(linea))
      except json.JSONDecodeError:
        eventos = [f"· {linea[:300]}"]
      for e in eventos:
        emitir(e, log)
    codigo = proc.wait()
    emitir(f"⏹️  código de salida {codigo}", log)
  return codigo


def verificar() -> int:
  cfg = cargar_config()
  errores = 0
  agy_validos = {"flash_lite", "flash", "pro", "inherit"}
  for rol, datos in cfg["roles"].items():
    agy_model = (datos.get("modelos") or {}).get("antigravity")
    if agy_model is not None and agy_model not in agy_validos:
      print(f"[ERROR] agentes.yaml → roles.{rol}.modelos.antigravity='{agy_model}' inválido (usa {sorted(agy_validos)})")
      errores += 1
    agente = ROOT / ".claude" / "agents" / f"{rol}.md"
    if not agente.exists():
      continue
    meta = yaml.safe_load(agente.read_text(encoding="utf-8").split("---")[1])
    if meta.get("model") != datos["modelos"]["claude"]:
      print(f"[ERROR] {agente.relative_to(ROOT)}: model={meta.get('model')} ≠ agentes.yaml ({datos['modelos']['claude']})")
      errores += 1
  for comando, rol in cfg["comandos"].items():
    if rol not in cfg["roles"]:
      print(f"[ERROR] comando {comando} → rol inexistente {rol}"); errores += 1
    if not (ROOT / ".claude" / "commands" / f"{comando}.md").exists():
      print(f"[ERROR] falta .claude/commands/{comando}.md"); errores += 1
  print("✓ agentes.yaml consistente" if not errores else f"✗ {errores} error(es)")
  return 1 if errores else 0


def main() -> int:
  p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  p.add_argument("comando", nargs="?")
  p.add_argument("argumentos", nargs="*")
  p.add_argument("--tool", choices=["claude", "cursor"])
  p.add_argument("--model")
  p.add_argument("--dry-run", action="store_true")
  p.add_argument("--quiet", action="store_true", help="solo escribe el log, sin eco en consola")
  p.add_argument("--verificar", action="store_true")
  a = p.parse_args()
  if a.verificar:
    return verificar()
  if not a.comando:
    p.error("falta el comando")
  return ejecutar(a.comando, " ".join(a.argumentos), a.tool, a.model, a.dry_run, a.quiet)


if __name__ == "__main__":
  sys.exit(main())
