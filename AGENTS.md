# Career Agent System — Felipe Vergara Peñaloza

Sistema multi-agente de carrera profesional para **Felipe Vergara Peñaloza** (*Cloud FinOps & DevSecOps Engineer*).
Este archivo es la **única fuente de verdad** para reglas globales y protocolo. Lo leen todas las herramientas:

| Herramienta | Cómo carga este archivo | Roles / comandos |
| :--- | :--- | :--- |
| Google Antigravity | `GEMINI.md` y `.antigravity/rules.md` → symlink a `AGENTS.md` | Skills en `.agents/skills/`, workflows en `.agent/workflows/` |
| Claude Code (`claude`) | `CLAUDE.md` importa `@AGENTS.md` | `.claude/skills/` (symlinks), `.claude/agents/` (con `model:` por rol), `.claude/commands/` |
| Cursor (IDE y `cursor-agent` CLI) | `AGENTS.md` nativo + `.cursor/rules/career-agent.mdc` (symlink) | `.cursor/commands` → symlink a `.claude/commands` |
| Gemini CLI (opcional) | `.gemini/settings.json` → `context.fileName: AGENTS.md` | `.gemini/commands/*.toml` |

Herdr es el multiplexor de terminal desde el que se operan estas consolas en paralelo.

**Regla de oro:** la lógica de negocio de cada rol vive **solo** en `.agents/skills/<rol>/SKILL.md`. Los subagentes, comandos y workflows de cada herramienta son envoltorios de pocas líneas que apuntan a ese archivo. Si cambias un procedimiento, cámbialo ahí; nunca en un envoltorio.

---

## 1. Estructura

| Ruta | Contenido |
| :--- | :--- |
| [perfil/perfil-maestro.md](perfil/perfil-maestro.md) | Stack (🟢/🟡/🔴), *Impact Log*, certificaciones, idiomas. Única fuente para redactar CVs. |
| [perfil/preferencias.md](perfil/preferencias.md) | Filtros duros, metas salariales y **rúbrica de puntaje 0–100**. |
| `ofertas/YYYY-MM-DD/<empresa-slug>-<rol-slug>.md` | Una ficha por oferta. **Su frontmatter YAML es el estado canónico de esa oferta.** |
| `cvs-generados/YYYY-MM-DD/cv-felipe-vergara-<slug>.{md,html,pdf}` | CVs adaptados. `cvs-generados/base/` = CV maestro. |
| `entrevistas/<id-oferta>.md` | Guías de preparación de entrevista. |
| `postulaciones/material/<id-oferta>.md` | Material de postulación (cover letter, nota LinkedIn, respuestas de formulario). |
| [agentes.yaml](agentes.yaml) | Matriz **rol → herramienta → modelo** (fuente única para lanzador, pipeline y subagentes). |
| [postulaciones/tracker.md](postulaciones/tracker.md) | Tablero **generado** (no editar a mano). |
| `postulaciones/estado-postulaciones.csv` | Índice plano **generado** de todas las ofertas y su etapa. |
| [postulaciones/mejoras-perfil.md](postulaciones/mejoras-perfil.md) | Radar de mejora continua (certificaciones, backlog, brechas de mercado). |
| `postulaciones/datos-historial.json` | `ofertas` = **generado**; `radar_skills` y `efectividad_portales` = escritos por `perfil-tracker`. |
| `postulaciones/*.xlsx`, `ofertas-y-postulaciones.csv`, `radar-mercado-skills.csv` | **Generados**. |
| [scripts/estado.py](scripts/estado.py) | Motor de estado: `validar`, `sincronizar`, `listar [--json]`, `existe`, `mover`. |
| [scripts/rastrear_ats.py](scripts/rastrear_ats.py) | Rastreador directo a 0 tokens contra APIs públicas de ATS (Greenhouse, Ashby, Lever) usando [perfil/empresas-target.yaml](perfil/empresas-target.yaml). |
| [scripts/ensamblar_cv.py](scripts/ensamblar_cv.py) | Ensamblador determinista de CVs a 0 tokens por ángulo + render HTML/PDF. |
| [scripts/lanzar.py](scripts/lanzar.py) | Lanza un comando como agente headless (Claude Code o Cursor) con el modelo de `agentes.yaml`; log legible en `logs/`. |
| [scripts/pipeline.py](scripts/pipeline.py) | Orquestador determinista multi-herramienta (0 tokens por defecto; `--web`/`--llm-cv` para LLMs → sincronizar → Drive). |
| [scripts/drive_sync.py](scripts/drive_sync.py) | Subida incremental a Google Drive vía rclone (manifiesto `postulaciones/.drive-sync.json`). |

---

## 2. Protocolo multi-agente (contrato obligatorio)

### 2.1 Fuente de verdad e ID
- Cada oferta es **un archivo**; su frontmatter manda. Todo lo demás se regenera con `python3 scripts/estado.py sincronizar`.
- **ID determinista** = `<carpeta-fecha>--<nombre-archivo-sin-.md>` (ej. `2026-11-16--aloware-automation-n8n-ai-engineer`). Nunca inventes IDs correlativos (`OF-0XX`); `legacy_id` existe solo para ofertas antiguas.
- Slugs: minúsculas, `a-z0-9` y guiones, sin tildes.

### 2.2 Frontmatter de una oferta

```yaml
---
tipo: oferta
id: 2026-11-16--empresa-rol
legacy_id: null
empresa: Empresa
cargo: Título exacto del rol
url: https://...            # link directo para postular
portal: LinkedIn / Get on Board / Careers
fecha_captura: '2026-11-16' # = carpeta
modalidad: 100% Remoto (LatAm / EST)
idioma: Inglés (C1)
salario: $3,000 - $4,000 USD/mes   # o "Sueldo por confirmar"
match_score: 85             # entero 0–100 (null solo si descartada sin evaluar)
prioridad: alta             # alta (>=80) | media (65–79) | stretch | descartada
estado: evaluada            # ver 2.3
angulo: finops              # finops | devsecops | secops-siem | ai-automation | base | null
cv: null                    # ruta al .md del CV asignado
motivo_descarte: null       # obligatorio si estado = descartada
keywords: []
brechas: []
notas: ''
fecha_postulacion: null     # se completa al pasar a postulado
actualizado: '2026-11-16'
# opcionales: prep_entrevista (ruta), revisar (texto de alerta)
---
```

Frontmatter de un CV: `tipo: cv`, `angulo`, `idioma: en|es`, `base: true|false`, `generado: 'YYYY-MM-DD'`. `render_cv.py` lo omite al renderizar.

### 2.3 Estados y transiciones

```
evaluada ──► cv_generado ──► postulado ──► entrevista_hr ──► entrevista_tecnica ──► oferta
    │             │              │                │                    │
    └─────────────┴──────────────┴────► descartada (nosotros) / rechazada (la empresa)
```
- Cambia estados **solo** con `python3 scripts/estado.py mover <id|legacy_id> <estado> [--cv RUTA] [--prep RUTA] [--motivo TXT] [--nota TXT]`. El script valida la transición, completa `fecha_postulacion` y usa un lock.
- `cv_generado` en adelante exige `cv`. `descartada` exige `motivo_descarte`.

### 2.4 Propiedad de archivos (quién escribe qué)

| Archivo | Escribe | Los demás |
| :--- | :--- | :--- |
| `ofertas/<fecha>/*.md` (crear) | `buscador-ofertas` | Solo cambian estado vía `estado.py mover` |
| `cvs-generados/**`, `postulaciones/material/**` | `adaptador-cv` | Lectura |
| `entrevistas/**` | `preparador-entrevistas` | Lectura |
| `perfil/*.md`, `postulaciones/mejoras-perfil.md`, `datos-historial.json` (`radar_skills`, `efectividad_portales`) | `perfil-tracker` | Lectura; proponen cambios en su resumen final |
| `tracker.md`, `estado-postulaciones.csv`, `datos-historial.json` (`ofertas`), XLSX/CSV | **Nadie a mano** → `estado.py sincronizar` | — |

### 2.5 Reglas de concurrencia (varios agentes en paralelo, p. ej. paneles de Herdr)
1. Antes de crear una oferta, deduplica: `python3 scripts/estado.py existe "<url o empresa>"`.
2. Un agente = un conjunto de archivos propios. Nunca edites un archivo cuyo dueño es otro rol.
3. Los CVs nuevos se nombran por oferta (`cv-felipe-vergara-<slug-oferta>.md`), así dos `adaptador-cv` en paralelo nunca chocan. Reutilizar un CV existente = solo apuntarlo con `--cv`, sin modificarlo.
4. `estado.py mover` y `sincronizar` toman un lock (`.locks/estado.lock`). Si devuelven código 2, reintenta.
5. **Definición de terminado de cualquier etapa:** `python3 scripts/estado.py validar` sin errores → `python3 scripts/estado.py sincronizar`.

---

## 3. Roles (`.agents/skills/`)

| Rol | Skill | Entrada | Salida | Estado que deja |
| :--- | :--- | :--- | :--- | :--- |
| Buscador / Analista de ofertas | [buscador-ofertas](.agents/skills/buscador-ofertas/SKILL.md) | `perfil/*`, `estado-postulaciones.csv` · (modo puntual: URL o texto) | `ofertas/<hoy>/<slug>.md` | `evaluada` o `descartada` |
| Adaptador de CV | [adaptador-cv](.agents/skills/adaptador-cv/SKILL.md) | ID de oferta + `perfil-maestro.md` | `cvs-generados/<hoy>/cv-...{md,html,pdf}` + `postulaciones/material/<id>.md` | `cv_generado` (+ `material`) |
| Preparador de entrevistas | [preparador-entrevistas](.agents/skills/preparador-entrevistas/SKILL.md) | ID de oferta + su CV + perfil | `entrevistas/<id>.md` | (no cambia estado; agrega `prep_entrevista`) |
| Perfil y seguimiento | [perfil-tracker](.agents/skills/perfil-tracker/SKILL.md) | Novedades de Felipe, todas las ofertas | Perfil, mejoras, radar, sincronización | `postulado`, `entrevista_*`, `oferta`, `rechazada` |

### Comandos (mismos nombres en todas las herramientas)

| Comando | Rol | Qué hace |
| :--- | :--- | :--- |
| `/buscar` | buscador-ofertas | Búsqueda diaria completa |
| `/analizar <url o texto>` | buscador-ofertas (modo puntual) | Evalúa una sola oferta con la rúbrica y la registra |
| `/adaptar-cv <id>` | adaptador-cv | Genera CV adaptado para una oferta |
| `/entrevista <id>` | preparador-entrevistas | Guía de preparación de entrevista |
| `/tracker [novedad]` | perfil-tracker | Registra novedades, revisa follow-ups y sincroniza |
| `/corrida-diaria` | todos | Flujo completo (sección 4) |
| `/pipeline [foco] [max N] [sin-busqueda] [drive]` | director | Igual que la corrida, sin intervención entre etapas, CVs en paralelo y subida a Drive |

### Modelos por rol (fuente: [agentes.yaml](agentes.yaml))

| Rol | Tipo de tarea | Antigravity (`invoke_subagent`) | Claude Code | Cursor | Herramienta por defecto (headless) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| perfil-tracker | Mecánica (estados, KPIs) | `flash_lite` | `haiku` | `composer-2.5-fast` | Cursor |
| buscador-ofertas | Volumen web + rúbrica | `flash` | `sonnet` | `gpt-5.5-medium` | Cursor |
| adaptador-cv | Redacción ATS / material | `flash` *(o `pro` si se pide)* | `sonnet` | `claude-sonnet-5-thinking-high` | Claude Code |
| preparador-entrevistas | Razonamiento profundo | `pro` | `opus` | `claude-opus-5-5-medium` | Claude Code |
| director (`/pipeline`) | Orquestación | `inherit` (sesión principal) | `sonnet` | `auto` | Claude Code |

Para cambiar un modelo, edita `agentes.yaml`, actualiza el `model:` del subagente en `.claude/agents/` (si aplica a Claude) y comprueba con `python3 scripts/lanzar.py --verificar`.

### Modo Orquestador en Google Antigravity (eficiencia y control de costos)

Cuando se opera desde **Google Antigravity**, la sesión principal actúa **exclusivamente como Orquestador (`director`)** siguiendo esta jerarquía de costo:

1. **Nivel 0 — Scripts deterministas (0 tokens, siempre primero):**
   - Validación/movimiento/sincronización: `python3 scripts/estado.py ...`
   - Rastreo directo de ATS oficiales: `python3 scripts/rastrear_ats.py --buscar --crear-ofertas`
   - Ensamblado estándar de CVs: `python3 scripts/ensamblar_cv.py --oferta <id>`
2. **Nivel 1 — Subagentes más baratos (`invoke_subagent` con `Model` de `agentes.yaml`):**
   - Cuando una tarea requiere razonamiento LLM (búsqueda web abierta, evaluación con rúbrica, personalización profunda de CV + material, actualización de brechas/radar o preparación de entrevistas), el orquestador **NO** la ejecuta en su propio contexto caro: delega en un subagente usando `invoke_subagent` (`TypeName: "self"`, `Model` exacto de `agentes.yaml → roles.<rol>.modelos.antigravity`).
   - Cada subagente lee `.agents/skills/<rol>/SKILL.md`, respeta la propiedad de archivos (§2.4), ejecuta su trabajo y responde al orquestador con un resumen estructurado.
   - Si hay varias tareas independientes (p. ej. 2–3 CVs en paralelo), el orquestador puede lanzar varios subagentes simultáneos (`flash`) y luego consolidar con `estado.py validar && estado.py sincronizar`.
3. **Nivel 2 — Modelo del orquestador (solo coordinación y síntesis):**
   - El orquestador decide qué etapa correr, valida los hand-offs, resuelve bloqueos y entrega el resumen ejecutivo final a Felipe.

### Tres formas de operar

1. **Orquestador en Antigravity (recomendado para uso diario):** pides la tarea en la sesión principal; el orquestador ejecuta primero los scripts a 0 tokens y delega cada rol LLM en subagentes con el tier definido en `agentes.yaml → modelos.antigravity`.
2. **Interactiva por paneles (Herdr) o Headless:** `python3 scripts/lanzar.py <comando> [args] [--tool claude|cursor] [--model M]`. Síguelos en vivo con `tail -f logs/*.log`.
3. **Pipeline automático CLI:** `python3 scripts/pipeline.py` (0 tokens por defecto; agrega `--web` y/o `--llm-cv` para incluir agentes externos).

---

## 4. Corrida diaria

Cuando Felipe pida **"Ejecuta la corrida diaria"** o `/corrida-diaria`:
1. **perfil-tracker** (Flujo A): `estado.py validar`, revisa follow-ups (>7 días en `postulado`) y pendientes `revisar`.
2. **buscador-ofertas**: busca vacantes 100% remotas de las últimas 24–72 h, deduplica con `estado.py existe` y crea fichas en `ofertas/<hoy>/`.
3. **adaptador-cv** para cada oferta `prioridad: alta` sin CV (`estado.py listar --prioridad alta --sin-cv`): CV + material de postulación. Puede correr en paralelo, una oferta por agente.
4. **perfil-tracker** (Flujo B): actualiza brechas de mercado y `radar_skills`; luego `estado.py sincronizar`.
5. Entrega a Felipe un resumen ejecutivo: ofertas nuevas por prioridad, CVs generados, follow-ups pendientes y links directos para postular.

---

## 5. Reglas globales
- **Veracidad estricta (archivos y chat):** ningún agente inventa experiencia, métricas, años, certificaciones ni ejemplos/anécdotas fuera de `perfil/perfil-maestro.md`, **tampoco al responder preguntas en el chat o en formularios de postulación**. Si falta un dato, caso real o ejemplo específico para responder una pregunta (por ejemplo, de entrevista o formulario), **pregunta primero a Felipe** en vez de asumir, extrapolar o fabricar una historia plausible.
- Usa rutas **relativas** al repo en todos los archivos (nada de `file:///home/...`).
- Herramientas web: usa la herramienta de búsqueda/lectura web que exponga tu entorno (Antigravity `search_web`, Claude Code `WebSearch`/`WebFetch`, Cursor `web_search`/`fetch`, Gemini CLI `google_web_search`/`web_fetch`).
- Google Drive: solo con `scripts/drive_sync.py` y el remoto rclone personal (`gdrive-career`). Nunca uses credenciales ni cuentas de Colektia.
- Datos personales (teléfono, email) viven solo en `perfil/perfil-maestro.md`, `postulaciones/mejoras-perfil.md` y los CVs. Este repo no tiene remote; no lo publiques sin revisarlo.
- Aplican además las reglas del workspace en `../AGENTS.md`: léelo al iniciar si tu herramienta no lo cargó (Gemini CLI y Antigravity se detienen en la raíz git de este repo). Resumen: sin credenciales de Colektia, nunca leer `.env` ni `~/.ssh`.
- Versionado: trabaja en ramas `feat/...` / `fix/...` y haz commit al final de cada corrida.
