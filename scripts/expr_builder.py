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
    operation: Callable | bool

    @property
    def is_const(self):
        return isinstance(self.operation, bool)

    def eval(self, **kwargs):
        return z3.BoolVal(self.operation) if self.is_const else self.operation(**kwargs)


class ExpressionBuilder:
    def __init__(self):
        self.inputs: set[str] = set()
        self.outputs: set[str] = set()
        self.drivers: dict[NetName, VerilogModuleInstance] = {}
        self.regs: dict[NetName, NetName] = {}  # output net -> input net mapping

    @classmethod
    def _unpack_ports(cls, symbol):
        raise NotImplementedError

    def _add_logic(self, symbol, out_port: PortName, operation):
        raise NotImplementedError

    def _add_reg(self, symbol, in_port: PortName, out_port: PortName):
        port_mapping = self._unpack_ports(symbol)
        self.regs[port_mapping[out_port]] = port_mapping[in_port]

    def _add_const(self, symbol, out_port: PortName, value: bool):
        self._add_logic(symbol, out_port, value)

    def filter_input_nets(
        self, instance: VerilogModuleInstance
    ) -> dict[NetName, PortName]:
        if instance.is_const:
            return {}
        return {
            input_net_name: input_port_name
            for input_net_name, input_port_name in instance.input_nets.items()
            if input_net_name in self.inputs
            or input_net_name in self.drivers
            or input_net_name in self.regs
        }

    def handle_symbol(self, symbol, name):
        match name:
            case str() as s if s.startswith(
                (
                    "sky130_fd_sc_hd__clkbuf_",
                    "sky130_fd_sc_hd__decap_",
                    "sky130_fd_sc_hd__tapvpwrvgnd_",
                    "sky130_fd_sc_hd__diode_",
                    "VIA_",
                )
            ):
                return  # ignore
            case str() as s if s.startswith("sky130_fd_sc_hd__buf_"):
                self._add_logic(symbol, "X", lambda A: A)
            case str() as s if s.startswith("sky130_fd_sc_hd__inv_"):
                self._add_logic(symbol, "Y", lambda A: z3.Not(A))
            case str() as s if s.startswith("sky130_fd_sc_hd__nand2_"):
                self._add_logic(symbol, "Y", lambda A, B: z3.Not(z3.And(A, B)))
            case str() as s if s.startswith("sky130_fd_sc_hd__nand2b_"):
                self._add_logic(symbol, "Y", lambda A_N, B: z3.Or(z3.Not(B), A_N))
            case str() as s if s.startswith("sky130_fd_sc_hd__and2_"):
                self._add_logic(symbol, "X", lambda A, B: z3.And(A, B))
            case str() as s if s.startswith("sky130_fd_sc_hd__and2b_"):
                self._add_logic(symbol, "X", lambda A_N, B: z3.And(z3.Not(A_N), B))
            case str() as s if s.startswith("sky130_fd_sc_hd__xor2_"):
                self._add_logic(symbol, "X", lambda A, B: z3.Xor(A, B))
            case str() as s if s.startswith("sky130_fd_sc_hd__xnor2_"):
                self._add_logic(symbol, "Y", lambda A, B: z3.Not(z3.Xor(A, B)))
            case str() as s if s.startswith("sky130_fd_sc_hd__or2_"):
                self._add_logic(symbol, "X", lambda A, B: z3.Or(A, B))
            case str() as s if s.startswith("sky130_fd_sc_hd__or3_"):
                self._add_logic(symbol, "X", lambda A, B, C: z3.Or(z3.Or(B, A), C))
            case str() as s if s.startswith("sky130_fd_sc_hd__or4_"):
                self._add_logic(
                    symbol, "X", lambda A, B, C, D: z3.Or(z3.Or(z3.Or(D, C), B), A)
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__nor2_"):
                self._add_logic(symbol, "Y", lambda A, B: z3.Not(z3.Or(A, B)))
            case str() as s if s.startswith("sky130_fd_sc_hd__nor3_"):
                self._add_logic(
                    symbol, "Y", lambda A, B, C: z3.Not(z3.Or(z3.Or(C, A), B))
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__and4b_"):
                self._add_logic(
                    symbol,
                    "X",
                    lambda A_N, B, C, D: z3.And(z3.And(z3.And(z3.Not(A_N), B), C), D),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__o211a_"):
                self._add_logic(
                    symbol,
                    "X",
                    lambda A1, A2, B1, C1: z3.And(z3.And(z3.Or(A2, A1), B1), C1),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__o211ai_"):
                self._add_logic(
                    symbol,
                    "Y",
                    lambda A1, A2, B1, C1: z3.Not(
                        z3.And(z3.And(C1, z3.Or(A2, A1)), B1)
                    ),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__a2111oi_"):
                self._add_logic(
                    symbol,
                    "Y",
                    lambda A1, A2, B1, C1, D1: z3.Not(
                        z3.Or(z3.Or(z3.Or(B1, C1), D1), z3.And(A1, A2))
                    ),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__a211o_"):
                self._add_logic(
                    symbol,
                    "X",
                    lambda A1, A2, B1, C1: z3.Or(z3.Or(z3.And(A1, A2), C1), B1),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__a211oi_"):
                self._add_logic(
                    symbol,
                    "Y",
                    lambda A1, A2, B1, C1: z3.Not(z3.Or(z3.Or(z3.And(A1, A2), B1), C1)),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__a221o_"):
                self._add_logic(
                    symbol,
                    "X",
                    lambda A1, A2, B1, B2, C1: z3.Or(
                        z3.Or(z3.And(A1, A2), z3.And(B1, B2)), C1
                    ),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__a221oi_"):
                self._add_logic(
                    symbol,
                    "Y",
                    lambda A1, A2, B1, B2, C1: z3.Or(
                        z3.Not(z3.Or(z3.Or(z3.And(B1, B2), C1), z3.And(A1, A2)))
                    ),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__o221a_"):
                self._add_logic(
                    symbol,
                    "X",
                    lambda A1, A2, B1, B2, C1: z3.And(
                        z3.And(z3.Or(B2, B1), z3.Or(A2, A1)), C1
                    ),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__o22a_"):
                self._add_logic(
                    symbol,
                    "X",
                    lambda A1, A2, B1, B2: z3.And(z3.Or(A2, A1), z3.Or(B2, B1)),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__a31o_"):
                self._add_logic(
                    symbol,
                    "X",
                    lambda A1, A2, A3, B1: z3.Or(z3.And(z3.And(A3, A1), A2), B1),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__a31oi_"):
                self._add_logic(
                    symbol,
                    "Y",
                    lambda A1, A2, A3, B1: z3.Not(
                        z3.Or(B1, z3.And(z3.And(A3, A1), A2))
                    ),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__a311o_"):
                self._add_logic(
                    symbol,
                    "X",
                    lambda A1, A2, A3, B1, C1: z3.Or(
                        z3.Or(z3.And(z3.And(A3, A1), A2), C1), B1
                    ),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__o32a_"):
                self._add_logic(
                    symbol,
                    "X",
                    lambda A1, A2, A3, B1, B2: z3.And(
                        z3.Or(z3.Or(A2, A1), A3), z3.Or(B2, B1)
                    ),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__o32ai_"):
                self._add_logic(
                    symbol,
                    "Y",
                    lambda A1, A2, A3, B1, B2: z3.Or(
                        z3.Not(z3.Or(B1, B2)), z3.Not(z3.Or(z3.Or(A3, A1), A2))
                    ),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__o31ai_"):
                self._add_logic(
                    symbol,
                    "Y",
                    lambda A1, A2, A3, B1: z3.And(
                        z3.Not(z3.And(B1, z3.Or(z3.Or(A2, A1), A3)))
                    ),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__o21a_"):
                self._add_logic(
                    symbol,
                    "X",
                    lambda A1, A2, B1: z3.And(z3.Or(A2, A2), B1),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__o21ai_"):
                self._add_logic(
                    symbol,
                    "Y",
                    lambda A1, A2, B1: z3.Not(z3.And(B1, z3.Or(A2, A1))),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__a21o_"):
                self._add_logic(
                    symbol, "X", lambda A1, A2, B1: z3.Or(z3.And(A1, A2), B1)
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__a21oi_"):
                self._add_logic(
                    symbol, "Y", lambda A1, A2, B1: z3.Not(z3.Or(B1, z3.And(A1, A2)))
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__o21ba_"):
                self._add_logic(
                    symbol,
                    "X",
                    lambda A1, A2, B1_N: z3.Not(z3.Or(B1_N, z3.Not(z3.Or(A1, A2)))),
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
            case str() as s if s.startswith("sky130_fd_sc_hd__a22o_"):
                self._add_logic(
                    symbol,
                    "X",
                    lambda A1, A2, B1, B2: z3.Or(z3.And(A1, A2), z3.And(B1, B2)),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__a22oi_"):
                self._add_logic(
                    symbol,
                    "Y",
                    lambda A1, A2, B1, B2: z3.And(
                        z3.Not(z3.And(A2, A1)), z3.Not(z3.And(B2, B1))
                    ),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__o22ai_"):
                self._add_logic(
                    symbol,
                    "Y",
                    lambda A1, A2, B1, B2: z3.Or(
                        z3.Not(z3.Or(A1, A2)), z3.Not(z3.Or(B1, B2))
                    ),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__o31a_"):
                self._add_logic(
                    symbol,
                    "X",
                    lambda A2, A1, A3, B1: z3.And(z3.Or(z3.Or(A2, A1), A3), B1),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__a32o_"):
                self._add_logic(
                    symbol,
                    "X",
                    lambda A1, A2, A3, B1, B2: z3.Or(
                        z3.And(B1, B2), z3.And(z3.And(A3, A1), A2)
                    ),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__o311a_"):
                self._add_logic(
                    symbol,
                    "X",
                    lambda A2, A1, A3, B1, C1: z3.And(
                        z3.And(z3.Or(z3.Or(A2, A1), A3), B1), C1
                    ),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__a41oi_"):
                self._add_logic(
                    symbol,
                    "Y",
                    lambda A1, A2, A3, A4, B1: z3.Not(
                        z3.Or(B1, z3.And(z3.And(z3.And(A1, A2), A3), A4))
                    ),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__o2bb2a_"):
                self._add_logic(
                    symbol,
                    "X",
                    lambda A1_N, A2_N, B1, B2: z3.And(
                        z3.Not(z3.And(A2_N, A1_N)), z3.Or(B2, B1)
                    ),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__and3_"):
                self._add_logic(symbol, "X", lambda A, B, C: z3.And(z3.And(C, A), B))
            case str() as s if s.startswith("sky130_fd_sc_hd__and3b_"):
                self._add_logic(
                    symbol, "X", lambda A_N, B, C: z3.And(z3.And(C, z3.Not(A_N)), B)
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__nand3_"):
                self._add_logic(
                    symbol, "Y", lambda A, B, C: z3.Not(z3.And(z3.And(B, A), C))
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__nand4_"):
                self._add_logic(
                    symbol,
                    "Y",
                    lambda A, B, C, D: z3.Or(z3.And(z3.And(z3.And(D, C), B), A)),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__or3b_"):
                self._add_logic(
                    symbol, "X", lambda A, B, C_N: z3.Or(z3.Or(B, A), z3.Not(C_N))
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__or4b_"):
                self._add_logic(
                    symbol,
                    "X",
                    lambda A, B, C, D_N: z3.Or(z3.Or(z3.Or(z3.Not(D_N), C), B), A),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__or4bb_"):
                self._add_logic(
                    symbol,
                    "X",
                    lambda A, B, C_N, D_N: z3.Or(z3.Or(B, A), z3.Not(z3.And(D_N, C_N))),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__nor4_"):
                self._add_logic(
                    symbol,
                    "Y",
                    lambda A, B, C, D: z3.Not(z3.Or(z3.Or(z3.Or(A, B), C), D)),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__nor4b_"):
                self._add_logic(
                    symbol,
                    "Y",
                    lambda A, B, C, D_N: z3.Not(
                        z3.Or(z3.Or(z3.Or(A, B), C), z3.Not(D_N))
                    ),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__nor3b_"):
                self._add_logic(
                    symbol, "Y", lambda A, B, C_N: z3.And(C_N, z3.Not(z3.Or(A, B)))
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__nand3b_"):
                self._add_logic(
                    symbol,
                    "Y",
                    lambda A_N, B, C: z3.Not(z3.And(z3.And(B, z3.Not(A_N)), C)),
                )
            case str() as s if s.startswith("sky130_fd_sc_hd__and4_"):
                self._add_logic(
                    symbol, "X", lambda A, B, C, D: z3.And(z3.And(z3.And(A, B), C), D)
                )
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
            case str() as s if s.startswith("sky130_fd_sc_hd__dfxtp_"):
                self._add_reg(symbol, in_port="D", out_port="Q")
            case str() as s if s.startswith("sky130_fd_sc_hd__dfstp_"):
                self._add_reg(symbol, in_port="D", out_port="Q")
            case str() as s if s.startswith("sky130_fd_sc_hd__conb_"):
                self._add_const(symbol, "HI", True)
                self._add_const(symbol, "LO", False)
            case _:
                raise ValueError(f"Unknown symbol: {name}")

    def get_order(self) -> Iterable[NetName]:
        return TopologicalSorter(
            {
                output_name: self.filter_input_nets(module).keys()
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
                    for input_net_name, input_port_name in self.filter_input_nets(
                        module
                    ).items()
                }
                values[net_name] = module.eval(**op_kwargs)
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

    def check_sat(self, s: z3.Solver, state: dict):
        print("Check satisfiability... ", end="")
        if s.check() == z3.sat:
            print("passed!")
            m = s.model()
    
            input_packed = {}
            var_times = {}
            for d in m.decls():
                input_name, t = d.name().rsplit("@", 1)
                t = int(t)
    
                input_packed[input_name] = input_packed.get(input_name, 0) | (
                    bool(m[d]) << t
                )
                var_times[t] = var_times.get(t, {}) | {input_name: m[d]}
    
            for time in sorted(var_times.keys()):
                print(f"@ {time}")
                var_values: dict = var_times[time]
                for var_name in sorted(var_values.keys()):
                    print(f"  {var_name}={var_values[var_name]}")
    
            print()
            for input_name in sorted(input_packed.keys()):
                print(f"{input_name:2} packed: {input_packed[input_name]:b}")
        else:
            print("UNSAT")
