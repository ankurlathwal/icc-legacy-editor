"""Summarise the store (IsStoring) path of an MFC71 Serialize function: member reads, inline writes, calls."""
import sys, re, pefile, capstone
dll, name = sys.argv[1], sys.argv[2]
ARCH = r'\+ 0x(14|24|28)\]' if 'MFC42' in open(dll,'rb').read().decode('latin1') else r'\+ 0x(14|18|28|2c)\]'
pe=pefile.PE(dll); b=pe.OPTIONAL_HEADER.ImageBase; img=pe.get_memory_mapped_image()
md=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_32)
ex={e.name.decode(): b+e.address for e in pe.DIRECTORY_ENTRY_EXPORT.symbols if e.name}
names={v:k for k,v in ex.items()}
imps={i.address:(e.dll.decode().split('.')[0]+'!'+(i.name.decode() if i.name else '#%d'%i.ordinal)) for e in pe.DIRECTORY_ENTRY_IMPORT for i in e.imports}
def nm(t):
    if t in names: return names[t]
    try:
        j=next(md.disasm(img[t-b:t-b+6],t))
        if j.mnemonic=='jmp' and j.op_str.startswith('0x'): return nm(int(j.op_str,16))
        if j.mnemonic=='jmp' and 'dword ptr [0x' in j.op_str: return imps.get(int(j.op_str.split('[')[1].rstrip(']'),16),'?')
    except StopIteration: pass
    return hex(t)
a=next(v for k,v in ex.items() if k.startswith(name))
a0=a
j=next(md.disasm(img[a-b:a-b+5],a))
if j.mnemonic=='jmp': a=int(j.op_str,16)
lim=int(sys.argv[3],16) if len(sys.argv)>3 else 0x2000
for i in md.disasm(img[a-b:a-b+lim],a):
    o=i.op_str; line=None
    if i.mnemonic=='call':
        t=int(o,16) if o.startswith('0x') else None
        n=nm(t) if t else o
        if 'MFC71!#2306' in n or 'MFC71!#1181' in n or 'MFC71!#2259' in n: continue
        line='CALL '+n
    elif re.search(r'(byte|word|dword) ptr \[e[a-d]x\], [a-z]', o) and i.mnemonic=='mov': line='  WRITE '+o
    elif re.search(r'ptr \[e(bp|si|di) \+ 0x[0-9a-f]+\]$', o) and i.mnemonic in('mov','movzx','movsx') and not re.search(ARCH,o): line='  read  '+o
    elif i.mnemonic in('ret',): line='RET'
    elif i.mnemonic.startswith('j') and i.mnemonic!='jmp' and False: line=i.mnemonic
    if line: print('%x %s'%(i.address,line))
    if i.mnemonic=='int3': break
