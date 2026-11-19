from pathlib import Path
import json,re,hashlib
root=Path('/private/tmp/ppa-aes-closure-20261004/aes');run=root/'runs/aes-closure-20261004-targeted14-r2';sta=sorted(run.glob('*stapostpnr'))[-1];out=root/'spef-naming-replay';out.mkdir(exist_ok=False)
state=json.loads((sta/'state_in.json').read_text());changes={}
def host(path):return root/Path(path).relative_to('/design')
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
for corner,path in state['spef'].items():
 source=host(path);text=source.read_text();dnet=set(re.findall(r'^\*D_NET (\*\d+) ',text,re.M));lines=text.splitlines(keepends=True);mapping={};updated=[]
 for line in lines:
  m=re.fullmatch(r'(\*\d+) (fanout_repair_\d+)(\s*)',line)
  if m and m[1] in dnet:
   mapping[m[2]]=m[2].replace('fanout_repair_','fanout_repair_net_',1)
   line=f'{m[1]} {mapping[m[2]]}{m[3]}'
  updated.append(line)
 target=out/source.name;target.write_text(''.join(updated));state['spef'][corner]='/design/spef-naming-replay/'+target.name
 # Only NAME_MAP names change: all RC topology and numeric values identical.
 inverse=target.read_text()
 for old,new in mapping.items():inverse=re.sub(r'(?m)^(\*\d+) '+re.escape(new)+r'(\s*)$',lambda m:m[1]+' '+old+m[2],inverse)
 assert inverse==text
 if changes:assert mapping==changes
 changes=mapping
for key in ['nl','pnl']:
 source=host(state[key]);text=source.read_text();instances=re.findall(r'\b(fanout_repair_\d+)\s*\(',text)
 assert set(changes)<=set(instances),('instances missing',set(changes)-set(instances))
 pattern=re.compile(r'\b(fanout_repair_\d+)\b(?!\s*\()')
 new=pattern.sub(lambda m:changes.get(m[1],m[1]),text)
 assert re.findall(r'\b(fanout_repair_\d+)\s*\(',new)==instances
 inverse=re.sub(r'\b(fanout_repair_net_\d+)\b',lambda m:m[0].replace('fanout_repair_net_','fanout_repair_',1),new);assert inverse==text
 target=out/source.name;target.write_text(new);state[key]='/design/spef-naming-replay/'+target.name
(out/'state_in.json').write_text(json.dumps(state,indent=2)+'\n');(out/'config.json').write_bytes((sta/'config.json').read_bytes())
(out/'provenance.json').write_text(json.dumps({'scope':'STA-only naming correction; routed geometry, RC values, cell connections and constraints unchanged','original_sta_step':str(sta),'renamed_nets':changes,'inverse_transform_byte_exact':True,'files_sha256':{p.name:digest(p) for p in out.iterdir() if p.is_file()}},indent=2)+'\n');print('Prepared naming replay for',len(changes),'repair nets')
