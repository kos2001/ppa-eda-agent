import hashlib, json, os, shutil, subprocess, tarfile, time
from pathlib import Path

root = Path('/home/kos2001/ppa-sram-bounded-20261015')
repo = Path('/mnt/c/Users/김오석/claude-work/ppa-eda-agent/.closure-followup')
archive = repo/'pipeline/designs/sram_wrapper/characterization/ngspice-bin-index'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
manifest = {'scope':'experimental simulator recovery; no full SRAM measurement', 'started_at':time.time(), 'commands':[]}
def save():
    (root/'recovery.json').write_text(json.dumps(manifest, indent=2)+'\n')
def run(argv, cwd, log, seconds):
    start = time.monotonic()
    with (root/log).open('w') as f:
        result = subprocess.run(['timeout','-k','10',str(seconds),*map(str,argv)], cwd=cwd, stdout=f, stderr=subprocess.STDOUT)
    manifest['commands'].append({'argv':list(map(str,argv)), 'cwd':str(cwd),'log':log,'exit_code':result.returncode,'wall_seconds':time.monotonic()-start,'timeout_s':seconds})
    save()
    if result.returncode: raise RuntimeError(log)
release=root/'ngspice-46.tar.gz'
assert sha(release)=='a0d1699af1940b06649276dcd6ff5a566c8c0cad01b2f7b5e99dedbb4d64c19b'
manifest['release_sha256']=sha(release)
patch=(archive/'ngspice-46.patch').read_bytes().replace(b'\r\n',b'\n')
assert hashlib.sha256(patch).hexdigest()=='1edebd83b170d588bfb55f7301a90ba7fb28bc867cf8c44f88e79efa4108aafc'
(root/'ngspice-46.patch').write_bytes(patch)
manifest['patch_lf_sha256']=sha(root/'ngspice-46.patch')
with tarfile.open(release) as tar: tar.extractall(root,filter='data')
source=root/'ngspice-46'
expected=json.loads((archive/'build-20261004.json').read_text())['original_source_sha256']
actual={p:sha(source/p) for p in expected}
assert expected==actual
manifest['original_source_sha256']=actual
flags=['--without-x','--disable-xspice','--enable-klu','--disable-openmp','--with-readline=no']
for kind in ('original','indexed'):
    if kind=='indexed': run(['patch','-p1','-i',root/'ngspice-46.patch'],source,'patch.log',30)
    build=root/('build-'+kind); build.mkdir()
    install=root/('install-'+kind)
    run([source/'configure','--prefix='+str(install),*flags],build,kind+'-configure.log',120)
    run(['make','-j4'],build,kind+'-make.log',600)
    run(['make','install'],build,kind+'-install.log',120)
    binary=install/'bin/ngspice'
    manifest[kind]={'path':str(binary),'sha256':sha(binary)}
    run([binary,'--version'],root,kind+'-version.log',10)
    run(['make','check'],build,kind+'-check.log',180)
    print(kind+' build complete',flush=True)
pdk=Path('/home/kos2001/claude_work/ppa-eda-agent/pdk')
for sub in ('sky130A/libs.tech/ngspice','sky130A/libs.ref/sky130_fd_pr/spice'):
    src=pdk/sub; dest=root/'pdk'/sub
    shutil.copytree(src,dest)
model_hashes={}
for p in sorted((root/'pdk').rglob('*')):
    if p.is_file():
        rel=p.relative_to(root/'pdk'); digest=sha(p)
        assert digest==sha(pdk/rel)
        model_hashes[str(rel)]=digest
(root/'model-hashes.json').write_text(json.dumps(model_hashes,indent=2)+'\n')
manifest['model_files']=len(model_hashes)
manifest['model_hash_manifest_sha256']=sha(root/'model-hashes.json')
common=['--original',manifest['original']['path'],'--indexed',manifest['indexed']['path']]
run(['python3',archive/'compare.py',*common,'--output-dir',root/'synthetic'],root,'synthetic.log',180)
run(['python3',archive/'compare_sky130.py',*common,'--pdk-root',root/'pdk','--output-dir',root/'sky130'],root,'sky130.log',420)
manifest['status']='controls_passed'
manifest['finished_at']=time.time()
save()
print(json.dumps(manifest,indent=2),flush=True)
