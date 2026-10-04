"""Symbolic bit tracer for straight-line x86 bit-(un)packing code.
Each 32-bit value is a list of 32 frozensets of source bits (XOR-combined)."""
import pefile, capstone, re, sys
Z=frozenset()
def const(v): return [frozenset({'1'}) if (v>>i)&1 else Z for i in range(32)]
def sym(name,n=32): return [frozenset({(name,i)}) if i<n else Z for i in range(32)]
def show(bits,lo=0,hi=32):
  out=[];i=lo
  while i<hi:
    b=bits[i]
    if not b: i+=1; continue
    # group consecutive bits from same source
    if len(b)==1 and isinstance(next(iter(b)),tuple):
      (src,idx),=b; j=i
      while j+1<hi and bits[j+1]==frozenset({(src,idx+j+1-i)}): j+=1
      out.append('[%d:%d]<-%s[%d:%d]'%(i,j,src,idx,idx+j-i)); i=j+1
    else:
      out.append('[%d]<-%s'%(i,'^'.join(sorted(map(str,b))))); i+=1
  return ' '.join(out)
REG32=['eax','ebx','ecx','edx','esi','edi','ebp','esp']
SUB={'al':('eax',0,8),'ah':('eax',8,16),'bl':('ebx',0,8),'bh':('ebx',8,16),'cl':('ecx',0,8),'ch':('ecx',8,16),'dl':('edx',0,8),'dh':('edx',8,16),'ax':('eax',0,16),'cx':('ecx',0,16),'dx':('edx',0,16),'bx':('ebx',0,16),'si':('esi',0,16),'di':('edi',0,16),'bp':('ebp',0,16)}
class M:
  def __init__(s,dll,start,end,reads,mem_init):
    pe=pefile.PE(dll); s.base=pe.OPTIONAL_HEADER.ImageBase; s.img=pe.get_memory_mapped_image()
    s.md=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_32)
    s.start,s.end=start,end; s.reads=list(reads)
    s.r={k:sym('?'+k) for k in REG32}; s.mem=dict(mem_init); s.log=[]; s.pushes=[]
  def getr(s,n):
    if n in s.r: return list(s.r[n])
    b,lo,hi=SUB[n]; v=s.r[b][lo:hi]; return v+[Z]*(32-len(v))
  def setr(s,n,v):
    if n in s.r: s.r[n]=list(v); return
    b,lo,hi=SUB[n]; cur=list(s.r[b]); cur[lo:hi]=v[:hi-lo]; s.r[b]=cur
  def memkey(s,op):
    m=re.search(r'\[(.*)\]',op); return m.group(1)
  def width(s,op):
    return 8 if op.startswith('byte') else 16 if op.startswith('word') else 32
  def get(s,op):
    op=op.strip()
    if op.startswith('0x') or op.lstrip('-').isdigit(): return const(int(op,0)&0xffffffff)
    if '[' in op:
      k=s.memkey(op); w=s.width(op)
      if k in ('eax','ecx','edx') :  # buffer read
        name=s.reads.pop(0); return sym(name,w)
      if k not in s.mem: s.mem[k]=sym('M['+k+']')
      v=s.mem[k][:w]; return v+[Z]*(32-w)
    return s.getr(op)
  def put(s,op,v):
    op=op.strip()
    if '[' in op:
      k=s.memkey(op); w=s.width(op)
      cur=s.mem.get(k,sym('M['+k+']')); cur=list(cur); cur[:w]=v[:w]; s.mem[k]=cur
    else: s.setr(op,v)
  def run(s):
    a=s.start
    while a<s.end:
      ins=next(s.md.disasm(s.img[a-s.base:a-s.base+16],a)); a=ins.address+ins.size
      m,ops=ins.mnemonic,[o.strip() for o in re.split(r',(?![^\[]*\])',ins.op_str)] if ins.op_str else []
      if m in('jbe','jb','jmp','ja','jae','je','jne'):
        if m in('jbe','jmp'): a=int(ops[0],16)
        continue
      if m=='call':
        s.log.append((ins.address,'call',ops[0],list(s.pushes))); s.pushes=[]; s.r['eax']=sym('ret@%x'%ins.address); continue
      if m=='push': s.pushes.append((ops[0],s.get(ops[0]))); continue
      mm=re.match(r'(e[a-z]{2}), \[(e[a-z]{2})\*([248])\]$', ins.op_str) if m=='lea' else None
      if mm:
        k={'2':1,'4':2,'8':3}[mm.group(3)]; x=s.getr(mm.group(2))
        s.setr(mm.group(1), ([Z]*k+x)[:32]); continue
      if m in('cmp','test','cdq','pop','lea','add','sub','inc','dec'):
        if m in ('add','sub','inc','dec','lea') and ops and ops[0] not in ('eax','ecx','edx','ebp','esp') and '[' not in ops[0]:
          s.put(ops[0],sym('arith@%x'%ins.address))
        continue
      if m=='mov' or m=='movzx':
        v=s.get(ops[1]); s.put(ops[0],v); continue
      if m in('and','or','xor'):
        x=s.get(ops[0]); y=s.get(ops[1])
        if m=='and':
          if ops[1].startswith('0x') or ops[1].isdigit():
            c=int(ops[1],0); r=[x[i] if (c>>i)&1 else Z for i in range(32)]
          else: r=[x[i] if y[i]==frozenset({'1'}) else (Z if not y[i] else frozenset({('AND',str(x[i]),str(y[i]))})) for i in range(32)]
        elif m=='xor': r=[x[i]^y[i] for i in range(32)]
        else: r=[x[i]|y[i] for i in range(32)]
        w=s.width(ops[0]) if '[' in ops[0] else (8 if ops[0] in SUB and SUB[ops[0]][2]-SUB[ops[0]][1]==8 else 16 if ops[0] in SUB else 32)
        s.put(ops[0],r); continue
      if m in('shl','shr','sar'):
        x=s.get(ops[0]); n=int(ops[1],0) if len(ops)>1 else 1
        w=8 if ops[0] in SUB and SUB[ops[0]][2]-SUB[ops[0]][1]==8 else 32
        x=x[:w]
        r=([Z]*n+x)[:w] if m=='shl' else x[n:]+[Z]*n
        s.put(ops[0],r+[Z]*(32-w)); continue
      if m=='not':
        x=s.get(ops[0]); s.put(ops[0],[b^frozenset({'1'}) for b in x]); continue
      raise Exception('unhandled %x %s %s'%(ins.address,m,ins.op_str))
