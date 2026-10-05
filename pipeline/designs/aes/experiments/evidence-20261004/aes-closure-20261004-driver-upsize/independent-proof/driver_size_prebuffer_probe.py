import sys,json
from pathlib import Path
import odb
sys.path.insert(0,'/flows')
from driver_size import size_drivers
db=odb.dbDatabase.create();odb.read_db(db,'/design/runs/aes-closure-20261004-targeted14-r2/41-openroad-repairantennas/1-diodeinsertion/aes_cipher_top.odb')
b=db.getChip().getBlock()
before={(inst.getName(),t.getMTerm().getName()):t.getNet().getName() if t.getNet() else None for inst in b.getInsts() for t in inst.getITerms()}
result=size_drivers(db,{'fanout901':'sky130_fd_sc_hd__buf_8','_20258_':'sky130_fd_sc_hd__o2bb2ai_4'})
after={(inst.getName(),t.getMTerm().getName()):t.getNet().getName() if t.getNet() else None for inst in b.getInsts() for t in inst.getITerms()}
assert before==after
result['checked_original_pins']=len(before)
Path('/design/driver_size_prebuffer_probe.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
