from pathlib import Path
import argparse,subprocess,re,json,time
ap=argparse.ArgumentParser(description='Compare two actual SKY130 MOS devices at each signoff PVT')
ap.add_argument('--original',type=Path,required=True)
ap.add_argument('--indexed',type=Path,required=True)
ap.add_argument('--pdk-root',type=Path,required=True)
ap.add_argument('--output-dir',type=Path,required=True)
args=ap.parse_args()
root=args.output_dir.resolve();root.mkdir(parents=True,exist_ok=False)
bins={'original':str(args.original.resolve()),'indexed':str(args.indexed.resolve())};result={}
lib=args.pdk_root.resolve()/'sky130A/libs.tech/ngspice/sky130.lib.spice'
assert lib.is_file(),lib
for corner,voltage,temp in [('ss',1.6,100),('tt',1.8,25),('ff',1.95,-40)]:
 p=root/f'sky130-{corner}.sp';p.write_text(f'SKY130 PVT transistor equivalence\n.lib "{lib}" {corner}\n.temp {temp}\nvd d 0 {voltage}\nvg g 0 {voltage}\nx1 d g 0 0 sky130_fd_pr__nfet_01v8 w=1.26 l=.15\nx2 d g 0 0 sky130_fd_pr__nfet_01v8 w=.42 l=.5\n.options KLU\n.control\nset numdgt=15\nop\nprint i(vd)\nquit\n.endc\n.end\n')
 row={}
 for kind,binary in bins.items():
  t=time.monotonic();r=subprocess.run([binary,'-b',str(p)],capture_output=True,text=True,timeout=60);(root/f'sky130-{corner}-{kind}.log').write_text(r.stdout+r.stderr)
  c=re.findall(r'i\(vd\)\s*=\s*([-+\d.eE]+)',r.stdout);assert r.returncode==0 and c,(kind,corner,r.stderr[-1000:])
  row[kind]={'current_A':float(c[-1]),'wall_seconds':time.monotonic()-t}
 assert abs(row['original']['current_A']-row['indexed']['current_A'])<1e-12,row
 result[corner]=row
(root/'sky130-result.json').write_text(json.dumps({'scope':'two MOS devices per PVT; not SRAM functional validation','cases':result},indent=2)+'\n');print(json.dumps(result,indent=2))
