---
description: 'Flujo completo: tracker → búsqueda → CVs → brechas → resumen'
---

Ejecuta la corrida diaria definida en `AGENTS.md` §4, etapa por etapa, leyendo la skill de cada rol en `.agents/skills/<rol>/SKILL.md` antes de ejecutarla. Respeta la propiedad de archivos (§2.4) y cierra con `python3 scripts/estado.py validar` y `python3 scripts/estado.py sincronizar`.

En Claude Code: delega cada etapa al subagente homónimo de `.claude/agents/`; en la etapa 3 lanza un subagente `adaptador-cv` por oferta, en paralelo. Ejecuta tú `estado.py sincronizar` una vez al final.
