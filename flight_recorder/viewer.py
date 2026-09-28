"""Generate a self-contained HTML timeline viewer."""

from __future__ import annotations

import json
from typing import Any

from .diffing import compare_events


def _script_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def render_viewer(
    run_id: str,
    events: list[dict[str, Any]],
    compare_run_id: str | None = None,
    compare_events_data: list[dict[str, Any]] | None = None,
) -> str:
    compare = compare_events(events, compare_events_data or []) if compare_run_id else None
    data = {"run_id": run_id, "events": events, "compare_run_id": compare_run_id, "comparison": compare}
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent Flight Recorder timeline</title>
<style>
body{font:15px/1.5 system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#172033;background:#f7f8fb}
h1{margin-bottom:.25rem}.honesty{padding:1rem;border-left:4px solid #b35b00;background:#fff4e5}
.filters{display:flex;gap:.75rem;flex-wrap:wrap;margin:1rem 0}select,input{padding:.55rem;border:1px solid #bbc4d3;border-radius:6px}
article{background:white;border:1px solid #d9deea;border-radius:8px;margin:.7rem 0;padding:1rem}
article.changed{border-color:#c53a3a;background:#fffafa}article h2{font-size:1rem;margin:0 0 .4rem}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f3f8;padding:.75rem;border-radius:5px}
.mut{color:#405a9c}.meta{color:#566176;font-size:.9rem}
</style></head><body>
<h1>Agent Flight Recorder</h1>
<p class="honesty"><strong>Demo honesty:</strong> The bundled sample agent and incident runs are scripted fixtures. They make no real LLM calls and do not represent production incidents. The recorder can be integrated with other agents.</p>
<p id="runline"></p><div class="filters">
<label>Kind <select id="kind"><option value="">All kinds</option></select></label>
<label>Actor <select id="actor"><option value="">All actors</option></select></label>
<label>Search payload <input id="search" type="search"></label></div>
<p id="summary"></p><main id="timeline"></main>
<script>
const DATA=__DATA__;
const events=DATA.events, kind=document.querySelector('#kind'), actor=document.querySelector('#actor');
document.querySelector('#runline').textContent='Run '+DATA.run_id+(DATA.compare_run_id?' compared with '+DATA.compare_run_id:'');
for(const [select,key] of [[kind,'kind'],[actor,'actor']]) for(const value of [...new Set(events.map(e=>e[key]))].sort()){const o=document.createElement('option');o.value=value;o.textContent=value;select.append(o)}
const changed=new Set((DATA.comparison?.rows||[]).map(row=>row.seq));
function render(){const query=document.querySelector('#search').value.toLowerCase();const rows=events.filter(e=>(!kind.value||e.kind===kind.value)&&(!actor.value||e.actor===actor.value)&&(!query||JSON.stringify(e.payload).toLowerCase().includes(query)));
document.querySelector('#summary').textContent=rows.length+' of '+events.length+' events'+(DATA.comparison?' | '+DATA.comparison.divergent_count+' differing positions':'');
const root=document.querySelector('#timeline');root.replaceChildren();for(const e of rows){const card=document.createElement('article');if(changed.has(e.seq))card.className='changed';const title=document.createElement('h2');title.textContent='#'+e.seq+' '+e.kind;const meta=document.createElement('div');meta.className='meta';meta.textContent=e.ts+' | actor: '+e.actor+' | payload sha256: '+e.payload_hash;const payload=document.createElement('pre');payload.textContent=JSON.stringify(e.payload,null,2);card.append(title,meta,payload);if(e.kind==='state_mutation'){const diff=document.createElement('div');diff.className='mut';diff.textContent='State diff: '+e.payload.path+' changed from '+JSON.stringify(e.payload.before)+' to '+JSON.stringify(e.payload.after);card.append(diff)}root.append(card)}}
kind.addEventListener('change',render);actor.addEventListener('change',render);document.querySelector('#search').addEventListener('input',render);render();
</script></body></html>""".replace("__DATA__", _script_json(data))
