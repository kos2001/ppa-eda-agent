import re,sys,collections,statistics,json
def parse(path):
    nets={}; cur=None; in_nets=False
    seg=re.compile(r'^\s*(?:\+ ROUTED|NEW)\s+(\w+)\s+\(\s*(\d+)\s+(\d+)\s*\)\s+\(\s*(\*|\d+)\s+(\*|\d+)\s*\)')
    for line in open(path):
        if line.startswith('NETS '): in_nets=True; continue
        if line.startswith('END NETS'): break
        if not in_nets: continue
        if line.startswith('    - '):
            name=line.split()[1]; cur={'pins':0,'diodes':0,'len':collections.Counter()}; nets[name.replace('\\','')]=cur
            for m in re.finditer(r'\(\s*(\S+)\s+(\S+)\s*\)',line):
                inst=m.group(1)
                if inst.startswith('ANTENNA_'): cur['diodes']+=1
                elif inst!='PIN': cur['pins']+=1
            continue
        if cur is None: continue
        if line.lstrip().startswith('('):   # connection list continued on a following line
            for m in re.finditer(r'\(\s*(\S+)\s+(\S+)\s*\)',line):
                inst=m.group(1)
                if inst.startswith('ANTENNA_'): cur['diodes']+=1
                elif inst!='PIN': cur['pins']+=1
            continue
        m=seg.match(line)
        if m:
            layer=m.group(1); x1,y1=int(m.group(2)),int(m.group(3)); x2=x1 if m.group(4)=='*' else int(m.group(4)); y2=y1 if m.group(5)=='*' else int(m.group(5))
            cur['len'][layer]+=(abs(x2-x1)+abs(y2-y1))/1000.0
    return nets
if __name__=='__main__':
    nets=parse(sys.argv[1]); print(len(nets),'nets')
