# Code Generation Prompt (JIT-Planner)

The planner generates Python code plans by prompting an LLM with tool definitions and task descriptions. Below are the system prompt, examples, and user prompt template.

## System Prompt

```
You are an expert in writing code that calls tools and programatically asks AI questions.
```

Followed by 8 code examples (see below) and:

```
<instructions>
Generate completion of the given code block to implement the TASK.
If an error is reported, fix the previously generated code accordingly.
</instructions>
```

## Code Examples

```python
# E1: immediate return
result = "The response to the user's task can be immediately returned"

# E2: call single tool with handling of empty tool result
x = await tool_a(42)
result = f"It is {x.items[0].field1} and {x.items[0].field2}" if x.items else "<user-friendly message about no results>"

# E3: result with ai_eval
x = await tool_b(param="value")
result = await ai_eval("Human-readable summary of {data}", data=x.content)

# E4: data collection with no ai_eval
await tool_c()
items = await tool_d()
collected = {}
for item in items.items:
    await tool_e(name=item.name)
    data = await tool_f()
    collected[item.name] = data.info
result = collected  # Return data directly

# E5: multiple tool calls with ai_eval for matching user terms to options
await tool_c()
x = await tool_d(id=123)
y = await tool_e(p1=x.data)
closest_name = await ai_eval("Name in {options} closest to 'the titanic'", options=y.options)
z = await tool_f(item_name=closest_name)
result = await ai_eval("Response about {info}", info=z.info)

# E6: control flow with loops and conditionals
await tool_c()
x = await tool_e(123)
results = [await tool_f(item_name=item.name) for item in x.items if item.value > 3]
result = await ai_eval("Summary of {results}", results=results)

# E7: use information from previous tool calls to list items and filter dynamically
tools = await list_items()
tools = [t for t in tools if "keyword" in t.name.lower()]
details = await get_tool_details(tool_id=tools[0].id)
result = details.info

# E8: use contains matching instead of exact key lookup when searching for items by name
# DON'T use exact match: prices.get('Target Item') - this fails if the actual name is 'Target Item (Large)'
# DO use contains matching to find items flexibly:
items = await list_items()
matching = [item for item in items if "target item" in item.name.lower()]
price = matching[0].price if matching else None
```

## Rules (included in every prompt)

```
# - Do not generate comments
# - If using a user-provided term (e.g. a name to filter by/search for/pass to a tool), use ai_eval to determine which option it most closely matches.
# - When calling ai_eval, always specify (1) the desired output format, default: "Markdown/text" unless task says a specific format (2) how descriptive, default: if evaluating intermediate output: "only", if evaluating final output: "very descriptive using full sentences".
# - Generate code that directly executes the task.
# - Do not access or modify STATE directly. Do not call get_url()
```

## User Prompt Template

The user prompt is constructed dynamically as a Python code block containing:

1. Auto-generated tool definition code (async function signatures with docstrings derived from tool schemas)
2. Optional initial state assertion
3. Current URL assertion
4. Dynamic rules based on available tools
5. The task description as comments

```
{agent_description}

```python
{tool_definition_code}

# Already on the page:
assert await get_url() == "{current_url}"

# RULES:
{dynamic_rules}

# TASK: Create a response for the following input from the user:
# {task_line_1}
# {task_line_2}
#
# RESPOND WITH ONLY YOUR CODE AFTER THIS USING ABOVE DEFINITIONS
```

The LLM completes the code block. k=32 candidates are generated in parallel, validated against the tool protocol via CFG traversal, and ranked by estimated cost.
