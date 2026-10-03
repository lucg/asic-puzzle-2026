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

import z3
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

from .expr_builder import ExpressionBuilder as ExpressionBuilderBase
from .expr_builder import NetName, PortName, VerilogModuleInstance


class ExpressionBuilder(ExpressionBuilderBase):
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
        self.regs[port_mapping[out_port]] = port_mapping[in_port]

    def __call__(self, obj: Token | SyntaxNode) -> None:
        """
        Visit method called for each node in the AST.

        Args:
            obj: The current AST node being visited. Can be a Token or SyntaxNode.
                 We're specifically interested in VariableSymbol nodes.
        """
        # Check if this is a variable symbol (includes logic declarations)
        match obj:
            case PortSymbol(direction=ArgumentDirection.In, name=name):
                self.inputs.add(name)
            case PortSymbol(direction=ArgumentDirection.Out, name=name):
                self.outputs.add(name)
            case UninstantiatedDefSymbol():
                self.handle_symbol(obj)


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
    extractor = ExpressionBuilder()

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

    state = extractor.build_output_expressions(10)

    s = z3.Solver()
    s.add(z3.And(state["S"] == z3.BoolVal(True)))

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
