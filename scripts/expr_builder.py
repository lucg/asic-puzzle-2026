from collections.abc import Callable, Iterable
from graphlib import TopologicalSorter

import z3
from pydantic import BaseModel

type PortName = str
type NetName = str


class VerilogModuleInstance(BaseModel):
    instance_name: str
    module_name: str
    input_nets: dict[NetName, PortName]
    operation: Callable


class ExpressionBuilder:
    def __init__(self):
        self.inputs: set[str] = set()
        self.outputs: set[str] = set()
        self.drivers: dict[NetName, VerilogModuleInstance] = {}
        self.regs: dict[NetName, NetName] = {}  # output net -> input net mapping

    def _add_logic(self, symbol, out_port: PortName, operation):
        raise NotImplementedError

    def _add_reg(self, symbol, in_port: PortName, out_port: PortName):
        raise NotImplementedError

    def handle_symbol(self, symbol):
        match symbol.definitionName:
            case str() as s if s.startswith(
                (
                    "sky130_fd_sc_hd__clkbuf_",
                    "sky130_fd_sc_hd__decap_",
                    "sky130_fd_sc_hd__tapvpwrvgnd_",
                )
            ):
                return  # ignore
            case str() as s if s.startswith("sky130_fd_sc_hd__nand2_"):
                self._add_logic(symbol, "Y", lambda A, B: z3.Not(z3.And(A, B)))
            case str() as s if s.startswith("sky130_fd_sc_hd__and2_"):
                self._add_logic(symbol, "X", lambda A, B: z3.And(A, B))
            case str() as s if s.startswith("sky130_fd_sc_hd__xor2_"):
                self._add_logic(symbol, "X", lambda A, B: z3.Xor(A, B))
            case str() as s if s.startswith("sky130_fd_sc_hd__xnor2_"):
                self._add_logic(symbol, "Y", lambda A, B: z3.Not(z3.Xor(A, B)))
            case str() as s if s.startswith("sky130_fd_sc_hd__or2_"):
                self._add_logic(symbol, "X", lambda A, B: z3.Or(A, B))
            case str() as s if s.startswith("sky130_fd_sc_hd__nor2_"):
                self._add_logic(symbol, "Y", lambda A, B: z3.Not(z3.Or(A, B)))
            case str() as s if s.startswith("sky130_fd_sc_hd__a31o_"):
                self._add_logic(
                    symbol,
                    "X",
                    lambda A1, A2, A3, B1: z3.Or(z3.And(z3.And(A3, A1), A2), B1),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__a21o_"):
                self._add_logic(
                    symbol, "X", lambda A1, A2, B1: z3.Or(z3.And(A1, A2), B1)
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__a21bo_"):
                self._add_logic(
                    symbol,
                    "X",
                    lambda A1, A2, B1_N: z3.Not(z3.And(B1_N, z3.Not(z3.And(A2, A1)))),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__a21boi_"):
                self._add_logic(
                    symbol,
                    "Y",
                    lambda A1, A2, B1_N: z3.Not(z3.Or(z3.Not(B1_N), z3.And(A1, A2))),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__o21bai_"):
                self._add_logic(
                    symbol,
                    "Y",
                    lambda A1, A2, B1_N: z3.Not(z3.And(z3.Not(B1_N), z3.Or(A2, A1))),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__and3_"):
                self._add_logic(symbol, "X", lambda A, B, C: z3.And(z3.And(C, A), B))
            case str() as s if s.startswith("sky130_fd_sc_hd__and4bb_"):
                self._add_logic(
                    symbol,
                    "X",
                    lambda A_N, B_N, C, D: z3.And(
                        z3.And(z3.Not(z3.Or(A_N, B_N)), C), D
                    ),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__mux2_"):
                self._add_logic(symbol, "X", lambda A0, A1, S: z3.If(S, A1, A0))
            case str() as s if s.startswith("sky130_fd_sc_hd__dfrtp_"):
                self._add_reg(symbol, in_port="D", out_port="Q")

            case definitionName:
                raise ValueError(f"Unknown definition: {definitionName}")

    def get_order(self) -> Iterable[NetName]:
        return TopologicalSorter(
            {
                output_name: module.input_nets.keys()
                for output_name, module in self.drivers.items()
            }
        ).static_order()

    def step(self, order: Iterable[NetName], state, t):
        values = dict(state)
        for i in self.inputs:
            values[i] = z3.Bool(f"{i}@{t}")

        for net_name in order:
            if net_name in self.drivers:
                module = self.drivers[net_name]
                op_kwargs = {
                    input_port_name: values[input_net_name]
                    for input_net_name, input_port_name in module.input_nets.items()
                    if input_net_name in self.inputs
                    or input_net_name in self.drivers
                    or input_net_name in self.regs
                }
                values[net_name] = module.operation(**op_kwargs)
        return {net_name: values[net_name] for net_name in self.outputs} | {
            output_net_name: values[input_net_name]
            for output_net_name, input_net_name in self.regs.items()
        }

    def build_output_expressions(self, steps):
        order = list(self.get_order())

        state = {net_name: z3.BoolVal(False) for net_name in self.regs}

        for t in range(steps):
            state = self.step(order, state, t)
        return state
