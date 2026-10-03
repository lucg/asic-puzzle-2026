# %%
import klayout.db as kdb
from pydantic import BaseModel

# %%
layout = kdb.Layout()
layout.read("04_final.gds")

# %%
top = layout.top_cell()

# %%
for idx in layout.layer_indexes():
    info = layout.get_info(idx)
    print(f"{info.layer}/{info.datatype}", top.bbox_per_layer(idx))


# %%
# https://skywater-pdk.readthedocs.io/en/main/rules/layers.html#gds-layers-information
WIRE_DTYPE = 20
LABEL_DTYPE = 5
VIA_DTYPE = 44


class SkyWaterLayerGroup(BaseModel):
    name: str
    layer: int

    @property
    def wire(self):
        return (self.layer, WIRE_DTYPE)

    @property
    def label(self):
        return (self.layer, LABEL_DTYPE)

    @property
    def via(self):
        return (self.layer, VIA_DTYPE)


SKYWATER_STACK = {
    name: SkyWaterLayerGroup(name=name, layer=layer)
    for name, layer in [
        ("nwell", 64),
        ("diff", 65),
        ("poly", 66),
        ("li1", 67),
        ("met1", 68),
        ("met2", 69),
        ("met3", 70),
        ("met4", 71),
        ("met5", 72),
        ("hvtp", 78),
        ("areaid", 81),
        ("text", 83),
        ("nsdm", 93),
        ("psdm", 94),
        ("npc", 95),
        ("pwell", 122),
        ("prBndry", 235),
    ]
}

SKYWATER_VIAS = {
    "li1": "met1",
    "met1": "met2",
    "met2": "met3",
    "met3": "met4",
    "met4": "met5",
}


# %%
for inst in top.each_inst():
    print(inst.cell.name, inst.trans)


# %%
def make_layer(layer_id: tuple[int, int], name, make_fn):
    return (
        make_fn(idx, name)
        if (idx := layout.find_layer(*layer_id)) is not None
        else None
    )


l2n = kdb.LayoutToNetlist(kdb.RecursiveShapeIterator(layout, top, []))

metal_layers = {}
for layer_group in SKYWATER_STACK.values():
    if (
        metal := make_layer(layer_group.wire, layer_group.name, l2n.make_polygon_layer)
    ) is None:
        continue
    l2n.connect(metal)
    metal_layers[layer_group.name] = metal

    if (
        label := make_layer(
            layer_group.label, f"{layer_group.name}_label", l2n.make_text_layer
        )
    ) is None:
        continue
    l2n.connect(metal, label)

for layer_from, layer_to in SKYWATER_VIAS.items():
    if (
        metal_layers.get(layer_from) is None
        or metal_layers.get(layer_to) is None
        or (
            via := make_layer(
                SKYWATER_STACK[layer_from].via,
                f"{SKYWATER_STACK[layer_from].name}_{SKYWATER_STACK[layer_to].name}_via",
                l2n.make_polygon_layer,
            )
        )
        is None
    ):
        continue

    l2n.connect(via)
    l2n.connect(metal_layers[layer_from], via)
    l2n.connect(via, metal_layers[layer_to])

l2n.extract_netlist()

# %%
assert l2n.is_extracted()
netlist = l2n.netlist()
netlist_top = netlist.circuit_by_name(layout.top_cell().name)

# %%
for sc in netlist_top.each_subcircuit():
    cell = sc.circuit_ref()
    conns = {}

    print()
    print(cell.name, sc.expanded_name())

    for pin in cell.each_pin():
        net = sc.net_for_pin(pin.id())
        if net is not None and pin.name():
            print(pin.name(), net.expanded_name())

# %%
