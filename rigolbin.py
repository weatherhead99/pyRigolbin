import dataclasses_struct as dcs
from typing import Annotated, TypeVar, Type, Generator
from pathlib import Path
from io import BufferedIOBase
import logging
import pprint
from dataclasses import InitVar, field
from datetime import datetime
import struct

#note see https://int.rigol.com/Images/DHO900_UserGuide_EN_tcm7-6003.pdf
# section 23.2.4 for documentation of this format

class RigolBinaryHeader:
    def extra_bytes_after(self) -> int:
        if hasattr(self, "sz"):
            assert isinstance(self.sz, int)
            strsz: int = dcs.get_struct_size(type(self))
            if self.sz < strsz:
                raise ValueError("data size reported is smaller than struct size! This should never happen")
            return self.sz - strsz
        return 0

    def __init__(self):
        logging.debug(pprint.pprint(self))

def _raw_str_decode(inp: bytes) -> str:
    return inp.rstrip(bytes([0])).decode("UTF-8")

@dcs.dataclass(dcs.LITTLE_ENDIAN)
class RigolBinaryFileHeader(RigolBinaryHeader):
    magicraw: Annotated[bytes, 2]
    versionraw: Annotated[bytes, 2]
    fsz: dcs.U64
    nwfms: dcs.U32

    def __post_init__(self):
        super().__init__()
        self.version = _raw_str_decode(self.versionraw)


@dcs.dataclass(dcs.LITTLE_ENDIAN)
class RigolBinaryWaveformHeader(RigolBinaryHeader):
    sz: dcs.U32
    tp: dcs.U32
    nbuf: dcs.U32
    npts: dcs.U32
    count: dcs.U32
    xdisprange: dcs.F32
    xdisporigin: dcs.F64
    xincr: dcs.F64
    xorigin: dcs.F64
    xunits: dcs.U32
    yunits: dcs.U32
    dateraw: Annotated[bytes, 16] #72 chars from here
    timeraw: Annotated[bytes, 16]
    modelraw: Annotated[bytes, 24]
    nameraw: Annotated[bytes, 16]

    def __post_init__(self):
        super().__init__()
        self.name = _raw_str_decode(self.nameraw)
        self.model = _raw_str_decode(self.modelraw)
        datestr: str = _raw_str_decode(self.dateraw)
        timestr: str = _raw_str_decode(self.timeraw)
        self.dt = datetime.strptime(f"{datestr} {timestr}",
                                    "%Y-%m-%d %H:%M:%S")


@dcs.dataclass(dcs.LITTLE_ENDIAN)
class RigolBinaryWaveformDataHeader(RigolBinaryHeader):
    sz: dcs.U32
    tp: dcs.U16
    bpp: dcs.U16
    bufsz: dcs.U64

    def __post_init__(self):
        super().__init__()


class RigolBinaryWaveformData:
    @classmethod
    def read_from_stream(cls, wfdhdr: RigolBinaryWaveformData, **kwargs):
        rawdat: bytes = strm.read(wfdhdr.bufsz)
        return RigolBinaryWaveformData(rawdat, **(kwargs | {"wfdhdr" : wfdhdr}))

    def __init__(self, rawdat: bytes, wfhdr: RigolBinaryWaveformHeader,
                 wfdhdr: RigolBinaryWaveformDataHeader, fhdr: RigolBinaryFileHeader):
        self._rawdat = rawdat
        self._wfhdr = wfhdr
        self._fhdr = fhdr
        self._wfdhdr = wfdhdr
        self._bpp = wfdhdr.bpp
        match self._bpp:
            case 4:
                self._itemfmt = "f"
            case 1:
                self._itemfmt = "B"
            case _:
                raise ValueError("can't interpret bpp field in data!")
        self._pylist = None
        self._xorigin = xorigin
        self._xincr = xincr

    @property
    def xiter(self) -> Generator[float]:
        xval: float = self._wfhdr.xorigin
        yield xval
        for ind in range(len(self)):
            xval += self._wfhdr.xincr
            yield xval

    @property
    def xlist(self) -> list[float]:
        return list(self.xiter)

    @property
    def itemfmt(self) -> str:
        return f"<{self._itemfmt}"

    @property
    def arrayfmt(self) -> str:
        arrstr: str = self._itemfmt * len(self)
        return f"<{arrstr}"

    def __iter__(self) -> Generator[float]:
        if self._pylist is not None:
            yield from self._pylist
        for itemind in range(len(self._rawdat) // self._bpp):
            buf = self._rawdat[itemind * self._bpp : (itemind+1)* self._bpp]
            out: float = struct.unpack(self.itemfmt, buf)[0]
            yield out

    def __getitem__(self, idx: int) -> float:
        if self._pylist is not None:
            return self._pylist[idx]
        buf = self._rawdat[idx * self._bpp: idx * self._bpp + self._bpp]
        return struct.unpack(self.itemfmt, buf)[0]

    def __len__(self) -> int:
        return len(self._rawdat) // self._bpp

    def to_list(self) -> list[float]:
        out =  list(struct.unpack(self.arrayfmt, self._rawdat))
        self._pylist = out
        return out


HeaderT = TypeVar("HeaderT", bound=RigolBinaryHeader, covariant=True)

def _read_header(f: BufferedIOBase, T: Type[HeaderT]) -> HeaderT:
    hdr_size: int = dcs.get_struct_size(T)
    packed_data: bytes = f.read(hdr_size)
    hdrout = T.from_packed(packed_data)
    #throw away extra bytes
    ebytes: int = hdrout.extra_bytes_after()
    if ebytes > 0:
        f.read(ebytes)

    return T.from_packed(packed_data)


def read_rigol_bin(floc: str | Path):
    with open(floc, "rb") as f:
        fhdr = _read_header(f, RigolBinaryFileHeader)
        waveforms = []
        for i in range(fhdr.nwfms):
            wvhdr = _read_header(f, RigolBinaryWaveformHeader)
            wvdathdr = _read_header(f, RigolBinaryWaveformDataHeader)
            wvdat = RigolBinaryWaveformData.read_from_stream(f, wvdathdr, wvhdr)
            waveforms.append((wvhdr, wvdathdr, wvdat))

    return fhdr, waveforms

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    fhdr, waveforms = read_rigol_bin("RigolDS20.bin")
