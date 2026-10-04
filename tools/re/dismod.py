import pefile, capstone, sys
f=sys.argv[1]
pe=pefile.PE(f); base=pe.OPTIONAL_HEADER.ImageBase
md=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_32)
img=pe.get_memory_mapped_image()
imps={}
for e in pe.DIRECTORY_ENTRY_IMPORT:
  for i in e.imports:
    imps[i.address]=e.dll.decode().split('.')[0]+'!'+(i.name.decode() if i.name else '#%d'%i.ordinal)
exps={base+e.address:e.name.decode() for e in pe.DIRECTORY_ENTRY_EXPORT.symbols if e.name}
byname={v:k for k,v in exps.items()}
# resolve jmp thunks (incremental linking)
def thunk(a):
  try:
    ins=next(md.disasm(img[a-base:a-base+5],a))
    if ins.mnemonic=='jmp' and ins.op_str.startswith('0x'): return int(ins.op_str,16)
  except StopIteration: pass
  return a
def name(v):
  if v in imps: return imps[v]
  t=thunk(v)
  if t in exps: return exps[t]
  if v in exps: return exps[v]
  # jmp [import]
  try:
    ins=next(md.disasm(img[v-base:v-base+6],v))
    if ins.mnemonic=='jmp' and 'dword ptr [0x' in ins.op_str:
      a=int(ins.op_str.split('[')[1].rstrip(']'),16); return imps.get(a,'?')
  except StopIteration: pass
  return None
def dis(a, limit=0x3000):
  a=thunk(a)
  out=[]
  for ins in md.disasm(img[a-base:a-base+limit],a):
    ann=''
    for tok in ins.op_str.replace('[',' ').replace(']',' ').replace(',',' ').split():
      if tok.startswith('0x'):
        n=name(int(tok,16))
        if n: ann=' ; '+n
    out.append('%x  %-6s %s%s'%(ins.address,ins.mnemonic,ins.op_str,ann))
    if ins.mnemonic=='int3' or (ins.mnemonic=='ret' and img[ins.address+ins.size-base] in (0xcc,0x90)): break
  return out
for fn in sys.argv[2:]:
  key=[k for k in byname if fn in k]
  for k in key:
    print('=====',k); print('\n'.join(dis(byname[k])))
