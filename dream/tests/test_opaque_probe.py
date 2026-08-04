"""Opaque-primitive probe → mirror emit path (Milestone-B Run-3 widener).

The probe measures config-dependent opaque types in-kernel (probe_primitives.py);
this pins the CONSUMPTION contract in mirror.py: any type present in
PRIMITIVE_SIZES is emittable as an alignment-matching blob so a PARENT struct
whose only blocker was that field now mirrors, with correct downstream offsets.
A probed opaque field flags the mirror opaque_probed (re-certified in-kernel at
transplant). Nothing guessed: an UNPROBED opaque type still refuses.
"""
import importlib.util
import os

import pytest

_spec = importlib.util.spec_from_file_location(
    "mirror_opq_t", os.path.join(os.path.dirname(__file__), "..", "mirror", "mirror.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def test_probed_membership_is_emit_license():
    # every probed type is treated as opaque-emittable (refuse-by-name set
    # auto-includes PRIMITIVE_SIZES keys) — probing a type licenses it.
    for t in M.PRIMITIVE_SIZES:
        assert t in M.OPAQUE_KERNEL_TYPES


def test_parent_with_probed_field_mirrors():
    # a struct whose ONLY hard field is a probed opaque type mirrors, and the
    # scalar field AFTER it lands at the probed size's offset.
    save = dict(M.PRIMITIVE_SIZES)
    try:
        M.PRIMITIVE_SIZES["struct faux_dev"] = [136, 8]
        M.OPAQUE_KERNEL_TYPES.add("struct faux_dev")
        src = ("struct host_s {\n\tint id;\n\tstruct faux_dev dev;\n\tint tag;\n};\n")
        m = M.mirror(src, "host_s")
        # id@0 (4) -> dev@8 (align 8, 136) -> tag@144
        offs = {f: o for _, f, o in m["fields"]}
        assert offs["id"] == 0 and offs["dev"] == 8 and offs["tag"] == 144
        assert m["size"] == 152          # 144 + 4 -> pad to align 8
        assert m.get("opaque_probed") is True
    finally:
        M.PRIMITIVE_SIZES.clear()
        M.PRIMITIVE_SIZES.update(save)
        M.OPAQUE_KERNEL_TYPES.discard("struct faux_dev")


def test_unprobed_opaque_still_refused():
    src = "struct host2_s {\n\tint id;\n\tstruct totally_unprobed_t x;\n};\n"
    with pytest.raises(M.Unsupported):
        M.mirror(src, "host2_s")


def test_scalar_typedef_probe_emits_scalar():
    # a probed SCALAR typedef (kuid_t-shaped: 4/4) emits as u32, not a blob
    save = dict(M.PRIMITIVE_SIZES)
    try:
        M.PRIMITIVE_SIZES["kuid_t"] = [4, 4]
        M.OPAQUE_KERNEL_TYPES.add("kuid_t")
        src = "struct host3_s {\n\tkuid_t owner;\n\tint n;\n};\n"
        m = M.mirror(src, "host3_s")
        offs = {f: o for _, f, o in m["fields"]}
        assert offs["owner"] == 0 and offs["n"] == 4
    finally:
        M.PRIMITIVE_SIZES.clear()
        M.PRIMITIVE_SIZES.update(save)
        M.OPAQUE_KERNEL_TYPES.discard("kuid_t")
