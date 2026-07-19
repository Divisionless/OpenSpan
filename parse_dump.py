import sys
from minidump.minidumpfile import MinidumpFile

def modranges(mf):
    mods = []
    for m in mf.modules.modules:
        name = (m.name or '?').split('\\')[-1]
        mods.append((m.baseaddress, m.baseaddress + m.size, name))
    mods.sort()
    return mods

def which(mods, addr):
    for lo, hi, name in mods:
        if lo <= addr < hi:
            return f"{name}+0x{addr-lo:x}"
    return None

def main(path):
    mf = MinidumpFile.parse(path)
    print("== DUMP:", path.split('\\')[-1])
    mods = modranges(mf)
    tid = None
    exc = getattr(mf, 'exception', None)
    if exc is not None:
        recs = getattr(exc, 'exception_records', None) or [exc]
        for r in recs:
            er = getattr(r, 'exception_record', r)
            code = getattr(er, 'ExceptionCode', None)
            addr = getattr(er, 'ExceptionAddress', None)
            tid = getattr(r, 'ThreadId', tid)
            fa = which(mods, addr) if addr else None
            cc = hex(code) if isinstance(code, int) else code
            print(f"  exception code={cc} at {hex(addr) if addr else '?'} ({fa}) thread={hex(tid) if tid else '?'}")
    th = None
    for t in mf.threads.threads:
        if tid is not None and t.ThreadId == tid:
            th = t; break
    if th is None:
        th = mf.threads.threads[0]
    start = th.Stack.StartOfMemoryRange
    size = th.Stack.MemoryLocation.DataSize
    rdr = mf.get_reader()
    try:
        data = rdr.read(start, size)
    except Exception as e:
        print("  (read failed:", e, ") trying buffered stack")
        data = th.Stack.MemoryLocation
        return
    print(f"  crash thread {hex(th.ThreadId)} stack {hex(start)} size {hex(size)}")
    print("  --- call stack (top-down, module attribution of return addrs) ---")
    seen, last = [], None
    for off in range(0, len(data) - 8, 8):
        val = int.from_bytes(data[off:off+8], 'little')
        w = which(mods, val)
        if w:
            base = w.split('+')[0]
            if base != last:      # collapse consecutive same-module frames
                seen.append(w); last = base
    for w in seen[:50]:
        print("   ", w)

if __name__ == "__main__":
    main(sys.argv[1])
