"""Emit Python tool definitions for static plan validation."""
import json
from typing import Any, Optional
from blastai.agents.models import SMCPTool, SMCPToolType, ToolExecutorType
from blastai.agents.schema_utils import generate_nested_pydantic_classes, json_type_to_python, snake_to_pascal

class DefinitionEmitter:
    def __init__(self, agent, state_aware=True):
        self.agent = agent
        self.state_aware = state_aware

    def _build_definition_code(self) -> str:
        """
        Build definition code showing tool signatures with precondition/postcondition assertions.

        This code is part of the Python code block that gets generated, showing the
        developer what tools are available and their contracts.

        Returns definitions as Python code with:
        - STATE dictionary for tracking page, selectedRestaurant, selectedCategory, current_url
        - Pydantic models for input parameters (matching JSON schemas)
        - Proper typed function signatures
        - Assert statements for preconditions (checking STATE)
        - Function implementation placeholder
        - Assert statements for postconditions (checking STATE after update)
        """
        lines = []

        # Import Pydantic for type definitions and other required modules
        lines.append("from pydantic import BaseModel, Field")
        lines.append("from typing import Optional, List, Dict, Any, Union, Literal")
        lines.append("import re")
        lines.append("")

        # Initialize STATE dictionary dynamically from all tools' pre/post variables
        # Only include STATE if state_aware is True
        if self.state_aware:
            # Collect all state keys referenced in pre/post and URL patterns
            state_keys = set()
            for t in self.agent.tools:
                if t.tool_executor_type == ToolExecutorType.SMCP:
                    if getattr(t, "pre", None) and isinstance(t.pre, dict):
                        state_keys.update(t.pre.keys())
                    if getattr(t, "post", None) and isinstance(t.post, dict):
                        state_keys.update(t.post.keys())
                    # Note: We no longer track current_url in STATE - use get_url() instead

            # Make deterministic ordering
            state_keys_list = sorted(list(state_keys))

            # Define STATE dictionary (provided at runtime)
            if state_keys_list:
                state_init = (
                    "{" + ", ".join([f'"{k}": None' for k in state_keys_list]) + "}"
                )
                lines.append(f"STATE: Dict[str, Any] = {state_init}")
                lines.append("")

        # Track which tools have been called (for pre_tools enforcement) - only if state_aware
        if self.state_aware:
            lines.append("# Track which tools have been called")
            lines.append("TOOLS_CALLED: set[str] = set()")
            lines.append("")

        # Add SMCP tool definitions with precondition/postcondition assertions
        for tool in self.agent.tools:
            if tool.tool_executor_type == ToolExecutorType.SMCP:
                smcp_tool = tool
                assert isinstance(smcp_tool, SMCPTool)

                # Skip OBSERVE tools - they're called automatically, not by user code
                if smcp_tool.type == SMCPToolType.OBSERVE:
                    continue

                # Generate Pydantic model for input parameters if they exist
                has_params = smcp_tool.input_schema and smcp_tool.input_schema.get(
                    "properties"
                )
                if has_params:
                    model_name = f"{snake_to_pascal(smcp_tool.name)}Input"
                    lines.append(f"class {model_name}(BaseModel):")

                    properties = smcp_tool.input_schema["properties"]
                    required = smcp_tool.input_schema.get("required", [])

                    for param_name, param_schema in properties.items():
                        param_type = json_type_to_python(
                            param_schema.get("type", "str")
                        )
                        is_required = param_name in required
                        param_desc = param_schema.get("description", "")

                        if is_required:
                            lines.append(
                                f'    {param_name}: {param_type} = Field(..., description="{param_desc}")'
                            )
                        else:
                            lines.append(
                                f'    {param_name}: Optional[{param_type}] = Field(None, description="{param_desc}")'
                            )

                    lines.append("")

                # Generate Pydantic model for output schema if it exists
                has_output = smcp_tool.output_schema and smcp_tool.output_schema.get(
                    "properties"
                )
                output_model_name = None
                if has_output:
                    output_model_name = f"{snake_to_pascal(smcp_tool.name)}Output"
                    # Use recursive generation to handle nested schemas
                    # This will generate all nested classes and return the root class name
                    temp_lines = []
                    generate_nested_pydantic_classes(
                        smcp_tool.output_schema, output_model_name, temp_lines
                    )
                    lines.extend(temp_lines)

                # Generate function signature with proper types (not **kwargs)
                # Return the Pydantic model for type safety and attribute access
                return_type = (
                    output_model_name if output_model_name else "Dict[str, Any]"
                )

                if has_params:
                    # Build typed parameters
                    properties = smcp_tool.input_schema["properties"]
                    required = smcp_tool.input_schema.get("required", [])
                    params = []
                    # Ensure required parameters come first in signature to avoid
                    # "non-default argument follows default argument" syntax errors.
                    # To make this robust against arbitrary ordering in the schema
                    # and missing/None `required`, iterate properties in their
                    # defined order and partition them into required vs optional.
                    properties_items = list(properties.items())
                    required_set = set(required or [])

                    # First, add required params in the properties order
                    for param_name, param_schema in properties_items:
                        if param_name in required_set:
                            param_type = json_type_to_python(
                                param_schema.get("type", "str")
                            )
                            params.append(f"{param_name}: {param_type}")

                    # Then add optional params (with defaults) in the properties order
                    for param_name, param_schema in properties_items:
                        if param_name in required_set:
                            continue
                        param_type = json_type_to_python(
                            param_schema.get("type", "str")
                        )
                        params.append(f"{param_name}: Optional[{param_type}] = None")

                    params_str = ", ".join(params)
                    lines.append(
                        f"async def {smcp_tool.name}({params_str}) -> {return_type}:"
                    )
                else:
                    lines.append(f"async def {smcp_tool.name}() -> {return_type}:")

                lines.append('    """')
                lines.append(f"    {smcp_tool.description}")

                # Add pre_tools requirements to docstring for LLM guidance
                if (
                    self.state_aware
                    and hasattr(smcp_tool, "pre_tools")
                    and smcp_tool.pre_tools
                ):
                    all_required = set()
                    for tool_names in smcp_tool.pre_tools.values():
                        all_required.update(tool_names)
                    if all_required:
                        lines.append("    ")
                        for req_tool in sorted(all_required):
                            lines.append(f"    Requires: Call {req_tool}() first.")

                lines.append('    """')

                # PRE_TOOLS ASSERTIONS (check that required tools have been called) - only if state_aware
                if (
                    self.state_aware
                    and hasattr(smcp_tool, "pre_tools")
                    and smcp_tool.pre_tools
                ):
                    # pre_tools is Dict[str, List[str]] mapping param names to required tool names
                    # Flatten to get all required tools
                    required_tools = set()
                    for param_name, tool_names in smcp_tool.pre_tools.items():
                        required_tools.update(tool_names)

                    if required_tools:
                        for required_tool in sorted(required_tools):
                            lines.append(
                                f'    assert "{required_tool}" in TOOLS_CALLED, "Must call {required_tool} before {smcp_tool.name}"'
                            )

                # PRECONDITIONS (assert statements checking STATE)
                if self.state_aware:
                    # URL pattern precondition - use get_url() to get current URL dynamically
                    if smcp_tool.pre_path and smcp_tool.pre_path != "":
                        url_pattern = smcp_tool.pre_path.replace("*", ".*")
                        lines.append("    current_url = await get_url()")
                        lines.append(
                            f'    assert re.match(r"{url_pattern}", current_url)'
                        )

                    # State preconditions with pattern support
                    if smcp_tool.pre and isinstance(smcp_tool.pre, dict):
                        for key, value in smcp_tool.pre.items():
                            # Skip None values - they don't impose constraints
                            if value is None:
                                continue
                            assertion = self._generate_assertion(
                                key, value, "precondition", state_check=True
                            )
                            if assertion:
                                lines.append(f"    {assertion}")

                # IMPLEMENTATION (stub - runtime provides actual implementation)
                lines.append("    ...")

                # POSTCONDITIONS (assert statements checking STATE after update)
                if (
                    self.state_aware
                    and smcp_tool.post
                    and isinstance(smcp_tool.post, dict)
                ):
                    for key, value in smcp_tool.post.items():
                        # Skip None values - they don't impose constraints
                        if value is None:
                            continue

                        # For postconditions, check STATE after update
                        if isinstance(value, str) and value.startswith("$"):
                            # Reference to input parameter
                            param_ref = value[1:]  # Remove $
                            lines.append(f'    assert STATE["{key}"] == {param_ref}')
                        else:
                            assertion = self._generate_assertion(
                                key, value, "postcondition", state_check=True
                            )
                            if assertion:
                                lines.append(f"    {assertion}")

                # Track that this tool was called (conceptually - runtime handles this) - only if state_aware
                if self.state_aware:
                    lines.append(f'    TOOLS_CALLED.add("{smcp_tool.name}")')
                    lines.append("")

        # Add core tool definitions (simplified signatures)
        for tool in self.agent.tools:
            if tool.tool_executor_type == ToolExecutorType.CORE:
                if tool.name == "update_smcp_tool":
                    lines.append(
                        "async def update_smcp_tool(name: str, type: str, execute: str, is_ready: str, is_completed: str, preconditions: List[Dict[str, str]], postconditions: List[Dict[str, str]], input_parameters: List[str]) -> Dict[str, Any]:"
                    )
                elif tool.name == "remove_smcp_tool":
                    lines.append(
                        "async def remove_smcp_tool(name: str) -> Dict[str, Any]:"
                    )
                elif tool.name == "list_smcp_tools":
                    lines.append("async def list_smcp_tools() -> Dict[str, Any]:")
                elif tool.name in ("ask_human", "ask_human_cli"):
                    lines.append(f"async def {tool.name}(question: str) -> str:")
                else:
                    lines.append(f"async def {tool.name}(**kwargs) -> Dict[str, Any]:")

                lines.append('    """')
                # Include the tool's description and explicit note about STATE handling
                if tool.name in ("ask_human", "ask_human_cli"):
                    # make it clear to generated code authors that STATE is updated by the runtime
                    lines.append(f"    {tool.description} Updates STATE automatically.")
                else:
                    lines.append(f"    {tool.description}")
                lines.append('    """')
                lines.append("    # ... implementation omitted ...")
                lines.append("    pass")
                lines.append("")

        # Add utility functions (stub implementations for type checking)
        lines.append("async def get_url() -> str:")
        lines.append('    """Get the current URL from the browser."""')
        lines.append('    raise NotImplementedError("Runtime implementation")')
        lines.append("")
        # NOTE: goto(url) is intentionally NOT exposed to codegen.
        # Using goto(url) bypasses state machine validation since we can't statically verify
        # that the dynamic URL will satisfy the preconditions for subsequent tool calls.
        # LLMs should use the SMCP navigation tools (e.g., goto_restaurants_list) which have
        # well-defined pre/post conditions that can be validated.

        # TODO: Uncomment ai_exec for production use. Currently commented out for benchmarking
        # to avoid LLMs using it instead of proper SMCP navigation tools.
        # lines.append('async def ai_exec(subtask: str, output_schema: Optional[Dict[str, Any]] = None) -> Any:')
        # lines.append('    """')
        # lines.append('    Execute an AI agent to complete a given subtask.')
        # lines.append('    ')
        # lines.append('    Args:')
        # lines.append('        subtask: Description of the subtask for the AI agent to complete')
        # lines.append('        output_schema: Optional JSON schema for structured output.')
        # lines.append('                      If omitted, returns string result.')
        # lines.append('    ')
        # lines.append('    Returns:')
        # lines.append('        - Pydantic model (if output_schema provided)')
        # lines.append('        - String (if output_schema omitted)')
        # lines.append('    """')
        # lines.append('    raise NotImplementedError("Runtime implementation")')
        # lines.append('')
        lines.append("async def ai_eval(expr: str, **kwargs) -> str:")
        lines.append('    """')
        lines.append("    Evaluate an expression by asking AI.")
        lines.append(
            '    Template format: ai_eval("Result from {variable}", variable=value)'
        )
        lines.append(
            "    The template string uses {variable} placeholders that get replaced with kwargs."
        )
        lines.append('    """')
        lines.append('    raise NotImplementedError("Runtime implementation")')
        lines.append("")

        # NOTE: The definition code does NOT include initial STATE or URL assertions
        # Those are added separately in the prompt generation (see _generate_candidate)
        # This keeps the definition code reusable across different initial states

        # NOTE: core tool stubs, including ask_human, are generated above in the core-tool loop.
        # Do not add duplicate ask_human stubs here.

        return "\n".join(lines)


    def _pattern_description(self, value: Any) -> str:
        """Generate human-readable description of a state pattern."""
        if value is None:
            return "must be null"
        if not isinstance(value, str):
            return f"== {json.dumps(value)}"
        if value == "*":
            return "can be any non-null value"
        elif value == "":
            return "must be null/empty"
        elif "|" in value:
            options = value.split("|")
            return f"must be one of: {', '.join(options)}"
        elif value.startswith("$"):
            return f"must match parameter {value[1:]}"
        else:
            return f"== {json.dumps(value)}"


    def _generate_assertion(
        self,
        var_name: str,
        pattern: Any,
        assertion_type: str,
        state_check: bool = False,
    ) -> Optional[str]:
        """
        Generate Python assertion code for a state pattern.

        Args:
            var_name: Variable name to check
            pattern: Pattern to match (*, |, $param, "", None, or concrete value)
            assertion_type: "precondition" or "postcondition"
            state_check: If True, check STATE[var_name]; otherwise check var_name directly

        Returns:
            Python assertion code or None if no assertion needed
        """
        var_ref = f'STATE["{var_name}"]' if state_check else var_name

        # Handle None/null explicitly
        if pattern is None:
            return f"assert {var_ref} is None"

        # Handle non-string patterns (numbers, booleans, etc.)
        if not isinstance(pattern, str):
            # Use repr() for booleans since json.dumps(True) returns "true" (invalid Python)
            if isinstance(pattern, bool):
                return f"assert {var_ref} == {repr(pattern)}"
            return f"assert {var_ref} == {json.dumps(pattern)}"

        # Handle string patterns
        if pattern == "*":
            # Must be non-null
            return f"assert {var_ref} is not None"
        elif pattern == "":
            # Must be null/empty
            return f"assert not {var_ref}"
        elif "|" in pattern:
            # Must be one of the options
            options = pattern.split("|")
            options_json = json.dumps(options)
            return f"assert {var_ref} in {options_json}"
        elif pattern.startswith("$"):
            # Handled separately in caller (needs context of params)
            return None
        else:
            # Concrete value
            return f"assert {var_ref} == {json.dumps(pattern)}"

