# Recover Go function names+addresses from __gopclntab (Go 1.18+/1.20+ layout), Mach-O 64 thin.
import struct,sys
b=open(sys.argv[1],'rb').read()
ncmds=struct.unpack_from('<I',b,16)[0]; off=32; sect=None; text_vm=None
for _ in range(ncmds):
    cmd,sz=struct.unpack_from('<II',b,off)
    if cmd==0x19:
        segname=b[off+8:off+24].rstrip(b'\0'); nsect=struct.unpack_from('<I',b,off+64)[0]
        for i in range(nsect):
            so=off+72+i*80; name=b[so:so+16].rstrip(b'\0')
            addr,size,foff=struct.unpack_from('<QQI',b,so+32)
            if name==b'__gopclntab': sect=(addr,size,foff)
            if name==b'__text' and segname==b'__TEXT': text_vm=addr
    off+=sz
addr,size,foff=sect; p=b[foff:foff+size]
magic=struct.unpack_from('<I',p,0)[0]
nfunc,nfiles,textStart,funcnameOff,cuOff,filetabOff,pctabOff,pclnOff=struct.unpack_from('<8Q',p,8)
assert magic in (0xfffffff0,0xfffffff1), hex(magic)
ft=p[pclnOff:]
for i in range(nfunc):
    entryoff,funcoff=struct.unpack_from('<II',ft,i*8)
    nameoff=struct.unpack_from('<i',ft,funcoff+4)[0]
    s=p[funcnameOff+nameoff:p.index(b'\0',funcnameOff+nameoff)].decode('utf-8','replace')
    print(f"{textStart+entryoff:#x} {s}")
