---
description: Registra novedades de postulaciones, follow-ups y sincroniza el estado
---

# /tracker

1. Lee `AGENTS.md` (protocolo §2 y Modo Orquestador §3) y `agentes.yaml`.
2. Si es un cambio de estado simple sin redacción, el orquestador puede ejecutar directamente `python3 scripts/estado.py mover ...` y `sincronizar` (0 tokens).
3. Para Flujo A completo (follow-ups, `REVISAR`), Flujo B (brechas/radar) o Flujo C (Impact Log en `perfil-maestro.md`), delega mediante `invoke_subagent` (`TypeName: "self"`, `Role: "Perfil y Tracker"`, `Model: <agentes.yaml → roles.perfil-tracker.modelos.antigravity>`). El subagente lee `.agents/skills/perfil-tracker/SKILL.md`, ejecuta el flujo y reporta al orquestador.
4. Argumentos ([novedad, ej: 'postulé a OF-013' | 'me llamaron de Wazuh' | 'aprobé FOCP']): usa lo que Felipe escribió después de `/tracker`.
5. Verifica con `python3 scripts/estado.py validar` y `python3 scripts/estado.py sincronizar`, y entrega el resumen final.
