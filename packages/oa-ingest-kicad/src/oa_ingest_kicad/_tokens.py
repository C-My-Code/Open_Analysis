# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Token vocabularies per context. Anything outside them is logged as ``INGEST.UNKNOWN_TOKEN``.

The sets are deliberately generous: a token listed here is one the reader either consumes or
knowingly ignores (annotation, silkscreen, text rendering, ...).
"""

from __future__ import annotations

_TEXT_EFFECTS = {"effects", "font", "justify", "hide", "unlocked", "knockout", "render_cache"}

TOP = frozenset(
    {
        "version", "generator", "generator_version", "general", "paper", "title_block", "layers",
        "setup", "net", "footprint", "module", "segment", "arc", "via", "zone", "group",
        "generated", "embedded_fonts", "embedded_files", "property", "image", "dimension",
        "target", "gr_text", "gr_text_box", "gr_line", "gr_arc", "gr_circle", "gr_rect",
        "gr_poly", "gr_curve", "gr_bbox", "gr_table", "table", "barcode", "variant",
        "variants", "net_class", "host", "uuid", "page", "tstamp",
    }
)  # fmt: skip

FOOTPRINT = frozenset(
    {
        "layer", "uuid", "tstamp", "tedit", "at", "descr", "tags", "property", "path",
        "sheetname", "sheetfile", "sheet_name", "sheet_file", "attr", "pad", "zone", "model",
        "group", "version", "generator", "generator_version", "locked", "placed", "fp_line",
        "fp_arc", "fp_circle", "fp_rect", "fp_poly", "fp_curve", "fp_text", "fp_text_box",
        "dimension", "image", "barcode", "solder_mask_margin", "solder_paste_margin",
        "solder_paste_ratio", "solder_paste_abs_margin", "clearance", "zone_connect",
        "thermal_width", "thermal_gap", "net_tie_pad_groups", "duplicate_pad_numbers_are_jumpers",
        "jumper_pad_groups", "component_classes", "embedded_fonts", "embedded_files",
        "private_layers", "teardrops", "autoplace_cost90", "autoplace_cost180", "fp_table",
        "table", "units", "variants", "variant", "net", "stackup",
    }
)  # fmt: skip

PAD = frozenset(
    {
        "at", "size", "drill", "layers", "net", "pinfunction", "pintype", "roundrect_rratio",
        "chamfer_ratio", "chamfer", "rect_delta", "offset", "uuid", "tstamp", "locked",
        "remove_unused_layers", "keep_end_layers", "zone_connect", "thermal_bridge_width",
        "thermal_width", "thermal_gap", "thermal_bridge_angle", "clearance", "die_length",
        "die_delay", "solder_mask_margin", "solder_paste_margin", "solder_paste_margin_ratio",
        "solder_paste_ratio", "options", "primitives", "padstack", "property", "teardrops",
        "zone_layer_connections", "tenting", "covering", "plugging", "filling", "capping",
        "backdrill", "tertiary_drill", "secondary_drill", "front_post_machining",
        "back_post_machining", "net_name", "free", "decoration", "stackup", "mask_margin",
    }
)  # fmt: skip

VIA = frozenset(
    {
        "at", "size", "drill", "layers", "net", "uuid", "tstamp", "locked", "free",
        "remove_unused_layers", "keep_end_layers", "zone_layer_connections", "teardrops",
        "padstack", "backdrill", "secondary_drill", "tertiary_drill", "tenting", "covering",
        "plugging", "filling", "capping", "front_post_machining", "back_post_machining",
        "status", "net_name",
    }
)  # fmt: skip

TRACK = frozenset(
    {"start", "mid", "end", "width", "layer", "net", "uuid", "tstamp", "locked", "status",
     "net_name", "teardrops"}
)  # fmt: skip

ZONE = frozenset(
    {
        "net", "net_name", "layer", "layers", "uuid", "tstamp", "hatch", "priority",
        "connect_pads", "min_thickness", "filled_areas_thickness", "fill", "keepout", "polygon",
        "filled_polygon", "fill_segments", "teardrop", "name", "locked", "placement",
        "attr", "islands", "filled_areas_polygon", "outline_hatch", "layer_overrides",
        "property", "zone_connect",
    }
)  # fmt: skip

SETUP = frozenset(
    {
        "stackup", "pad_to_mask_clearance", "solder_mask_min_width", "pad_to_paste_clearance",
        "pad_to_paste_clearance_ratio", "allow_soldermask_bridges_in_footprints", "tenting",
        "covering", "plugging", "capping", "filling", "aux_axis_origin", "grid_origin",
        "pcbplotparams", "last_trace_width", "user_trace_width", "trace_clearance",
        "zone_clearance", "zone_45_only", "trace_min", "via_size", "via_drill", "via_min_size",
        "via_min_drill", "uvia_size", "uvia_drill", "uvias_allowed", "uvia_min_size",
        "uvia_min_drill", "max_error", "defaults", "rules", "edge_clearance", "segment_width",
        "edge_width", "pcb_text_width", "pcb_text_size", "mod_edge_width", "mod_text_size",
        "mod_text_width", "pad_size", "pad_drill", "visible_elements", "user_diff_pair",
        "user_via", "net_class",
    }
)  # fmt: skip

STACKUP_LAYER = frozenset(
    {"type", "color", "thickness", "material", "epsilon_r", "loss_tangent", "addsublayer"}
)

STACKUP = frozenset(
    {"layer", "copper_finish", "dielectric_constraints", "edge_connector", "castellated_pads",
     "edge_plating"}
)  # fmt: skip

CONTEXTS: dict[str, frozenset[str]] = {
    "kicad_pcb": TOP,
    "footprint": FOOTPRINT | _TEXT_EFFECTS,
    "pad": PAD,
    "via": VIA,
    "segment": TRACK,
    "arc": TRACK,
    "zone": ZONE,
    "setup": SETUP,
    "stackup": STACKUP,
    "stackup layer": STACKUP_LAYER,
}
