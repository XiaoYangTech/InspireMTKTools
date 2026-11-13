"""Dump .NET IL (metadata + method bodies) from a PE assembly using dnfile/dncil.

Usage:
  python tools/ildump.py <assembly.exe> [--out out_dir] [--type-filter LogoBuilder]
                         [--method-filter MainForm] [--list-types]

Outputs:
  <out>/types.txt          - all typedefs with method/field counts
  <out>/methods.txt        - all methods with signatures
  <out>/il/<TypeName>.<Method>.il - per-method IL listing
  <out>/methods.jsonl      - machine-readable index
"""
import argparse
import json
import os
import sys

import dnfile
from dncil.cil.body import CilMethodBody
from dncil.cil.body.reader import CilMethodBodyReaderBase
from dncil.cil.error import MethodBodyFormatError


class DnfileBodyReader(CilMethodBodyReaderBase):
    def __init__(self, pe, row):
        self.pe = pe
        self.row = row
        self.rva = row.Rva
        self.offset = 0

    def read(self, n):
        data = self.pe.get_data(self.rva + self.offset, n)
        self.offset += n
        return data

    def tell(self):
        return self.offset

    def seek(self, offset):
        self.offset = offset
        return self.offset


def u16(data, off):
    return int.from_bytes(data[off:off + 2], "little")


def first_row_index(lst):
    if not lst:
        return None
    item = lst[0]
    return getattr(item, "row_index", None)


def fmt_constant(pe, row):
    # try decode Constant value for a Field row
    try:
        tbl = pe.net.mdtables.Constant
        for c in tbl.rows:
            if c.Parent.row_index == row.row_index and c.Parent.table.name == "Field":
                v = c.Value
                if isinstance(v, bytes):
                    return v
                return v
    except Exception:
        pass
    return None


def get_types(pe):
    td = pe.net.mdtables.TypeDef
    rows = td.rows
    out = []
    for i, row in enumerate(rows):
        out.append({
            "index": i + 1,
            "token": f"0x{(0x02000000 + i + 1):08x}",
            "namespace": str(row.TypeNamespace) if row.TypeNamespace else "",
            "name": str(row.TypeName) if row.TypeName else "",
            "full_name": ((str(row.TypeNamespace) + ".") if row.TypeNamespace else "") + (str(row.TypeName) if row.TypeName else ""),
            "_name": str(row.TypeName) if row.TypeName else "",
            "_ns": str(row.TypeNamespace) if row.TypeNamespace else "",
            "extends": getattr(row.Extends, "row_index", None) if row.Extends else None,
            "method_list": first_row_index(row.MethodList),
            "field_list": first_row_index(row.FieldList),
        })
    return out


def get_methods(pe):
    md = pe.net.mdtables.MethodDef
    rows = md.rows
    out = []
    for i, row in enumerate(rows):
        out.append({
            "index": i + 1,
            "token": f"0x{(0x06000000 + i + 1):08x}",
            "name": str(row.Name) if row.Name else "",
            "rva": row.Rva,
            "flags": row.Flags,
            "impl_flags": row.ImplFlags,
            "sig": (row.Signature.value if hasattr(row.Signature, "value") else None),
            "params": first_row_index(row.ParamList),
        })
    return out


def method_body_il(pe, row):
    """Return list of (offset, instr_str) or raise."""
    if not row.Rva:
        return None
    try:
        reader = DnfileBodyReader(pe, row)
        body = CilMethodBody(reader)
        lines = []
        for insn in body.instructions:
            lines.append((insn.offset, insn))
        return body, lines
    except MethodBodyFormatError as e:
        return e
    except Exception as e:  # noqa
        return e


def resolve_operand(pe, insn):
    """Best-effort operand rendering with token resolution."""
    op = insn.operand
    name = insn.opcode.name
    if op is None:
        return ""
    # token operands are ints referencing metadata
    if isinstance(op, int) and name not in ("ldc.i4", "ldc.i4.s", "ldc.i4.m1", "ldc.i4.0"):
        tok = op & 0xFFFFFFFF
        table = (tok >> 24) & 0xFF
        rid = tok & 0x00FFFFFF
        try:
            if table == 0x01:  # TypeRef
                r = pe.net.mdtables.TypeRef.rows[rid - 1]
                return f"{r.TypeNamespace}.{r.TypeName}  /*0x{tok:08x}*/".replace("None.", "")
            if table == 0x02:  # TypeDef
                r = pe.net.mdtables.TypeDef.rows[rid - 1]
                return f"{r.TypeNamespace}.{r.TypeName}  /*0x{tok:08x}*/".replace("None.", "")
            if table == 0x04:  # Field
                r = pe.net.mdtables.Field.rows[rid - 1]
                return f"field {r.Name}  /*0x{tok:08x}*/"
            if table == 0x06:  # MethodDef
                r = pe.net.mdtables.MethodDef.rows[rid - 1]
                return f"method {r.Name}  /*0x{tok:08x}*/"
            if table == 0x0A:  # MemberRef
                r = pe.net.mdtables.MemberRef.rows[rid - 1]
                cls = ""
                try:
                    cls = str(r.Class.row.Name) if r.Class and r.Class.row else ""
                except Exception:
                    pass
                return f"{cls}::{r.Name}  /*0x{tok:08x}*/"
            if table == 0x11:  # StandAloneSig
                return f"sig  /*0x{tok:08x}*/"
            if table == 0x1B:  # TypeSpec
                return f"typespec  /*0x{tok:08x}*/"
            if table == 0x2B:  # MethodSpec
                return f"methodspec  /*0x{tok:08x}*/"
        except Exception:
            return f"0x{tok:08x}"
        return f"0x{tok:08x}"
    if isinstance(op, bytes):
        return '"' + op.hex() + '"' if len(op) < 80 else '"' + op[:80].hex() + '..."'
    return str(op)


def find_type_of_method(types, method_index):
    """Map method row index -> owning type using method_list ranges."""
    best = None
    for t in types:
        ml = t["method_list"]
        if ml is None:
            continue
        # method row indices are 1-based and monotonically increasing; need end of next type
        if ml <= 1:
            continue
        if method_index >= ml:
            if best is None or ml > best["method_list"]:
                best = t
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("assembly")
    ap.add_argument("--out", default="analysis/il")
    ap.add_argument("--type-filter", default=None)
    ap.add_argument("--method-filter", default=None)
    ap.add_argument("--list-types", action="store_true")
    ap.add_argument("--max-chars", type=int, default=0, help="limit total IL chars (0 = unlimited)")
    args = ap.parse_args()

    pe = dnfile.dnPE(args.assembly)
    types = get_types(pe)
    methods = get_methods(pe)
    os.makedirs(args.out, exist_ok=True)
    os.makedirs(os.path.join(args.out, "il"), exist_ok=True)

    # compute per-type method ranges robustly (empty runs give None pointers)
    def compute_ends():
        starts = [(i, t["method_list"]) for i, t in enumerate(types) if t["method_list"]]
        starts.sort(key=lambda x: x[1])
        for i, t in enumerate(types):
            st = t["method_list"]
            if not st:
                t["method_end"] = st
                continue
            nxt = None
            for _, s2 in starts:
                if s2 > st:
                    nxt = s2
                    break
            t["method_end"] = nxt if nxt is not None else len(methods) + 1
    compute_ends()

    with open(os.path.join(args.out, "types.txt"), "w", encoding="utf-8") as f:
        for t in types:
            cnt = max(0, (t["method_end"] or 0) - (t["method_list"] or 0))
            f.write(f'{t["token"]} {t["full_name"]}  methods={cnt}\n')

    owner = {}
    for t in types:
        start = t["method_list"] or 0
        end = t["method_end"] or 0
        if not start or not end:
            continue
        for mi in range(start, end):
            owner[mi] = t["full_name"]

    index = []
    total_chars = 0
    for m in methods:
        full = owner.get(m["index"], "?")
        m["owner"] = full
        index.append({"token": m["token"], "owner": full, "name": m["name"], "rva": m["rva"]})

    with open(os.path.join(args.out, "methods.jsonl"), "w", encoding="utf-8") as f:
        for r in index:
            f.write(json.dumps(r) + "\n")

    written = 0
    for m in methods:
        full = m["owner"]
        if args.type_filter and args.type_filter not in full:
            continue
        if args.method_filter and args.method_filter not in m["name"]:
            continue
        row = pe.net.mdtables.MethodDef.rows[m["index"] - 1]
        res = method_body_il(pe, row)
        safe = lambda s: "".join(c if c.isalnum() or c in "._-" else "_" for c in s)
        fname = f'{safe(full)}.{safe(m["name"])}.{m["index"]}.il'
        path = os.path.join(args.out, "il", fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"// {full}::{m['name']}  token={m['token']} rva=0x{m['rva']:x}\n")
            if res is None:
                f.write("// <abstract / extern, no body>\n")
                continue
            if isinstance(res, Exception):
                f.write(f"// <body error: {res}>\n")
                continue
            body, lines = res
            try:
                hdr = body.header
                f.write(f"// maxstack={getattr(hdr, 'max_stack', '?')} localsig={getattr(hdr, 'local_var_sig_tok', '?')}\n")
            except Exception:
                pass
            for off, insn in lines:
                f.write(f"{off:04X}  {insn.opcode.name:<12} {resolve_operand(pe, insn)}\n")
                total_chars += 1
                if args.max_chars and total_chars > args.max_chars:
                    f.write("// <truncated>\n")
                    break
        written += 1

    print(f"types={len(types)} methods={len(methods)} il_files={written} -> {args.out}")
    if args.list_types:
        for t in types:
            cnt = max(0, (t["method_end"] or 0) - (t["method_list"] or 0))
            print(f'{t["token"]} {t["full_name"]} methods={cnt}')


if __name__ == "__main__":
    main()
