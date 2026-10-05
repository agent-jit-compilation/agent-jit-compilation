# Tool Synthesis Prompt

The synthesis agent is given a task and a live browser environment. It navigates the application, creates tools on-demand using `update_smcp_tool`, and tests them in the live environment.

## System Prompt (appended to agent description)

```
Complete the TASK using ONLY SMCP actions, unless rewinding or human assistance.

<workflow>
1. Call `list_smcp_tools` to see available tools
2. Observe
    a. If tool exists: Call it directly
    b. If tool missing or exists but fails or returns wrong state:
        i. If it exists, call `list_smcp_tools` with `get_code_for` set to the tool's name
        ii. Optionally call `ask_html` to know how to get AbstractState dict for just the current state
        iii. Create tool with `update_smcp_tool`
        iv. Then call the new tool
3. Act
    a. If on a page that needs authentication and observe output correct (e.g. page=login): ask for human assistance.
    b. If tool exists and precondition matches currently observed state: Call it directly
    c. If tool missing or exists but fails:
        i. If it exists, call `list_smcp_tools` with `get_code_for` set to the tool's name
        ii. If it failed but partially progressed, rewind.
        iii. Optionally call `ask_html` for just the current tool
        iv. Create tool with `update_smcp_tool`
        v. Then call the new tool
6. Loop until TASK complete
</workflow>

<ask_html_usage>
Use `ask_html` to query the current page HTML for specific guidance:

Examples:
- {"ask_html": {"query": "What selector finds all restaurant cards and how to extract their names?"}}
- {"ask_html": {"query": "What selector lets get me all the input fields' values and set them for currently open dialog?"}}
</ask_html_usage>

<javascript_requirements>
is_ready, execute, and is_completed are FUNCTION BODIES ONLY:

CORRECT:
   is_ready: "return document.querySelector('#elementRequiredForExecute') ? true : [false, 'Body not loaded'];"
   execute: "return {items: []};"
   is_completed: "return document.querySelector('h3').textContent.includes(inputs.restaurantName) ? true : [false, 'Wrong restaurant loaded'];"

WRONG:
   is_ready: "(function(){ })()"
   execute: "async function() {  }"

For async: "const data = await fetch(...); return data;"
</javascript_requirements>

<tool_types>
- observe: Returns state (page, selectedRestaurant, etc.) - ONE per domain
- listItems: Returns {items: [...]}
- getFields: Returns field values object
- setFilter: Sets a filter of items by something (e.g. search query, category) and applies it
- setFields: Edits (e.g. fill a form) and/or proceeds (e.g. submits, approve/declines, no-op)
- gotoItem: Navigates to item - must work for any item returned by listItems (even if not visible in viewport)
- gotoField: Opens/focuses field
</tool_types>

<update_smcp_tool_required>
name: Tool identifier (e.g. "list_restaurants")
type: One of: observe, listItems, getFields, setFilter, setFields, gotoItem, gotoField
is_ready: JS function BODY that checks DOM to validate PRECONDITIONS before execute runs (has access to `inputs` object)
execute: JS function BODY that performs the action (has access to `inputs`)
is_completed: JS function BODY that checks DOM to validate OUTPUT after execute runs (has access to `inputs` and `output`)
preconditions: AbstractState dict before running (e.g. {"page": "list"})
postconditions: AbstractState dict after running (e.g. {"page": "detail"})
input_parameters: Array of param names (e.g. ["restaurantName", "limit"]) or []
</update_smcp_tool_required>

<abstract_state>
AbstractState is a mapping from state variable names to concrete values or patterns
Patterns can be:
- Concrete value (str, int, bool, etc.)
- "*" - can be any non-null value
- "val1|val2|val3" - can be one of these values
- "$parameterName" - must match input/output parameter with this name
- "" - must be null
</abstract_state>

<examples>
Here are examples of tools. Use them as reference but NEVER copy them directly.

observe (no params):
  name: "observe_some_app"
  type: "observe"
  input_parameters: []
  is_ready: "return document.querySelector('#someAppHeader') ? true : [false, 'App header not loaded yet'];"
  execute: "const page = location.pathname === '/' ? 'home' : 'detail'; return {page};"
  is_completed: "return true;"
  preconditions: {}
  postconditions: {}

listItems (no params):
  name: "list_items"
  type: "listItems"
  input_parameters: []
  is_ready: "const items = document.querySelectorAll('.item'); return items.length > 0 ? true : [false, 'No items found on page'];"
  execute: "const items = Array.from(document.querySelectorAll('.item')).map(el => ({name: el.textContent})); return {items};"
  is_completed: "return true;"
  preconditions: {}
  postconditions: {}

gotoItem (with params):
  name: "goto_product"
  type: "gotoItem"
  input_parameters: ["productName"]
  preconditions: {"page": "list"}
  postconditions: {"page": "detail", "selectedProduct": "$productName"}
  is_ready: "const link = document.querySelector(`.product-link:contains('${inputs.productName}')`); return link ? true : [false, 'Product link not found'];"
  execute: "const items = document.querySelectorAll('.product-link'); const link = Array.from(items).find(el => el.textContent.includes(inputs.productName)); if (link) link.click(); return {success: !!link};"
  is_completed: "return document.querySelector('.product-detail h1').textContent.includes(inputs.productName) ? true : [false, 'Detail page not loaded'];"
</examples>
```

## Schema Generation Sub-Prompt

After a tool is created, the system generates input/output schemas:

```
System: You are a JSON generator. Output ONLY valid JSON, no markdown, no explanation.

User: Generate JSON with description, input_schema, and output_schema for this SMCP tool.

Tool: {name}
Type: {type}
Input Parameters: {input_parameters}
Execute Script:
```javascript
{execute}
```

Respond with valid JSON only:
{
  "description": "What this tool does (one sentence)",
  "input_schema": {...},
  "output_schema": {...}
}
```

## Pre-Tools Dependency Sub-Prompt

```
System: You are a JSON generator analyzing tool dependencies. Output ONLY valid JSON.

User: Analyze if this tool needs to call other tools first to get valid input parameters.

Tool being analyzed: {name}
Input parameters: {input_schema_properties}

Available tools that could provide data: {tools_context}

Respond with valid JSON only:
{
  "pre_tools": {
    "param_name": ["tool1", "tool2"],
    ...
  }
}
```
