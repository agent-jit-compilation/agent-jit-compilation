SYSTEM_PROMPT_IDENTIFY_LOGICAL_STEPS = """
<role>
You are an expert at identifying the logical steps needed to complete a task.
</role>

<task>
You will be given a task and a set of pages that have been visited.
Your job is to identify the logical steps needed to complete the task.
</task>

<instructions>
- Do not infer pages that did not appear in the pages visited.
- The pages might not be in the order of the task. So you should think about the actual order.
- For usability, the action names should be as general as possible. e.g. Prefer "goto_Category" over "goto_MenOuterwearCategory"
- Each "do" action indicates an action that's contained in the same page. So the src_page_url and dst_page_url are the same.
- Each "goto" action indicates an action that leads to a different page. So the src_page_url and dst_page_url are different.
- Please ground your answer based on the logical steps needed to complete the task. Do not miss any steps simply because they are not in the pages visited.
</instructions>

<output_format>
Your response must be valid JSON matching this schema:
{
  "steps": [
    {
      "name": "string (e.g., goto_Category, do_AddToCart)",
      "description": "string",
      "src_page": "string",
      "src_page_url": "string or null",
      "dst_page": "string",
      "dst_page_url": "string or null",
      "required_parameters": [
        {
          "name": "parameter_name",
          "status": "known" | "unknown",
          "value": "optional_specific_value"
        }
      ]
    }
  ]
}

For required_parameters:
- Each parameter has "name", "status", and optional "value"
- Use status "known" if the parameter is directly specified in the task or already in the URL
- Use status "unknown" if the parameter needs to be discovered through search/exploration
- Include "value" field only when the specific value is explicit in the task (e.g., "Walmart", "black", "size M")
- Common parameter names: store_name, product_name, product_id, category, filter_criteria, color, size, etc.
- Empty list [] if no parameters are required
</output_format>

<example>
Input:
Task: Find and purchase a medium-sized black jacket under $150 from WinterWear.com

Pages visited:
[0] Homepage - https://www.winterwear.com/
[1] Men's Outerwear Category - https://www.winterwear.com/mens/outerwear
[2] Men's Outerwear Category (filtered by color: black) - https://www.winterwear.com/mens/outerwear?color=black
[3] Men's Outerwear Category (filtered by color: black, price: under $150) - https://www.winterwear.com/mens/outerwear?color=black&price=0-150
[4] Product Detail Page - Alpine Trek Jacket - https://www.winterwear.com/product/alpine-trek-jacket-black
[5] Product Detail Page (size M selected) - Alpine Trek Jacket - https://www.winterwear.com/product/alpine-trek-jacket-black?size=M
[6] Shopping Cart - https://www.winterwear.com/cart
[7] Checkout - Shipping Information - https://www.winterwear.com/checkout/shipping
[8] Checkout - Payment Information - https://www.winterwear.com/checkout/payment
[9] Order Confirmation - https://www.winterwear.com/order/confirmed/ORD-2024-9847

Output:
{
  "steps": [
    {
      "name": "goto_Category",
      "description": "Go to the men's outerwear category",
      "src_page": "HomePage",
      "src_page_url": "https://www.winterwear.com/",
      "dst_page": "CategoryPage",
      "dst_page_url": "https://www.winterwear.com/mens/outerwear",
      "required_parameters": [
        {"name": "category", "status": "known", "value": "mens-outerwear"}
      ]
    },
    {
      "name": "do_ApplyFilters",
      "description": "Apply the filters to the men's outerwear category",
      "src_page": "CategoryPage",
      "src_page_url": "https://www.winterwear.com/mens/outerwear",
      "dst_page": "CategoryPage",
      "dst_page_url": "https://www.winterwear.com/mens/outerwear?color=black&price=0-150",
      "required_parameters": [
        {"name": "color", "status": "known", "value": "black"},
        {"name": "price_range", "status": "known", "value": "under-150"}
      ]
    },
    {
      "name": "goto_ProductPage",
      "description": "Go to the product page",
      "src_page": "CategoryPage",
      "src_page_url": "https://www.winterwear.com/mens/outerwear?color=black&price=0-150",
      "dst_page": "ProductPage",
      "dst_page_url": "https://www.winterwear.com/product/alpine-trek-jacket-black",
      "required_parameters": [
        {"name": "product_name", "status": "unknown"},
        {"name": "product_id", "status": "unknown"}
      ]
    },
    {
      "name": "do_AddToCart",
      "description": "Add the product to the cart",
      "src_page": "ProductPage",
      "src_page_url": "https://www.winterwear.com/product/alpine-trek-jacket-black",
      "dst_page": "ShoppingCartPage",
      "dst_page_url": "https://www.winterwear.com/cart",
      "required_parameters": [
        {"name": "size", "status": "known", "value": "M"}
      ]
    },
    {
      "name": "do_Checkout",
      "description": "Checkout the product",
      "src_page": "ShoppingCartPage",
      "src_page_url": "https://www.winterwear.com/cart",
      "dst_page": "OrderConfirmationPage",
      "dst_page_url": "https://www.winterwear.com/order/confirmed/ORD-2024-9847",
      "required_parameters": []
    }
  ]
}
</example>
"""

USER_PROMPT_IDENTIFY_LOGICAL_STEPS = """
Task: {task}
Pages visited: {pages_visited}
"""

SYSTEM_PROMPT_IDENTIFY_LOGICAL_STEPS_WITH_TRAJECTORIES = """
<role>
You are an expert at identifying the logical steps needed to complete a task.
</role>

<task>
You will be given a task and a set of pages that have been visited.
Your job is to identify the logical steps needed to complete the task.
</task>

<instructions>
- Do not infer pages that did not appear in the pages visited.
- The pages might not be in the order of the task. So you should think about the actual order.
- For usability, the action names should be as general as possible. e.g. Prefer "goto_Category" over "goto_MenOuterwearCategory"
- Each "do" action indicates an action that's contained in the same page. So the src_page_url and dst_page_url are the same.
- Each "goto" action indicates an action that leads to a different page. So the src_page_url and dst_page_url are different.
- Please ground your answer based on the actual trajectory given to you. If there are any missing steps, due to the trajectory not being complete, you should add it.
</instructions>

<output_format>
Your response must be valid JSON matching this schema:
{
  "steps": [
    {
      "name": "string (e.g., goto_Category, do_AddToCart)",
      "description": "string",
      "src_page": "string",
      "src_page_url": "string or null",
      "dst_page": "string",
      "dst_page_url": "string or null",
      "required_parameters": [
        {
          "name": "parameter_name",
          "status": "known" | "unknown",
          "value": "optional_specific_value"
        }
      ]
    }
  ]
}

For required_parameters:
- Each parameter has "name", "status", and optional "value"
- Use status "known" if the parameter is directly specified in the task or already in the URL
- Use status "unknown" if the parameter needs to be discovered through search/exploration
- Include "value" field only when the specific value is explicit in the task (e.g., "Walmart", "black", "size M")
- Common parameter names: store_name, product_name, product_id, category, filter_criteria, color, size, etc.
- Empty list [] if no parameters are required
</output_format>
"""

USER_PROMPT_IDENTIFY_LOGICAL_STEPS_WITH_TRAJECTORIES = """
Task: {task}
Pages visited: {pages_visited}
Trajectories: {trajectories}
"""

SYSTEM_PROMPT_CATEGORIZE_TRAJECTORY_TO_LOGICAL_STEPS = """
<role>
You are a web agent that categorizes trajectory steps to logical steps.
</role>

<task>
You will be given a task, a set of pages that have been visited, and a set of trajectories.
Your job is to categorize the trajectory steps to the logical steps.
</task>

<instructions>
- Evaluation previous goal provides a good answer of which logical step the *previous* trajectory step belongs to.
</instructions>

<output_format>
Your response must be valid JSON matching this schema:
{
  "trajectory_to_logical_steps": [
    {
      "trajectory_id": 0,
      "logical_step_name": "string (e.g., goto_Category)"
    }
  ]
}
</output_format>
"""

USER_PROMPT_CATEGORIZE_TRAJECTORY_TO_LOGICAL_STEPS = """
Task: {task}
Logical steps: {logical_steps}
Trajectory steps: {trajectory_steps}
"""

SYSTEM_PROMPT_EVALUATE_STEP = """
<role>
You are a web agent that evaluates the success of a trajectory step.
</role>

<task>
Given a logical step description and the current and next steps, determine whether the trajectory step was successful.
</task>

<output_format>
Your response must be valid JSON matching this schema:
{
  "status": "success" | "failure" | "partial" | "unknown"
}
</output_format>
"""

USER_PROMPT_EVALUATE_STEP = """
Logical step description: {logical_step_description}

Current step: {current_step}

Next step: {next_step}
"""


# ============================================================================
# Page Schema Extraction
# ============================================================================

SYSTEM_PROMPT_EXTRACT_PAGE_SCHEMA = """You are an expert at analyzing web page structure and extracting UI schemas.

Your task is to analyze HTML snapshots from web pages and identify the structural elements, entities, and patterns present on the page.

For each page, you should identify:
1. **Element/Entity Types**: Repeated patterns (e.g., Restaurant cards, Product listings) or unique elements (e.g., Checkout button)
2. **Element Type Classification**:
   - "list": Multiple similar items that enumerate (e.g., list of restaurants, products)
   - "single": Unique elements that appear once (e.g., navigation bar, checkout button)
   - "form": Input fields grouped together (e.g., login form, checkout form)
   - "navigation": Links/buttons for page transitions
3. **Fields**: For each element type, identify which data fields are always present (required) vs sometimes present (optional)
4. **Actions**: What user actions are possible on this page (e.g., click restaurant, search, add to cart)

Be precise and focus on **semantic structure** rather than HTML implementation details.
Group similar elements together even if they have slightly different styling.
"""

USER_PROMPT_EXTRACT_PAGE_SCHEMA = """Analyze the following HTML snapshots and extract the UI schema.

Page URL Pattern: {url_pattern}
Sample URLs: {sample_urls}

HTML Samples (from different visits to similar pages):
{html_samples}

Extract the schema including:
1. **Page type**: Infer the semantic page type from the content (e.g., "Homepage", "ProductPage", "CheckoutPage", "SearchResultsPage", etc.)
2. **Elements**: All element/entity types present on this page
3. **Element classification**: For each element, classify as "list", "single", "form", or "navigation"
4. **Fields**: Required fields (always present) and optional fields (sometimes present) for each element
5. **Actions**: Available actions users can perform on this page

Focus on the USER-VISIBLE structure, not HTML implementation details.
Be generic and don't make assumptions about the site domain.
"""

SYSTEM_PROMPT_PRODUCE_JSON_SCHEMA = """
You produce a single JSON Schema (Draft 2020-12) that summarizes what is visible in the provided HTML.

<instructions>
Do not focus on CSS selectors. Focus on the semantic meaning of the HTML.
You will output all object types and their attributes. Attributes include an object's fields but also how it references other objects. Each object type should have a unique entry under $defs.

Here's an example of "x-collectionMeta" you should add to each object type you define using $defs:
{ 
   "totalVisibleCount": <integer>, # number of occurences of this object type in the HTML
   "enums": [ { "value": "<group name: string>", "count": <int>, "children": [ { "ref": "<child reference: string>", "count": "<int>" } ] }, ... ], # list of enums for this object type
   "fields": [ { "name": "<field name: string>", "type": "<field type: string>", "required": <bool> }, ... ], # list of fields for this object type
   "notes": "Relevant notes about the object type"
}
</instructions>

<output_format>
- Return only the JSON Schema object.
- Specify "$id" to be the page name, and "$schema" to be "https://json-schema.org/draft/2020-12/schema"
</output_format>
"""

USER_PROMPT_PRODUCE_JSON_SCHEMA = """
<page_url>
{url}
</page_url>

<html>
{html}
</html>
"""

SYSTEM_PROMPT_PRODUCE_EDGES = """
<role>
You are an expert at identifying the minimal set of actions needed to complete a task.
</role>

<instructions>
- **Avoid semantic duplicates**: If multiple trajectory steps perform the same logical action (even with slightly different implementations or verbs), they should map to a SINGLE action with a general name.
  - Bad: "do_IdentifyPrice" and "do_ViewPrice" (these are duplicates)
  - Good: "do_ViewPrice" (one canonical action)
  - Bad: "goto_RestaurantMenu" and "goto_FoodMenu" 
  - Good: "goto_Menu"
- **Use canonical verbs**: Prefer standard action verbs: "goto" for navigation, "do" for same-page actions, "select", "view", "add", "remove", "apply", "search"
- **Deduplication principle**: Before creating a new action, check if an existing action already covers that behavior
</instructions>

<explanation>
A website is a collection of PagePatterns.
A PagePattern is a group of pages that share the same template logically.
e.g. /store/*, /product/*, /checkout

Action:
- name: name of the action. This name should be as general and reusable as possible. 
  - e.g. Prefer "goto_Category" over "goto_MenOuterwearCategory".
  - Important: Do NOT include what belongs in the parameters in the name.
- description: description of the action
- parameters: these are the values we need to fill in to complete the action. Each parameter has "name", "input_values", and "observed_values".
  - "input_values": values that are provided as task input
  - "observed_values": values that are observed in the trajectory
- version_number: defaults to 1. Only increment if the action is repeated and the previous action was successful. e.g. an agent adds two items to cart, the second action should have version_number=2. If the first action keeps failing, then do not increment.
- src_page_pattern: the page pattern that the action is from
- dst_page_pattern: the page pattern that the action leads to (can be the same as src_page_pattern, or a different page pattern)
</explanation>

<example>
Task:
Find and purchase a medium-sized black jacket under $150 from WinterWear.com

Page information:
- Homepage: https://www.winterwear.com/
- Men's Outerwear Category: https://www.winterwear.com/mens/outerwear
- Men's Outerwear Category (filtered by color: black): https://www.winterwear.com/mens/outerwear?color=black
- Men's Outerwear Category (filtered by color: black, price: under $150): https://www.winterwear.com/mens/outerwear?color=black&price=0-150
- Product Detail Page: https://www.winterwear.com/product/alpine-trek-jacket-black
- Product Detail Page (size M selected): https://www.winterwear.com/product/alpine-trek-jacket-black?size=M
- Shopping Cart: https://www.winterwear.com/cart
- Checkout: https://www.winterwear.com/checkout/shipping
- Checkout: https://www.winterwear.com/checkout/payment
- Order Confirmation: https://www.winterwear.com/order/confirmed/ORD-2024-9847

Actions:
{
  "actions": [
    {
      "name": "goto_Category",
      "description": "Go to a category page",
      "src_page_pattern": "/",
      "dst_page_pattern": "/{department}/{category}",
      "parameters": [
        {"name": "department", "input_values": null, "observed_values": ["mens"]},
        {"name": "category", "input_values": null, "observed_values": ["outerwear"]}
      ],
      "version_number": 1
    },
    {
      "name": "do_ApplyFilters",
      "description": "Apply the filters to a category page",
      "src_page_pattern": "/{department}/{category}",
      "dst_page_pattern": "/{department}/{category}",
      "parameters": [
        {"name": "color", "input_values": ["black"], "observed_values": null},
        {"name": "price_range", "input_values": ["under 150"], "observed_values": ["under-150"]}
      ],
      "version_number": 1
    },
    {
      "name": "goto_ProductPage",
      "description": "Go to the product page",
      "src_page_pattern": "/{department}/{category}",
      "dst_page_pattern": "/product/{product_id}",
      "parameters": [
        {"name": "product_id", "input_values": null, "observed_values": ["alpine-trek-jacket-black"]}
      ],
      "version_number": 1
    },
    {
      "name": "goto_AddToCart",
      "description": "Add the product and go to the cart",
      "src_page_pattern": "/product/{product_id}",
      "dst_page_pattern": "/cart",
      "parameters": [
        {"name": "size", "input_values": ["M"], "observed_values": null}
      ],
      "version_number": 1
    },
    {
      "name": "goto_CompleteCheckout",
      "description": "Complete the checkout process",
      "src_page_pattern": "/cart",
      "dst_page_pattern": "/order/confirmed/{order_id}",
      "parameters": [],
      "version_number": 1
    }
  ]
}
</example>
"""

USER_PROMPT_PRODUCE_EDGES = """
<task>
{task}
</task>

<page_information>
{page_information}
</page_information>

<trajectories>
{trajectories}
</trajectories>
"""

SYSTEM_PROMPT_ASSIGN_STEPS = """
<role>
You are an expert at mapping trace steps to high-level actions.
</role>

<task>
Given a task, a list of actions (edges in a site graph), and trajectory steps taken by an agent, 
you must assign EVERY single trace step to exactly one action.
</task>

<instructions>
CRITICAL REQUIREMENTS:
- EVERY trace step MUST be mapped to an action
- You MUST include ALL trace steps in your output - do not skip any
- Each trace step has a unique step ID - map every single step ID to an action
- If a step doesn't perfectly match an action, choose the closest/most relevant action
- Count the total trace steps and verify your output has the same count

Guidelines:
- Look at what the agent actually did in each step (next_action_name), the agent's memory, evaluation, and goal
- Match steps to actions based on the page transitions and action descriptions
- Only specify action name, not the parameters
- Multiple consecutive trace steps can map to the same action if they're part of the same logical step
- If uncertain, choose the action that best represents the intent of that trace step
- Only use the action names provided in the actions section, do not create new action names. Do not include parameters in the action name.
</instructions>

<output_format>
Return a JSON object with:
{
  "traj_to_actions": [
    {"step_id": 0, "action_name": "action_name_1"},
    {"step_id": 1, "action_name": "action_name_2"},
    ...
    // MUST include ALL trace steps
  ]
}
</output_format>
"""

USER_PROMPT_ASSIGN_STEPS = """
<task>
{task}
</task>

<actions>
{actions}
</actions>

<trace_steps>
{trace_steps}
</trace_steps>

REMINDER: You MUST map EVERY trace step listed above. Count them carefully and ensure your output includes all of them. You will likely need to use all the actions provided."""
