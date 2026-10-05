from typing import List, Optional

import openai
from pydantic import BaseModel


class ElementCount(BaseModel):
    """A single element and its usage count."""

    element_name: str
    required: bool  # Whether this element is required for the task
    count: int  # Number of interactions (0 if not required)
    destination_page: str  # The page the element navigates to


class ElementUsageCounts(BaseModel):
    """Element usage counts for sequential execution."""

    elements: List[ElementCount]


class SchedulerPlan(BaseModel):
    """Execution plan with element usage predictions."""

    explanation: str
    elements: List[ElementCount]  # All elements with usage counts and required flag


class ParallelizabilityCheck(BaseModel):
    """Result of parallelizability check."""

    is_parallelizable: bool


class ParallelElementCount(BaseModel):
    """Simple element count for parallel operations (no required field needed)."""

    element_name: str
    count: int


class ParallelUsageCounts(BaseModel):
    """Element usage counts for parallel execution strategy."""

    parallel_elements: List[ParallelElementCount]  # Elements used per worker
    num_workers: int  # Number of parallel workers needed
    requires_rediscovery: bool  # Whether subtasks must rediscover targets
    rediscovery_elements: List[ParallelElementCount]  # Navigation elements per worker


SYSTEM_PROMPT_COUNT_ELEMENTS = """You are a helpful assistant that predicts element usage for web automation tasks.
Given a task, page schemas, and available elements, predict which elements will be used.

**Visibility Hierarchy:**
Elements are organized into pages and modals. Information becomes visible as you navigate:

1. **Page elements**: When you navigate to a page (via an element with `opensPage`), all non-modal elements on that page become visible. Their properties (name, price, rating, etc.) can be READ without clicking.

2. **Modal elements**: Elements inside a modal (shown with dot notation like `someModal.button`) are only visible AFTER you click an element with `opensModal` pointing to that modal.

**Key principle**: To READ an element's properties, you just need to be on the right page. To ACCESS content behind `opensPage` or `opensModal`, you must INTERACT with that element (click).

Example visibility chain:
  [Page: main] → itemCard visible (can READ itemCard.name, itemCard.price)
       ↓ click itemCard (has opensPage: "detail")
  [Page: detail] → detailButton visible, reviewCard visible (can READ their properties)
       ↓ click detailButton (has opensModal: "editModal")  
  [Modal: editModal] → editModal.saveButton now visible

**Counting rules:**
- Only count elements that MUST be interacted with (clicked, typed into)
- If you only need to READ properties, you don't need to count that element
- Use x-visibleCount/x-visibleCounts to determine accurate counts
- If a specific group/category is NOT listed in x-visibleCounts, estimate using a similar category's count or use the average of listed counts as a reasonable default
- **NEVER output count=0 for required elements that need interaction.** If exact counts are unknown, make a reasonable estimate based on:
  - Total visible count (x-visibleCount) and likely proportion matching the criteria
  - Example: 200 total items, filtering by a specific attribute → estimate ~5% match, so ~10 items
  - When in doubt, estimate conservatively (around 5% of total for filtered subsets)
- Carefully consider the visibility hierarchy and make use of the information provided (e.g., attributes of the element)

Examples:
- Task: "What is the price of the first product?"
  productCard has properties: {name, price}, opensPage: "product"
  → productCard: 0 (just READ price from the visible card, no click needed)

- Task: "Get details from the first product's detail page"
  productCard has opensPage: "product"
  → productCard: 1 (must click to navigate to detail page)

- Task: "Get the first 3 reviews"
  reviewCard has x-visibleCount: 10
  → reviewCard: 0 (just READ 3 reviews, no clicks needed)

- Task: "Add 2 items to cart" (requires opening customization modal)
  itemCard has opensModal: "customizationModal"
  → itemCard: 2, customizationModal.addButton: 2

- Task: "Invite 5 users to the project"
  inviteButton opens inviteModal (1 interaction to open)
  inviteModal.emailInput accepts multiple comma-separated emails in one field
  inviteModal.submitButton submits all invites at once
  → inviteButton: 1, inviteModal.submitButton: 1 (NOT 5 each!)

- Task: "Get info from all 6 stores in the 'Featured' section"
  storeCard has x-visibleCounts: { "Featured": 6, "Popular": 4 }
  → storeCard: 6 (use count for 'Featured' section), operation is READ so count=0 if just reading properties
  → BUT if task requires clicking each store: storeCard: 6 (must interact)

In your explanation, explain:
1. Which elements you chose and why
2. Whether each element needs READ (count=0) or INTERACT (count>0)
3. How you determined the count (from x-visibleCount, x-visibleCounts, or task requirements)

Some pages might have semantically related names, but beware that they are different pages, and navigation is still required. Keep track of the page you are on after each interaction.

Produce the simplest plan with minimum interactions needed.
ONLY use elements from the provided list.
"""

USER_PROMPT_COUNT_ELEMENTS = """Task: {task}

{page_schemas}Available Elements (grouped by page/modal):
{valid_elements}

Property Reference:
- Properties without opensPage/opensModal: Can be READ once the element is visible (no interaction needed)
- opensPage: Clicking navigates to another page (INTERACTION unlocks that page's elements)
- opensModal: Clicking opens the specified modal (INTERACTION unlocks modal elements)
- x-visibleCount: Total visible instances of this element
- x-visibleCounts: Count per group (e.g., {{ "SectionA": 5, "SectionB": 3 }})

Output a SchedulerPlan with:
- elements: For each element that requires INTERACTION:
  - element_name: name from the list
  - required: true if MUST interact to complete task, false otherwise
  - count: number of interactions needed (0 if only reading, >0 if clicking/typing)

Remember: Reading visible properties = 0 interactions. Navigating or opening modals = 1+ interactions."""


SYSTEM_PROMPT_CHECK_PARALLELIZABLE = """Determine if a task can be parallelized.
Parallelizable = the same action repeated on multiple items/entities (e.g., writing reviews for multiple orders, visiting multiple restaurants, sending emails to multiple people, processing multiple items).
NOT parallelizable = single entity operation, purely sequential dependencies where one action's output is another's input, or single page data extraction."""

USER_PROMPT_CHECK_PARALLELIZABLE = """Task: {task}

Is this parallelizable? (True if involves multiple independent operations on separate entities)"""


SYSTEM_PROMPT_COUNT_PARALLEL = """Identify parallelizable operations from a sequential plan.
All elements in the list are REQUIRED for the task.

**IMPORTANT: Maximum 4 workers can run simultaneously.** If more than 4 parallel operations are needed,
workers are batched (e.g., 6 workers = batch of 4, then batch of 2).

- parallel_elements: elements that can run independently (per worker, not total)
- num_workers: total number of parallel workers needed (can exceed 4)
- requires_rediscovery: The subtasks must rediscover URL/link for their targets from initial page. True if element has no URL/link property in schema
- rediscovery_elements: navigation elements each worker needs if requires_rediscovery=True

e.g., worker needs to navigate to a item details page, so it needs the "rediscover" item url if not visible in the initial page.
e.g., worker needs to find the item it needs to work with, and the item location needs search or read to find, so it needs the "rediscover" item.
"""

USER_PROMPT_COUNT_PARALLEL = """Task: {task}

Required Elements (all must be executed): {sequential_usage}

Identify parallel operations. If element used N times independently, set num_workers=N and count=1 per worker.
Check schema for URL/link property to determine requires_rediscovery."""


class Scheduler:
    def __init__(
        self,
        client: openai.OpenAI,
        site_name: str,
        element_latencies_file: Optional[str] = None,
        model: Optional[str] = None,
        top_n_elements: Optional[int] = None,
        schema_root: Optional[str] = None,
    ):
        """Initialize the Scheduler.

        Args:
            client: OpenAI client
            site_name: Name of the site (for loading schemas)
            element_latencies_file: Path to element latencies JSON file. If not provided,
                will be derived from site_name and model.
            model: Model name (e.g., 'gpt-4.1') for loading model-specific latencies.
                Ignored if element_latencies_file is provided.
            top_n_elements: If provided, only load the top N elements with highest mean latency.
                If None, loads all elements.
        """
        self.client = client
        self.site_name = site_name
        self.schema_root = schema_root or "saved_schemas"

        # Resolve element latencies file path
        if element_latencies_file is None:
            from blast_scheduler.offline.element_latencies import (
                get_element_latencies_path,
            )

            element_latencies_file = get_element_latencies_path(site_name, model)

        # Load page schemas for context
        from blast_scheduler.offline.site_graph_builder import ContextBuilder

        self.context_builder = ContextBuilder(site_name, client, schema_root=self.schema_root)
        self.page_schemas = self.context_builder.load_all_page_schemas()

        # Load elements that have latency data (only these can be used for predictions)
        self.valid_elements = self._load_elements_with_latencies(
            element_latencies_file, top_n_elements
        )

        # Load site config (page order, etc.)
        self.site_config = self._load_site_config()

    def _load_elements_with_latencies(
        self, element_latencies_file: str, top_n_elements: Optional[int] = None
    ) -> List[str]:
        """Load all element names that have latency data.

        Only elements with latency data can be used for predictions, since we
        need their latency distributions for cost estimation.

        Args:
            element_latencies_file: Path to element latencies JSON
            top_n_elements: If provided, return only the top N elements with highest mean latency.
                If None, return all elements.

        Returns:
            List of element names that have latency data, sorted by descending mean latency
        """
        from pathlib import Path

        from blast_scheduler.offline.element_latencies import LatencyDistributionBuilder

        if not Path(element_latencies_file).exists():
            raise FileNotFoundError(
                f"Element latencies file not found: {element_latencies_file}"
            )

        element_latencies = LatencyDistributionBuilder.load_element_latencies(
            element_latencies_file
        )

        # Return all element names that have latency data
        return sorted(
            list(element_latencies.keys()),
            key=lambda x: element_latencies[x].mean,
            reverse=True,
        )[:top_n_elements]

    def _load_site_config(self) -> dict:
        """Load site configuration from saved_schemas/{site_name}/config.json.

        Returns:
            Dict with site configuration. Empty dict if config file doesn't exist.
        """
        import json
        from pathlib import Path

        config_path = Path(self.schema_root) / self.site_name / "config.json"
        if config_path.exists():
            with open(config_path) as f:
                return json.load(f)
        return {}

    def _get_element_descriptions(self) -> dict[str, str]:
        """Extract descriptions for valid elements from page schemas.

        Returns:
            Dict mapping element names to their descriptions (only for valid_elements)
        """
        # Build set of valid element names and their base names (for nested like "modal.button")
        valid_names = set(self.valid_elements)
        valid_base_names = {elem.split(".")[0] for elem in self.valid_elements}

        descriptions = {}
        for url, schema in self.page_schemas.items():
            defs = schema.get("$defs", {})
            for def_name, def_schema in defs.items():
                # Only include if element is in valid_elements
                if def_name not in valid_names and def_name not in valid_base_names:
                    continue
                if isinstance(def_schema, dict):
                    desc = def_schema.get("description")
                    if desc and def_name not in descriptions:
                        descriptions[def_name] = desc
        return descriptions

    def _get_element_properties(self) -> dict[str, dict]:
        """Extract properties for valid elements from page schemas.

        Returns:
            Dict mapping element names to their properties dict (only for valid_elements)
        """
        # Build set of valid element names and their base names (for nested like "modal.button")
        valid_names = set(self.valid_elements)
        valid_base_names = {elem.split(".")[0] for elem in self.valid_elements}

        properties = {}
        for url, schema in self.page_schemas.items():
            defs = schema.get("$defs", {})
            for def_name, def_schema in defs.items():
                # Only include if element is in valid_elements
                if def_name not in valid_names and def_name not in valid_base_names:
                    continue
                if isinstance(def_schema, dict) and def_name not in properties:
                    props = def_schema.get("properties", {})
                    x_visible_count = def_schema.get("x-visibleCount")
                    x_visible_counts = def_schema.get("x-visibleCounts")

                    # Build combined dict with properties and cardinality info
                    all_props = dict(props) if props else {}
                    if x_visible_count is not None:
                        all_props["x-visibleCount"] = {"const": x_visible_count}
                    if x_visible_counts is not None:
                        all_props["x-visibleCounts"] = {"const": x_visible_counts}

                    if all_props:
                        properties[def_name] = all_props
        return properties

    def _get_element_pages(self) -> dict[str, list[str]]:
        """Get which pages each valid element appears on.

        Returns:
            Dict mapping element names to list of page names they appear on
        """
        valid_names = set(self.valid_elements)
        valid_base_names = {elem.split(".")[0] for elem in self.valid_elements}

        element_pages: dict[str, list[str]] = {}
        for url, schema in self.page_schemas.items():
            # Use the JSON filename (without extension) as page name
            # e.g., "main.json" -> "main", "product_details.json" -> "product_details"
            page_name = url.rsplit("/", 1)[-1].replace(".json", "") or "index"

            defs = schema.get("$defs", {})
            for def_name in defs.keys():
                if def_name in valid_names or def_name in valid_base_names:
                    if def_name not in element_pages:
                        element_pages[def_name] = []
                    if page_name not in element_pages[def_name]:
                        element_pages[def_name].append(page_name)

        return element_pages

    def _format_property(self, prop_name: str, prop_schema: dict) -> str:
        """Format a property with type info.

        Args:
            prop_name: Name of the property
            prop_schema: Schema dict for the property

        Returns:
            Formatted string like "name (string)" or "size (enum: [S, M, L])"
        """
        if "$ref" in prop_schema:
            ref_name = prop_schema["$ref"].split("/")[-1]
            return f"{prop_name} (ref: {ref_name})"
        elif "const" in prop_schema:
            const_val = prop_schema["const"]
            # Highlight navigation properties with arrows for clarity
            if prop_name == "opensPage":
                return f"opensPage -> {const_val} [CLICK TO ACCESS PAGE]"
            elif prop_name == "opensModal":
                return f"opensModal -> {const_val} [CLICK TO OPEN MODAL]"
            return f"{prop_name} (const: {const_val})"
        elif "enum" in prop_schema:
            enum_vals = ", ".join(str(v) for v in prop_schema["enum"][:3])
            suffix = "..." if len(prop_schema["enum"]) > 3 else ""
            return f"{prop_name} (enum: [{enum_vals}{suffix}])"
        else:
            prop_type = prop_schema.get("type", "any")
            return f"{prop_name} ({prop_type})"

    def _format_element_line(
        self,
        elem: str,
        descriptions: dict[str, str],
        properties: dict[str, dict],
        include_properties: bool = False,
    ) -> str:
        """Format an element line with optional description and properties.

        Args:
            elem: Element name
            descriptions: Dict of element descriptions
            properties: Dict of element properties
            include_properties: If True, include properties on a second line

        Returns:
            Formatted element string
        """
        # Try to match element name (may be nested like "menuItemCard.itemAddButton")
        base_name = elem.split(".")[0]
        desc = descriptions.get(elem) or descriptions.get(base_name)

        if desc:
            line = f"- {elem}: {desc}"
        else:
            line = f"- {elem}"

        if include_properties:
            props = properties.get(elem) or properties.get(base_name)
            if props:
                # Separate readable properties from navigation actions
                readable_props = []
                nav_actions = []
                for k, v in props.items():
                    formatted = self._format_property(k, v)
                    if k in ("opensPage", "opensModal"):
                        nav_actions.append(formatted)
                    else:
                        readable_props.append(formatted)

                if readable_props:
                    line += f"\n    Readable: {', '.join(readable_props)}"
                if nav_actions:
                    line += f"\n    Navigation: {', '.join(nav_actions)}"

        return line

    def check_parallelizable(self, task: str) -> bool:
        """Check if a task can benefit from parallel execution.

        Args:
            task: The task description

        Returns:
            True if the task is parallelizable, False otherwise
        """
        resp = self.client.responses.parse(
            model="gpt-4.1",
            temperature=0,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT_CHECK_PARALLELIZABLE},
                {
                    "role": "user",
                    "content": USER_PROMPT_CHECK_PARALLELIZABLE.format(task=task),
                },
            ],
            text_format=ParallelizabilityCheck,
        )

        return resp.output_parsed.is_parallelizable

    def get_sequential_usage(
        self,
        task: str,
        include_descriptions: bool = True,
        include_properties: bool = True,
        include_schemas: bool = False,
    ) -> SchedulerPlan:
        """Get execution plan with element usage counts for sequential execution.

        Args:
            task: The task description
            include_descriptions: If True, include element descriptions in the prompt
            include_properties: If True, include element properties with type/enum info
            include_schemas: If True, include full page schemas in the prompt

        Returns:
            SchedulerPlan with element usage predictions
        """
        # Format elements, optionally with descriptions and properties, grouped by page
        descriptions = self._get_element_descriptions() if include_descriptions else {}
        properties = self._get_element_properties() if include_properties else {}
        element_pages = self._get_element_pages()

        # Group elements by each page they appear on (elements on multiple pages
        # are listed under each page separately)
        from collections import defaultdict

        page_groups: dict[str, list[str]] = defaultdict(list)
        for elem in self.valid_elements:
            base_name = elem.split(".")[0]
            pages = element_pages.get(elem) or element_pages.get(base_name, [])
            if pages:
                for page in pages:
                    page_groups[page].append(elem)
            else:
                page_groups["Unknown"].append(elem)

        # Sort pages using config order
        page_order_list = self.site_config.get("page_order", [])
        page_order_map = {name: i for i, name in enumerate(page_order_list)}

        def page_sort_key(page_name: str) -> tuple[int, str]:
            order = page_order_map.get(page_name, 99)
            return (order, page_name)

        elements_sections = []

        # First, output all element definitions
        if include_descriptions or include_properties:
            elements_sections.append("Elements:")
            for elem in sorted(self.valid_elements):
                line = self._format_element_line(
                    elem, descriptions, properties, include_properties
                )
                elements_sections.append(line)

        # Then output each page with just element names
        elements_sections.append("\nPages:")
        for page_name in sorted(page_groups.keys(), key=page_sort_key):
            elements_sections.append(f"[{page_name}]")
            for elem in page_groups[page_name]:
                elements_sections.append(f"- {elem}")

        elements_str = "\n".join(elements_sections)
        print(elements_str)

        # Format page schemas if requested
        if include_schemas:
            schemas_str = (
                "Page Schemas:\n"
                + "\n".join(
                    f"{url}:\n{schema}" for url, schema in self.page_schemas.items()
                )
                + "\n\n"
            )
        else:
            schemas_str = ""

        max_retries = 5
        resp = None
        for attempt in range(max_retries):
            # Add retry context if this is a retry attempt
            task_with_context = task
            if attempt > 0:
                invalid_elements = [
                    elem.element_name
                    for elem in resp.output_parsed.elements
                    if elem.element_name not in self.valid_elements
                ]
                task_with_context = (
                    f"{task}\n\n"
                    f"RETRY: You previously used invalid element(s): {', '.join(invalid_elements)}. "
                    f"Only use elements from the provided list."
                )

            resp = self.client.responses.parse(
                model="gpt-4.1",
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT_COUNT_ELEMENTS},
                    {
                        "role": "user",
                        "content": USER_PROMPT_COUNT_ELEMENTS.format(
                            task=task_with_context,
                            page_schemas=schemas_str,
                            valid_elements=elements_str,
                        ),
                    },
                ],
                text_format=SchedulerPlan,
            )

            # Validate all elements are in the allowed list
            invalid_elements = [
                elem.element_name
                for elem in resp.output_parsed.elements
                if elem.element_name not in self.valid_elements
            ]

            if not invalid_elements:
                break  # All elements are valid

            if attempt == max_retries - 1:
                raise ValueError("Sequential usage prediction still contains unknown elements after five attempts")

        return resp.output_parsed

    def get_parallel_usage(
        self, task: str, sequential_plan: SchedulerPlan
    ) -> Optional[ParallelUsageCounts]:
        """Get element usage counts for parallel execution.

        First checks if the task is parallelizable, then reorganizes the sequential
        plan into parallel stages if applicable.

        Args:
            task: The task description
            sequential_plan: The sequential execution plan to reorganize

        Returns:
            ParallelUsageCounts if parallelizable, None otherwise
        """
        # First check if the task is parallelizable
        is_parallelizable = self.check_parallelizable(task)
        print(f"is_parallelizable: {is_parallelizable}")

        if not is_parallelizable:
            return None

        # Task is parallelizable, identify which elements can be parallelized
        # Only pass required elements to parallel planning
        required_elements = [e for e in sequential_plan.elements if e.required]

        # If no required elements (read-only from front page), no parallel planning needed
        if not required_elements:
            return None

        seq_formatted = "\n".join(
            f"- {elem.element_name}: {elem.count}" for elem in required_elements
        )

        max_retries = 5
        resp = None
        for attempt in range(max_retries):
            # Add retry context if this is a retry attempt
            task_with_context = task
            if attempt > 0:
                # Collect invalid elements from both parallel and rediscovery
                invalid_parallel = [
                    elem.element_name
                    for elem in resp.output_parsed.parallel_elements
                    if elem.element_name not in self.valid_elements
                ]
                invalid_rediscovery = [
                    elem.element_name
                    for elem in resp.output_parsed.rediscovery_elements
                    if elem.element_name not in self.valid_elements
                ]
                invalid_elements = invalid_parallel + invalid_rediscovery
                if invalid_elements:
                    task_with_context = (
                        f"{task}\n\n"
                        f"RETRY: You previously used invalid element(s): {', '.join(invalid_elements)}. "
                        f"Only use elements from the provided list."
                    )
            print("start")
            resp = self.client.responses.parse(
                model="gpt-4.1",
                temperature=0,
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT_COUNT_PARALLEL},
                    {
                        "role": "user",
                        "content": USER_PROMPT_COUNT_PARALLEL.format(
                            task=task_with_context,
                            sequential_usage=seq_formatted,
                        ),
                    },
                ],
                text_format=ParallelUsageCounts,
            )
            print("end")

            # Validate all elements are valid
            invalid_parallel = [
                elem.element_name
                for elem in resp.output_parsed.parallel_elements
                if elem.element_name not in self.valid_elements
            ]
            invalid_rediscovery = [
                elem.element_name
                for elem in resp.output_parsed.rediscovery_elements
                if elem.element_name not in self.valid_elements
            ]

            if not invalid_parallel and not invalid_rediscovery:
                break  # All elements are valid

            if attempt == max_retries - 1:
                raise ValueError("Parallel usage prediction still contains unknown elements after five attempts")

        return resp.output_parsed
