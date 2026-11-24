---
description: Responde preguntas de formularios y portales de empleo para una postulación específica
---

1. Identificar la oferta objetivo en `ofertas/` usando su ID o slug.
2. Identificar las preguntas del formulario ingresadas por el usuario.
3. Consultar los logros del grafo (`scripts/evaluador_ia.py` / Neo4j) y los datos comprobados de `perfil/perfil-maestro.md`.
4. Ejecutar el asistente de postulación:
   ```bash
   python3 scripts/asistente_postulacion.py --oferta <id_oferta> --preguntas "<preguntas>"
   ```
5. Validar que las respuestas cumplan con la regla de veracidad estricta (sin herramientas inventadas, métricas exactas de Colektia, expectativas salariales correctas).
6. Confirmar la actualización de `postulaciones/material/<id_oferta>.md`.
