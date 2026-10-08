import klayout.db as kdb
import z3
from pydantic import BaseModel

from .expr_builder import ExpressionBuilder as ExpressionBuilderBase
from .expr_builder import NetName, PortName, VerilogModuleInstance

type SymbolType = kdb.SubCircuit


class ExpressionBuilder(ExpressionBuilderBase):
    @classmethod
    def _unpack_ports(cls, sc: SymbolType):
        cell = sc.circuit_ref()
        return {
            pin_name: net.expanded_name()
            for pin in cell.each_pin()
            if (net := sc.net_for_pin(pin.id())) is not None
            and (pin_name := pin.name())
        }

    def _add_logic(self, sc: SymbolType, out_port: PortName, operation):
        out_net: NetName = (input_ports := self._unpack_ports(sc)).pop(out_port)

        input_nets = {
            net_name: port_name for port_name, net_name in input_ports.items()
        }
        cell = sc.circuit_ref()

        self.drivers[out_net] = VerilogModuleInstance(
            instance_name=sc.expanded_name(),
            module_name=cell.name,
            input_nets=input_nets,
            operation=operation,
        )


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


def build_netlist(layout, cell):
    def make_layer(layer_id: tuple[int, int], name, make_fn):
        return (
            make_fn(idx, name)
            if (idx := layout.find_layer(*layer_id)) is not None
            else None
        )

    l2n = kdb.LayoutToNetlist(kdb.RecursiveShapeIterator(layout, cell, []))

    metal_layers = {}
    for layer_group in SKYWATER_STACK.values():
        if (
            metal := make_layer(
                layer_group.wire, layer_group.name, l2n.make_polygon_layer
            )
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

    assert l2n.is_extracted()
    return l2n


if __name__ == "__main__":
    layout = kdb.Layout()
    layout.read("warmup/04_final.gds")
    top = layout.top_cell()

    netlist = (l2n := build_netlist(layout, top)).netlist()
    netlist.make_top_level_pins()

    netlist_top = netlist.circuit_by_name(top.name)

    builder = ExpressionBuilder()

    for sc in netlist_top.each_subcircuit():
        builder.handle_symbol(sc, sc.circuit_ref().name)

    driven = set(builder.drivers) | set(builder.regs)
    for top_pin in netlist_top.each_pin():
        top_pin_name = top_pin.name()
        if top_pin_name not in {"A", "B", "S", "en", "clk", "rst_n"}:
            continue
        (builder.outputs if top_pin_name in driven else builder.inputs).add(
            top_pin_name
        )

    state = builder.build_output_expressions(10)

    s = z3.Solver()
    s.add(z3.And(state["S"] == z3.BoolVal(True)))

    builder.check_sat(s, state)
    print()
