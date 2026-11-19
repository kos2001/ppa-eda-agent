import sys,json
from pathlib import Path
from openroad import Tech,Design
sys.path.insert(0,'/flows')
from macro_pin_buffer import buffer_macro_pins
tech=Tech();design=Design(tech);design.readDb('/design/runs/sram-closure-20261004-fanout/final/odb/sram_wrapper.odb')
b=tech.getDB().getChip().getBlock()
original={(t.getInst().getName(),t.getMTerm().getName()):t.getNet().getName() for inst in b.getInsts() for t in inst.getITerms() if t.getNet() is not None}
pins=[f'u_sram/addr{port}[{bit}]' for port in range(2) for bit in range(8)]+[f'u_sram/din0[{bit}]' for bit in range(8)]+['u_sram/web0']
result=buffer_macro_pins(tech.getDB(),pins,'sky130_fd_sc_hd__inv_16',2.0,'sky130_fd_sc_hd__inv_8')
checked=0
for inst in b.getInsts():
 if inst.getName().startswith(('macro_pin_buffer_','macro_pin_prebuffer_')):continue
 for term in inst.getITerms():
  key=(inst.getName(),term.getMTerm().getName())
  if key not in original:continue
  net=term.getNet();assert net is not None
  inversions=0
  while net.getName().startswith(('macro_pin_buffer_','macro_pin_prebuffer_')):
   drivers=[t for t in net.getITerms() if str(t.getIoType())=='OUTPUT'];assert len(drivers)==1
   ins=[t for t in drivers[0].getInst().getITerms() if str(t.getIoType())=='INPUT'];assert len(ins)==1
   assert drivers[0].getInst().getMaster().getName() in ['sky130_fd_sc_hd__inv_8','sky130_fd_sc_hd__inv_16']
   inversions+=1;net=ins[0].getNet()
  assert inversions in [0,2]
  assert net.getName()==original[key],key
  checked+=1
result['connectivity_checked_original_pins']=checked
Path('/design/macro_inverter_connectivity_probe.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'inserted_buffers':result['inserted_buffers'],'connectivity_checked_original_pins':checked}))
