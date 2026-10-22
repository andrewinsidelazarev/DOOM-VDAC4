"""Replay recorded engine E4 requests through real Z80/FAT32, compare SD I/O.

Record with VD_TEST_TRACE=1 build/host/host.exe tests/data/DOOM1.WAD save.
This measures sector reads and checks every returned byte, not hardware time.
"""
from pathlib import Path
import argparse,json,struct,time
from unittest.mock import patch
import test_launcher as t

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--limit',type=int,default=256)
parser.add_argument('--spc',type=int,default=8)
args=parser.parse_args()
wad=(t.ROOT/'tests/data/DOOM1.WAD').read_bytes()
trace=list(struct.iter_unpack('<IH',(t.ROOT/'build/host/wad-requests.bin').read_bytes()))
if args.limit:trace=trace[:args.limit]

class Replay(t.Esp):
    def __call__(self,c,p):
        if c==0x70:self.before=self.machine.reads
        return super().__call__(c,p)

results=[]
with patch.object(t,'WAD',wad):
    for mapped in (False,True):
        disk,_=t.disk_image(spc=args.spc,wad_stride=1)
        esp=Replay(cached=True)
        esp.plan=[(0xE4,struct.pack('<IH',at,n),('wad',wad[at:at+n])) for at,n in trace]
        m=t.Machine(disk,esp);esp.machine=m
        m.step_limit=10000000
        if not mapped:
            at=m.symbols['ReadAt']+4
            assert m.memory[at]==0xC2  # bypass only JP NZ,FastReadAt
            m.memory[at:at+3]=bytes(3)
        started=time.monotonic()
        try:m.run()
        except Exception:
            print(dict(sd_reads=m.reads,sequence=esp.seq,remaining=len(esp.plan)),flush=True)
            raise
        assert esp.done and not m.writes
        assert m.memory[m.symbols['FastEnabled']]==1
        row=dict(reader='extents' if mapped else 'FILEX_READ_AT',requests=len(trace),
                 data_bytes=sum(n for _,n in trace),sectors_per_cluster=args.spc,
                 sd_reads=m.reads-esp.before,host_seconds=round(time.monotonic()-started,3))
        results.append(row);print(json.dumps(row),flush=True)
assert results[1]['sd_reads']<results[0]['sd_reads']
(t.ROOT/f'build/wad-replay-spc{args.spc}.json').write_text(json.dumps(results,indent=2)+'\n')
