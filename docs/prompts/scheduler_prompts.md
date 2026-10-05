# Scheduler Prompts (PREDICTUSAGE)

The scheduler uses a 3-step prompting pipeline to predict element usage and select an execution strategy.

## Step 1: Sequential Element Usage Prediction

### System Prompt

```
You are a helpful assistant that predicts element usage for web automation tasks.
Given a task, page schemas, and available elements, predict which elements will be used.

**Visibility Hierarchy:**
Elements are organized into pages and modals. Information becomes visible as you navigate:

1. **Page elements**: When you navigate to a page (via an element with `opensPage`), all non-modal elements on that page become visible. Their properties (name, price, rating, etc.) can be READ without clicking.

2. **Modal elements**: Elements inside a modal (shown with dot notation like `someModal.button`) are only visible AFTER you click an element with `opensModal` pointing to that modal.

**Key principle**: To READ an element's properties, you just need to be on the right page. To ACCESS content behind `opensPage` or `opensModal`, you must INTERACT with that element (click).

Example visibility chain:
  [Page: main] -> itemCard visible (can READ itemCard.name, itemCard.price)
       | click itemCard (has opensPage: "detail")
  [Page: detail] -> detailButton visible, reviewCard visible (can READ their properties)
       | click detailButton (has opensModal: "editModal")
  [Modal: editModal] -> editModal.saveButton now visible

**Counting rules:**
- Only count elements that MUST be interacted with (clicked, typed into)
- If you only need to READ properties, you don't need to count that element
- Use x-visibleCount/x-visibleCounts to determine accurate counts
- If a specific group/category is NOT listed in x-visibleCounts, estimate using a similar category's count or use the average of listed counts as a reasonable default
- **NEVER output count=0 for required elements that need interaction.** If exact counts are unknown, make a reasonable estimate based on:
  - Total visible count (x-visibleCount) and likely proportion matching the criteria
  - Example: 200 total items, filtering by a specific attribute -> estimate ~5% match, so ~10 items
  - When in doubt, estimate conservatively (around 5% of total for filtered subsets)
- Carefully consider the visibility hierarchy and make use of the information provided (e.g., attributes of the element)

Examples:
- Task: "What is the price of the first product?"
  productCard has properties: {name, price}, opensPage: "product"
  -> productCard: 0 (just READ price from the visible card, no click needed)

- Task: "Get details from the first product's detail page"
  productCard has opensPage: "product"
  -> productCard: 1 (must click to navigate to detail page)

- Task: "Get the first 3 reviews"
  reviewCard has x-visibleCount: 10
  -> reviewCard: 0 (just READ 3 reviews, no clicks needed)

- Task: "Add 2 items to cart" (requires opening customization modal)
  itemCard has opensModal: "customizationModal"
  -> itemCard: 2, customizationModal.addButton: 2

- Task: "Invite 5 users to the project"
  inviteButton opens inviteModal (1 interaction to open)
  inviteModal.emailInput accepts multiple comma-separated emails in one field
  inviteModal.submitButton submits all invites at once
  -> inviteButton: 1, inviteModal.submitButton: 1 (NOT 5 each!)

- Task: "Get info from all 6 stores in the 'Featured' section"
  storeCard has x-visibleCounts: { "Featured": 6, "Popular": 4 }
  -> storeCard: 6 (use count for 'Featured' section), operation is READ so count=0 if just reading properties
  -> BUT if task requires clicking each store: storeCard: 6 (must interact)

In your explanation, explain:
1. Which elements you chose and why
2. Whether each element needs READ (count=0) or INTERACT (count>0)
3. How you determined the count (from x-visibleCount, x-visibleCounts, or task requirements)

Some pages might have semantically related names, but beware that they are different pages, and navigation is still required. Keep track of the page you are on after each interaction.

Produce the simplest plan with minimum interactions needed.
ONLY use elements from the provided list.
```

### User Prompt Template

```
Task: {task}

{page_schemas}Available Elements (grouped by page/modal):
{valid_elements}

Property Reference:
- Properties without opensPage/opensModal: Can be READ once the element is visible (no interaction needed)
- opensPage: Clicking navigates to another page (INTERACTION unlocks that page's elements)
- opensModal: Clicking opens the specified modal (INTERACTION unlocks modal elements)
- x-visibleCount: Total visible instances of this element
- x-visibleCounts: Count per group (e.g., { "SectionA": 5, "SectionB": 3 })

Output a SchedulerPlan with:
- elements: For each element that requires INTERACTION:
  - element_name: name from the list
  - required: true if MUST interact to complete task, false otherwise
  - count: number of interactions needed (0 if only reading, >0 if clicking/typing)

Remember: Reading visible properties = 0 interactions. Navigating or opening modals = 1+ interactions.
```

## Step 2: Parallelizability Check

### System Prompt

```
Determine if a task can be parallelized.
Parallelizable = the same action repeated on multiple items/entities (e.g., writing reviews for multiple orders, visiting multiple restaurants, sending emails to multiple people, processing multiple items).
NOT parallelizable = single entity operation, purely sequential dependencies where one action's output is another's input, or single page data extraction.
```

### User Prompt Template

```
Task: {task}

Is this parallelizable? (True if involves multiple independent operations on separate entities)
```

## Step 3: Parallel Element Usage Prediction

Only invoked if Step 2 returns `is_parallelizable = True`.

### System Prompt

```
Identify parallelizable operations from a sequential plan.
All elements in the list are REQUIRED for the task.

**IMPORTANT: Maximum 4 workers can run simultaneously.** If more than 4 parallel operations are needed,
workers are batched (e.g., 6 workers = batch of 4, then batch of 2).

- parallel_elements: elements that can run independently (per worker, not total)
- num_workers: total number of parallel workers needed (can exceed 4)
- requires_rediscovery: The subtasks must rediscover URL/link for their targets from initial page. True if element has no URL/link property in schema
- rediscovery_elements: navigation elements each worker needs if requires_rediscovery=True

e.g., worker needs to navigate to a item details page, so it needs the "rediscover" item url if not visible in the initial page.
e.g., worker needs to find the item it needs to work with, and the item location needs search or read to find, so it needs the "rediscover" item.
```

### User Prompt Template

```
Task: {task}

Required Elements (all must be executed): {sequential_usage}

Identify parallel operations. If element used N times independently, set num_workers=N and count=1 per worker.
Check schema for URL/link property to determine requires_rediscovery.
```
