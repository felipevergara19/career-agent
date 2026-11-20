@AGENTS.md

## Notas específicas de Claude Code
- `.claude/skills/*` son symlinks a `.agents/skills/*`: edita siempre el original en `.agents/skills/`.
- `.claude/agents/*` son subagentes delgados por rol; úsalos para delegar etapas y para paralelizar `adaptador-cv` (un subagente por oferta).
- `.claude/commands/*` implementan `/buscar`, `/analizar`, `/adaptar-cv`, `/entrevista`, `/tracker` y `/corrida-diaria`.
- Herramientas web: `WebSearch` / `WebFetch`.
