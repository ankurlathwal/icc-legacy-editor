import pefile, capstone, sys, re
f=sys.argv[1]; cls=sys.argv[2]
pe=pefile.PE(f); base=pe.OPTIONAL_HEADER.ImageBase
md=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_32)
img=pe.get_memory_mapped_image()
for e in pe.DIRECTORY_ENTRY_EXPORT.symbols:
  if not e.name: continue
  n=e.name.decode()
  if not n.endswith('@'+cls+'@@') and ('@'+cls+'@@') not in n: continue
  if not re.match(r'\?(get|is|set|has|can)',n): continue
  a=base+e.address
  ins=list(md.disasm(img[e.address:e.address+60],a))
  if ins and ins[0].mnemonic=='jmp':
    t=int(ins[0].op_str,16); ins=list(md.disasm(img[t-base:t-base+60],t))
  body=[]
  for i in ins[:10]:
    body.append(i.mnemonic+' '+i.op_str)
    if i.mnemonic=='ret': break
  offs=sorted(set(re.findall(r'\[(?:ecx|esi|eax|edx) \+ (0x[0-9a-f]+)\]',' '.join(body))))
  print('%-70s %s   | %s'%(n[:70],offs,' ; '.join(body)[:150]))
