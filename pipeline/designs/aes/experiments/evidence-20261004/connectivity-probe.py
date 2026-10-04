import odb,json,sys
sys.path.insert(0,"/design/probes")
from fanout_buffer import repair_fanout
from pathlib import Path
from openroad import Tech, Design
tech=Tech();design=Design(tech);design.readDb("/design/runs/aes-closure-20261004-baseline/41-openroad-repairantennas/1-diodeinsertion/aes_cipher_top.odb")
db=tech.getDB();b=db.getChip().getBlock()
print("net_types",[(b.findNet(n).getName(),str(b.findNet(n).getSigType())) for n in ["clknet_leaf_70_clk","_05848_"]])
original={(t.getInst().getName(),t.getMTerm().getName()):(t.getNet().getName(),t.getNet().getName()) for inst in b.getInsts() for t in inst.getITerms() if t.getNet() is not None}
original_drivers={n.getName(): [(t.getInst().getName(),t.getMTerm().getName()) for t in n.getITerms() if str(t.getIoType())=="OUTPUT"] for n in b.getNets()}
result=repair_fanout(db,16,"sky130_fd_sc_hd__buf_4","sky130_fd_sc_hd__clkbuf_8")
Path("/design/probes/fanout.json").write_text(json.dumps(result,indent=2))
checked=0
for inst in b.getInsts():
 if inst.getName().startswith("fanout_repair_"):continue
 for term in inst.getITerms():
  if (term.getInst().getName(),term.getMTerm().getName()) not in original:continue
  original_net,original_name=original[(term.getInst().getName(),term.getMTerm().getName())]
  net=term.getNet()
  assert net is not None
  hops=0
  while net.getName().startswith("fanout_repair_"):
   drivers=[t for t in net.getITerms() if str(t.getIoType())=="OUTPUT"]
   assert len(drivers)==1
   bi=drivers[0].getInst()
   assert bi.getName().startswith("fanout_repair_")
   ins=[t for t in bi.getITerms() if str(t.getIoType())=="INPUT" and t.getNet() is not None]
   assert len(ins)==1
   net=ins[0].getNet();hops+=1
   assert hops<20,"cycle"
  assert net.getName()==original_net,(inst.getName(),term.getMTerm().getName(),original_name,net.getName())
  checked+=1
print("connectivity_checked_original_pins",checked)
for net in b.getNets():
 if net.getName().startswith("fanout_repair_"):
  assert len([t for t in net.getITerms() if str(t.getIoType())=="INPUT"])<=16
odb.write_db(db,"/design/probes/fanout.odb")
print(json.dumps({"inserted":result["inserted_buffers"],"nets":len(result["repaired_nets"]),"skipped":result["skipped_nets"]}))
