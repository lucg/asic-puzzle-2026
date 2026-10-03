# SPDX-FileCopyrightText: Michael Popoloski
# SPDX-License-Identifier: MIT

# modified from:
# https://github.com/MikePopoloski/slang/blob/292805396d9c9952a457f9d14ff900c8f6a3d3f2/pyslang/examples/extract_logic_names.py

"""Extract the names of all declarations from SystemVerilog code.

This example demonstrates how to use the pyslang visitor system to traverse
the AST and extract the names of all variables .

Example usage:
    python extract_names.py

The script will parse a sample SystemVerilog module and print the names of
all relevant declarations found in the code.
"""

import traceback
from collections.abc import Callable, Iterable
from graphlib import TopologicalSorter

import z3
from pydantic import BaseModel
from pyslang import DiagnosticEngine, TextDiagnosticClient
from pyslang.ast import (
    ArgumentDirection,
    Compilation,
    NamedValueExpression,
    NetSymbol,
    PortSymbol,
    SimpleAssertionExpr,
    UninstantiatedDefSymbol,
)
from pyslang.parsing import Token
from pyslang.syntax import SyntaxNode, SyntaxTree

type PortName = str
type NetName = str

class VerilogModuleInstance(BaseModel):
    instance_name: str
    module_name: str
    input_nets: dict[NetName, PortName]
    operation: Callable


class DeclarationExtractor:
    """
    Visitor class to extract names of declarations.

    1. Filter for specific symbol types (VariableSymbol)
    2. Check the type of variables
    3. Extract and collect symbol names
    """

    def __init__(self):
        self.inputs: set[str] = set()
        self.outputs: set[str] = set()
        self.drivers: dict[NetName, VerilogModuleInstance] = {}
        self.reg: dict[NetName, NetName] = {}  # output net -> input net mapping

    @staticmethod
    def _unpack_port_connection(connection):
        match connection:
            case SimpleAssertionExpr(
                expr=NamedValueExpression(
                    symbol=NetSymbol(name=symbol_name),
                ),
            ):
                return symbol_name

    @classmethod
    def _unpack_ports(cls, symbol: UninstantiatedDefSymbol):
        unpacked_connections = (
            x
            for conn in symbol.portConnections
            if (x := cls._unpack_port_connection(conn)) is not None
        )
        return {
            name: connection
            for name, connection in zip(symbol.portNames, unpacked_connections)
        }

    def _add_logic(
        self, symbol: UninstantiatedDefSymbol, out_port: PortName, operation
    ):
        out_net: NetName = (input_ports := self._unpack_ports(symbol)).pop(out_port)

        input_nets = {
            net_name: port_name for port_name, net_name in input_ports.items()
        }

        self.drivers[out_net] = VerilogModuleInstance(
            instance_name=symbol.name,
            module_name=symbol.definitionName,
            input_nets=input_nets,
            operation=operation,
        )

    def _add_reg(
        self, symbol: UninstantiatedDefSymbol, in_port: PortName, out_port: PortName
    ):
        port_mapping = self._unpack_ports(symbol)
        self.reg[port_mapping[out_port]] = port_mapping[in_port]

    def _handle_symbol(self, symbol: UninstantiatedDefSymbol):
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

    def __call__(self, obj: Token | SyntaxNode) -> None:
        """
        Visit method called for each node in the AST.

        Args:
            obj: The current AST node being visited. Can be a Token or SyntaxNode.
                 We're specifically interested in VariableSymbol nodes.
        """
        # Check if this is a variable symbol (includes logic declarations)
        match obj:
            case PortSymbol() as port:
                port_set = (
                    self.inputs
                    if port.direction == ArgumentDirection.In
                    else self.outputs
                )
                port_set.add(port.name)
            case UninstantiatedDefSymbol():
                self._handle_symbol(obj)

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
                    port_name: values[net_name]
                    for net_name, port_name in module.input_nets.items()
                }
                values[net_name] = module.operation(**op_kwargs)
        return {net_name: values[net_name] for net_name in self.outputs} | {
            output_net_name: values[input_net_name]
            for output_net_name, input_net_name in self.reg.items()
        }


def build_output_expressions(extractor: DeclarationExtractor, steps):
    order = list(extractor.get_order())

    state = {net_name: z3.BoolVal(False) for net_name in extractor.reg}

    for t in range(steps):
        state = extractor.step(order, state, t)
    return state

def extract_declaration_names(systemverilog_code: str) -> list[str]:
    """
    Extract declaration names from SystemVerilog code.

    Args:
        systemverilog_code: A string containing SystemVerilog source code.

    Returns:
        A list of strings containing the names of all logic declarations.
    """
    # Parse the SystemVerilog code into a syntax tree
    tree = SyntaxTree.fromText(systemverilog_code)

    # Create a compilation unit and add the syntax tree
    compilation = Compilation()
    compilation.addSyntaxTree(tree)

    # Handle diagnostics.
    diagnostics = compilation.getAllDiagnostics()

    diagClient = TextDiagnosticClient()
    diagEngine = DiagnosticEngine(compilation.sourceManager)
    diagEngine.addClient(diagClient)

    has_error = False
    for diag in diagnostics:
        diagEngine.issue(diag)
        has_error = diag.isError() or has_error

    # print(diagClient.getString())

    # if has_error:
    #     raise RuntimeError("Compilation had errors")

    # Create our visitor to extract logic declaration names
    extractor = DeclarationExtractor()

    # Visit all nodes in the compilation root
    compilation.getRoot().visit(extractor)

    info = (
        [f"input: {p}" for p in extractor.inputs]
        + [f"outputs: {p}" for p in extractor.outputs]
        + [
            f"{i.instance_name} {i.module_name} {i.input_nets}"
            for i in extractor.drivers.values()
        ]
    )

    state = build_output_expressions(extractor, 10)

    s = z3.Solver()
    s.add(z3.And(state["S"] == z3.BoolVal(True)))

    print("Check satisfiability... ", end="")
    if s.check() == z3.sat:
        print("passed!")
        m = s.model()

        var_times = {}
        for d in m.decls():
            input_name, t = d.name().rsplit("@", 1)
            t = int(t)

            var_times[t] = var_times.get(t, {}) | {input_name: m[d]}

        for time in sorted(var_times.keys()):
            print(f"@ {time}")
            var_values: dict = var_times[time]
            for var_name in sorted(var_values.keys()):
                print(f"  {var_name}={var_values[var_name]}")
    else:
        print("UNSAT")
    print()

    return info


def extract_declarations_from_file(filepath: str) -> list[str]:
    """
    Extract declaration names from a SystemVerilog file.

    Args:
        filepath: Path to a SystemVerilog file.

    Returns:
        A list of strings containing the names of all declarations.
    """
    with open(filepath, "r", encoding="utf-8") as file:
        content = file.read()
    return extract_declaration_names(content)


def main():
    """Main function demonstrating the declaration extractor."""

    import argparse

    parser = argparse.ArgumentParser(
        description="Extract declaration names from SystemVerilog code"
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="SystemVerilog files to process (if none provided, uses built-in example)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show verbose output including sample code",
    )

    args = parser.parse_args()

    print("SystemVerilog Declaration Extractor")
    print("=" * 50)
    print()

    if args.files:
        # Process provided files
        for filepath in args.files:
            print(f"Processing file: {filepath}")
            try:
                names = extract_declarations_from_file(filepath)

                if names:
                    print(f"Found {len(names)} declarations:")
                    for i, name in enumerate(names, 1):
                        print(f"  {i:2d}. {name}")
                else:
                    print("No declarations found.")

            except Exception as e:  # noqa: BLE001 - example keeps going on any per-file error
                print(f"Error: {e}")
                traceback.print_exc()
            print()
    else:
        # Use built-in example
        sample_code = """
        module example_module(
            input  logic        clk,           // Input logic signal
            input  logic        reset_n,       // Active-low reset
            input  logic [7:0]  data_in,       // 8-bit input data bus
            output logic [15:0] data_out,      // 16-bit output data bus
            output logic        valid_out      // Output valid signal
        );

            // Internal logic declarations
            logic [3:0]  counter;              // 4-bit counter
            logic        enable;               // Enable signal
            logic [1:0]  state, next_state;    // State machine signals
            logic [7:0]  temp_data;            // Temporary data storage

            // Some non-logic declarations for comparison
            bit          bit_signal;           // This is 'bit', not 'logic'
            reg [7:0]    reg_signal;           // This is 'reg', not 'logic'
            wire         wire_signal;          // This is a net, not a variable
            int          int_var;              // This is 'int', not a variable

            // More logic declarations
            logic        ready;                // Ready signal
            logic [31:0] result;               // 32-bit result

            always_ff @(posedge clk or negedge reset_n) begin
                if (!reset_n) begin
                    counter <= 4'b0;
                    state <= 2'b00;
                end else begin
                    counter <= counter + 1;
                    state <= next_state;
                end
            end

            always_comb begin
                next_state = state + 1;
                temp_data = data_in;
                ready = (counter == 4'hF);
                data_out = {temp_data, temp_data};
                valid_out = ready;
            end

        endmodule
        """

        if args.verbose:
            print("Sample SystemVerilog code:")
            print("-" * 30)
            print(sample_code)
            print("-" * 30)
            print()

        try:
            # Extract declaration names
            names = extract_declaration_names(sample_code)

            # Display results
            if names:
                print(f"Found {len(names)} declarations:")
                print()
                for i, name in enumerate(names, 1):
                    print(f"  {i:2d}. {name}")
            else:
                print("No declarations found.")

            print()
            print("Note: This extractor only finds variables declared with the")
            print("'logic' keyword. It excludes 'bit', 'reg', nets (wire), and")
            print("other data types, even if they are functionally similar.")
            print()
            print("Usage: python extract_names.py [file1.sv file2.sv ...]")
            print("       python extract_names.py --verbose  # Show sample code")

        except Exception as e:  # noqa: BLE001 - top-level guard reports any failure
            print(f"Error processing SystemVerilog code: {e}")
            print("Make sure pyslang is properly installed.")


if __name__ == "__main__":
    main()
