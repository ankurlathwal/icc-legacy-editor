"""Annotated store-path listing for ICC 2 (MFC42) Serialize functions, with embedded
member objects resolved to their classes via the owning class's constructor."""
import sys, re, struct, pefile, capstone
md=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_32)
class Mod:
    def __init__(s,path):
        s.pe=pefile.PE(path); s.b=s.pe.OPTIONAL_HEADER.ImageBase; s.img=s.pe.get_memory_mapped_image()
        s.ex={}; s.addr={}
        for e in s.pe.DIRECTORY_ENTRY_EXPORT.symbols:
            if e.name: s.ex[s.b+e.address]=e.name.decode(); s.addr.setdefault(e.name.decode(), s.b+e.address)
        s.imps={i.address:(e.dll.decode().split('.')[0]+'!'+(i.name.decode() if i.name else '#%d'%i.ordinal)) for e in s.pe.DIRECTORY_ENTRY_IMPORT for i in e.imports}
    def dis(s,a,n=0x1800):
        out=[]
        for i in md.disasm(s.img[a-s.b:a-s.b+n],a):
            out.append(i)
            if i.mnemonic=='int3' or (i.mnemonic=='ret' and s.img[i.address+i.size-s.b] in (0xcc,0x90)): break
        return out
    def real(s,a):
        i=next(md.disasm(s.img[a-s.b:a-s.b+6],a))
        if i.mnemonic=='jmp' and i.op_str.startswith('0x'): return s.real(int(i.op_str,16))
        return a
    def name(s,t):
        t2=s.real(t)
        if t2 in s.ex: return s.ex[t2]
        i=next(md.disasm(s.img[t-s.b:t-s.b+6],t))
        if i.mnemonic=='jmp' and 'dword ptr [0x' in i.op_str: return s.imps.get(int(i.op_str.split('[')[1].rstrip(']'),16),'?')
        return hex(t)
MODS=[Mod('CrickMan.dll'),Mod('CrTypes.dll')]
VT={}   # vtable addr -> class
for m in MODS:
    for a,n in m.ex.items():
        mm=re.match(r'\?\?_7(\w+)@@6B@',n)
        if mm: VT[a]=mm.group(1)
def find(name_prefix):
    for m in MODS:
        for n,a in m.addr.items():
            if n.startswith(name_prefix): return m,a
    return None,None
def members(cls):
    """offset -> member class, from the class's constructors."""
    out={}
    for m in MODS:
        for n,a in m.addr.items():
            if not n.startswith('??0%s@@'%cls): continue
            ins=m.dis(m.real(a),0x1000); last_lea={}; regvt={}
            for i in ins:
                mm=re.match(r'dword ptr \[(e\w\w) \+ (0x[0-9a-f]+)\], (0x[0-9a-f]+)$',i.op_str)
                if i.mnemonic=='mov' and mm and int(mm.group(3),16) in VT:
                    out.setdefault(int(mm.group(2),16), VT[int(mm.group(3),16)])
                elif i.mnemonic=='mov' and mm and 0x100c0000<=int(mm.group(3),16)<0x100d0000:
                    ser=struct.unpack_from('<I',m.img,int(mm.group(3),16)-m.b+8)[0]
                    out.setdefault(int(mm.group(2),16), 'VT%s(ser %s)'%(mm.group(3), hex(m.real(ser))))
                mm3=re.match(r'dword ptr \[(e\w\w) \+ (0x[0-9a-f]+)\], (e[a-z]x)$',i.op_str)
                if i.mnemonic=='mov' and mm3 and mm3.group(3) in regvt:
                    out.setdefault(int(mm3.group(2),16), regvt[mm3.group(3)])
                mm4=re.match(r'(e[a-z]x), (0x100c[0-9a-f]{4})$',i.op_str)
                if i.mnemonic=='mov' and mm4:
                    v=int(mm4.group(2),16)
                    if v in VT: regvt[mm4.group(1)]=VT[v]
                    else:
                        ser=struct.unpack_from('<I',m.img,v-m.b+8)[0]; regvt[mm4.group(1)]='VT%s(ser %s)'%(mm4.group(2), hex(m.real(ser)))
                mm=re.match(r'ecx, \[(e\w\w) \+ (0x[0-9a-f]+)\]$',i.op_str)
                if i.mnemonic=='lea' and mm: last_lea['ecx']=int(mm.group(2),16)
                if i.mnemonic=='call' and i.op_str.startswith('0x') and 'ecx' in last_lea:
                    t=m.name(int(i.op_str,16)); mm2=re.match(r'\?\?0(\w+)@@',t)
                    if mm2 and mm2.group(1)!=cls: out.setdefault(last_lea['ecx'], mm2.group(1))
                    last_lea.pop('ecx',None)
    return out
def listing(fn_prefix, cls=None):
    m,a=find(fn_prefix)
    a=m.real(a); mem=members(cls) if cls else {}
    lines=[]
    last_member=None
    for i in m.dis(a):
        o=i.op_str
        mm=re.search(r'\[e(?:si|di|bp|bx) \+ (0x[0-9a-f]+)\]',o)
        if mm and i.mnemonic in('lea','mov'): last_member=int(mm.group(1),16)
        if i.mnemonic=='call':
            if o.startswith('0x'):
                n=m.name(int(o,16))
                if '#2801' in n or '#2740' in n: continue
                lines.append('%x CALL %s'%(i.address,n))
            elif re.match(r'dword ptr \[e\wx \+ 8\]',o):
                lines.append('%x VCALL Serialize of member +%s -> %s'%(i.address, hex(last_member) if last_member is not None else '?', mem.get(last_member,'?')))
            else: lines.append('%x CALL %s'%(i.address,o))
        elif i.mnemonic=='mov' and re.match(r'(byte|word|dword) ptr \[e[a-d]x\], ',o): lines.append('%x   WRITE %s'%(i.address,o.split(' ptr')[0]))
        elif i.mnemonic.startswith('j') and i.mnemonic not in('jmp',) and 'jbe' not in i.mnemonic: lines.append('%x   %s %s'%(i.address,i.mnemonic,o))
        elif i.mnemonic=='ret': lines.append('%x RET'%i.address)
        elif i.mnemonic=='cmp' and re.search(r', 0x[0-9a-f]+$|, [0-9]+$',o): lines.append('%x   cmp %s'%(i.address,o))
    return mem, lines
if __name__=='__main__':
    fn=sys.argv[1]; cls=sys.argv[2] if len(sys.argv)>2 else None
    mem,lines=listing(fn,cls)
    if cls: print('members of',cls,{hex(k):v for k,v in sorted(mem.items())})
    stop=int(sys.argv[3]) if len(sys.argv)>3 else 1
    n=0
    for l in lines:
        print(l)
        if l.endswith('RET'):
            n+=1
            if n>=stop: break
