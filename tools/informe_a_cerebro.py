"""
Copia los informes semanales del experimento SMC (publicados en el estado del paper trading) al vault de Obsidian.

Crea una nota por semana en  <vault>/01-Proyectos/Experimento SMC agentes/Informe SMC AAAA-MM-DD.md  (si no existe ya)
y una nota índice con enlaces. No borra ni modifica notas existentes.

Uso: python tools/informe_a_cerebro.py [ruta_del_vault]     (por defecto C:/Users/AGOME/Documents/Cerebro)
"""
import json, sys, urllib.request
from pathlib import Path

URL = "https://raw.githubusercontent.com/Gxmezz/paper-trading-trend-breakout/gh-pages/state.json"
VAULT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("C:/Users/AGOME/Documents/Cerebro")
DIR = VAULT / "01-Proyectos" / "Experimento SMC agentes"

def fmt_acc(a):
    if not a["operaciones"]: return "sin operaciones"
    return (f"{a['operaciones']} operaciones · acierto {a['acierto_pct']} % · PF {a['PF'] if a['PF'] is not None else '—'} · "
            f"R medio {a['R_medio']:+.2f} · {a['resultado_eur']:+.2f} €")

def note(r):
    st = r["stats"]; m = st["coincidencia_entradas"]; g = st["gestion"]["acciones"]
    alerts = "\n".join(f"- {a}" for a in r.get("alertas", [])) or "- Ninguna"
    risk = ", ".join(f"{x['dia']}: {x['modo']}" + (f" ×{x['mult']}" if x["modo"] == "reducido" else "") for x in st["riesgo"]) or "sin revisiones"
    return f"""---
tipo: informe
creado: {r['created'][:10]}
proyecto: "[[Trading algoritmico]]"
tags: [trading, smc, agentes, informe]
---
# Informe SMC semana {st['semana']}

## Resumen
{r.get('resumen', '')}

**Conclusión provisional:** {r.get('conclusion', '')}

## Alertas
{alerts}

## Cifras de la semana
| | Agentes | Gemela mecánica |
|---|---|---|
| Semana | {fmt_acc(st['agentes_semana'])} | {fmt_acc(st['gemela_semana'])} |
| Desde el inicio | {fmt_acc(st['agentes_total'])} · capital {st['agentes_total']['capital_eur']} € · caída máx. {st['agentes_total']['caida_max_pct']} % | {fmt_acc(st['gemela_total'])} · capital {st['gemela_total']['capital_eur']} € · caída máx. {st['gemela_total']['caida_max_pct']} % |

- Setups de la semana: {st['setups_semana']} · ambos entran {m['ambos_entran']} · solo el agente {m['solo_agente']} · solo la gemela {m['solo_gemela']} · ninguno {m['ninguno']}
- Errores del agente: {st['errores_agente']} · rechazos del guardián: {st['rechazos_guardian']}
- Gestión: {st['gestion']['revisiones']} revisiones · stop {g['mover_stop']} · BE {g['break_even']} · objetivos {g['cambiar_objetivos']} · parciales {g['cerrar_parcial']} · cierres {g['cerrar']}
- Riesgo: {risk}
- Gasto de la API: {st['gasto_api_semana_usd']} $ esta semana · {st['gasto_api_total_usd']} $ en total
- Progreso hacia la evaluación: {st['progreso']['operaciones_agentes']} de {st['progreso']['objetivo_operaciones']} operaciones · {st['progreso']['dias']} de {st['progreso']['objetivo_dias']} días
"""

def main():
    with urllib.request.urlopen(URL, timeout=30) as f: s = json.load(f)
    reps = s.get("reports", [])
    DIR.mkdir(parents=True, exist_ok=True)
    new = 0
    for r in reps:
        p = DIR / f"Informe SMC {r['week']}.md"
        if p.exists(): continue
        p.write_text(note(r), encoding="utf-8"); new += 1
    idx = DIR / "Experimento SMC agentes.md"
    names = sorted((p.stem for p in DIR.glob("Informe SMC *.md")), reverse=True)
    idx.write_text("---\ntipo: indice\ncreado: 2026-11-12\ntags: [trading, smc, agentes]\n---\n# Experimento SMC agentes\n\n"
                   "Informes semanales del experimento (agentes frente a gemela mecánica). Proyecto: [[Trading algoritmico]].\n"
                   "Dashboard: https://gxmezz.github.io/paper-trading-trend-breakout/\n\n"
                   + "\n".join(f"- [[{n}]]" for n in names) + "\n", encoding="utf-8")
    print(f"Informes publicados: {len(reps)} · nuevos en Cerebro: {new} · carpeta: {DIR}")

if __name__ == "__main__":
    main()
