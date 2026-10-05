import argparse,subprocess,time,re,json,hashlib
from pathlib import Path
ap=argparse.ArgumentParser(description='Compare numeric-bin selection with an unmodified ngspice 46')
ap.add_argument('--original',type=Path,required=True)
ap.add_argument('--indexed',type=Path,required=True)
ap.add_argument('--output-dir',type=Path,required=True)
args=ap.parse_args()
root=args.output_dir.resolve();root.mkdir(parents=True,exist_ok=False)
bins={'original':str(args.original.resolve()),'indexed':str(args.indexed.resolve())}
model=lambda n,lo,hi,v:f'.model {n} nmos level=54 version=4.8.2 lmin={lo} lmax={hi} wmin=1u wmax=100u vth0={v}\n'
cases={
 'different_bins':model('n.1','0.1u','0.9u','.3')+model('n.2','0.9u','4u','.8'),
 'overlap_original_order':model('n.1','0.1u','4u','.3')+model('n.2','0.1u','4u','.8'),
 'overlap_reversed_order':model('n.2','0.1u','4u','.8')+model('n.1','0.1u','4u','.3'),
 'duplicate_exact_model':model('n.1','0.1u','4u','.3')+model('n.1','0.1u','4u','.8'),
 'irrelevant_prefix':model('n.1','0.1u','4u','.3')+model('nn.2','0.1u','4u','.9')+model('n.bad','0.1u','4u','.8')}
results={}
for name,models in cases.items():
 p=root/(name+'.sp');p.write_text('Model-bin equivalence '+name+'\n'+models+'vd d 0 1\nvg g 0 1\nm1 d g 0 0 n w=2u l=.5u\nm2 d g 0 0 n w=4u l=1u nf=2\n.options KLU\n.control\nset numdgt=15\nop\nprint i(vd)\nquit\n.endc\n.end\n')
 row={}
 for kind,binary in bins.items():
  begin=time.monotonic();r=subprocess.run([binary,'-b',str(p)],capture_output=True,text=True,timeout=60)
  (root/(name+'-'+kind+'.log')).write_text(r.stdout+r.stderr)
  current=re.findall(r'i\(vd\)\s*=\s*([-+\d.eE]+)',r.stdout)
  assert r.returncode==0 and current,(name,kind,r.returncode,r.stdout[-1000:],r.stderr[-1000:])
  row[kind]={'current_A':float(current[-1]),'wall_seconds':time.monotonic()-begin}
 assert abs(row['original']['current_A']-row['indexed']['current_A'])<=1e-12,(name,row)
 results[name]=row
(root/'result.json').write_text(json.dumps({'scope':'synthetic model-bin selection, not SRAM characterization','simulators':{k:{'path':v,'sha256':hashlib.sha256(Path(v).read_bytes()).hexdigest()} for k,v in bins.items()},'cases':results},indent=2)+'\n')
print(json.dumps(results,indent=2))
