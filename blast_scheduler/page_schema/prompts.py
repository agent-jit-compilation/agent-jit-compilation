"""Prompts for page schema generation."""

SCHEMA_PROMPT = """Produce a JSON schema (Draft 2020-12) for this webpage.

Important Rules:
1. Only describe what is visible:
- **DO NOT generate or infer properties that are not explicitly visible** in the provided screenshot(s) or HTML snapshot(s)
- Do not assume or hallucinate additional fields, actions, or properties
- Important elements visible in the screenshot(s) or HTML snapshot(s) should be included in the schema.
- If an element is visible in the screenshot, but not in the HTML snapshot, it should *STILL* be included in the schema.

2. Distinction between Elements and Properties:
- **$defs = ACTIONABLE ELEMENTS ONLY**: Each $def represents a discrete user action (click, type, toggle)
  - Examples: buttons, clickable cards, input fields, links, toggles
- **Properties within $defs = INFORMATION**: Data visible when interacting with that element
  - These are NOT separate elements, just data the agent can see/read
  - Examples: restaurantName, price, rating are properties OF restaurantCard, not separate elements
- **DO NOT create $defs for data-only properties** like names, URLs, prices, descriptions

3. Structure Rules:
- Keep the schema FLAT - one $def per actionable element type
- DO NOT create $defs for containers/wrappers (sections, layouts, rows) that are not directly interactive
- Use properties to describe what information is accessible when interacting with an element

4. Modal Rules:
- A modal is a container that overlays the main content and can be opened and closed. This includes popups, sidebars, drawers, dialogs, and overlays.
  - For example, an overlay with additional button/content is considered a modal.
  - Some pages can have multiple modals. For our purposes, it is critical that all modals are included in the schema. Similarly, there should be at least one element that opens a modal.
- All modals MUST include the literal word "Modal" in the $def name. For instance, use "aSidebarModal" not "aSidebar", "aDrawerModal" not "aDrawer"
- Actionable elements INSIDE a modal use dot notation in the $def name: "itemCustomizationModal.addToCartButton"
- The modal container itself is NOT an action, but the trigger button that opens it is the action
- **CRITICAL**: Any element that triggers a modal/sidebar/drawer MUST have an `opensModal` property linking to the modal it opens. You should reasonably infer if an element opens a modal or not.
- Add an `opensModal` property to each $def that triggers a modal:
  ```json
  "aButton": {{ "properties": {{ "opensModal": {{ "const": "aModal" }}, "itemCount": {{ "type": "integer" }} }} }},
  "bItemCard": {{ "properties": {{ "opensModal": {{ "const": "bModal" }}, ... }} }},
  ```
  - Multiple $defs can open the same modal; `opensModal` links them together
- When you define modal elements (e.g., "aModal.aButton"), identify what triggers that modal and ensure those triggers have `opensModal` set

5. Page Navigation Rules:
- Elements that navigate to a different page (not a modal overlay) MUST have an `opensPage` property
- The value MUST be one of the known page names for this site (provided below)
- This is distinct from `opensModal` which opens an overlay on the same page
- Example:
  ```json
  "someCard": {{ "properties": {{ "opensPage": {{ "const": "page1" }}, "name": {{ "type": "string" }}, ... }} }},
  "someButton": {{ "properties": {{ "opensPage": {{ "const": "page2" }} }} }}
  ```
- Use `opensPage` for: clickable cards that go to detail pages, navigation buttons, links to other pages
- Use `opensModal` for: buttons that open overlays/drawers/popups without changing the URL
{known_pages_section}

6. Naming Rules:
- Each $def must have a unique, intuitive, and descriptive id (e.g., "checkoutButton" not "button")

7. Shared Definitions:
- When the same property (with identical type/enum) appears on multiple elements, define it once in $defs and use "$ref"
- Example: If "sectionTitle" appears on sectionSeeAllButton, sectionCarouselArrowButton, etc.:
  - Define: "$defs": {{ "sectionTitle": {{ "type": "string", "enum": [...] }} }}
  - Reference: "sectionTitle": {{ "$ref": "#/$defs/sectionTitle" }}

8. Consolidate Similar Elements Across Sections:
- **CRITICAL**: When multiple sections have structurally similar elements (e.g., category cards, product cards, "See all" links, carousel buttons), create ONE generic $def with a grouping property, NOT separate $defs per section
- **NEVER include section/category names in $def names**. Section names belong ONLY in properties as grouping values.
- BAD $def names: "topCategoriesKitchenCard", "homeEssentialsCard", "fashionDealsLink", "kitchenNextSlideButton"
- GOOD $def names: "categoryCard", "sectionLink", "carouselNextButton" (with sectionTitle property)
- The grouping property (like sectionTitle) should be an enum listing ALL section names where this element type appears
- Use `x-visibleCounts` to show how many of each element appear per section
- This applies to ALL repeating elements: cards, links, buttons, carousel controls, etc.
- Example:
  ```json
  "categoryCard": {{
    "type": "object",
    "description": "Clickable cards within promotional sections",
    "x-visibleCount": 12,
    "x-visibleCounts": {{
      "Section A": 4,
      "Section B": 4,
      "Section C": 4
    }},
    "properties": {{
      "sectionTitle": {{ "$ref": "#/$defs/promoSectionTitle" }},
      "title": {{ "type": "string" }},
      "imageAlt": {{ "type": "string" }}
    }}
  }},
  "carouselNextButton": {{
    "type": "object",
    "description": "Next slide button for carousels",
    "x-visibleCount": 3,
    "properties": {{
      "sectionTitle": {{ "$ref": "#/$defs/promoSectionTitle" }},
      "ariaLabel": {{ "const": "Next slide" }}
    }}
  }}
  ```

9. Enum vs String Rules:
- Use `enum` for FIXED/SEMI-FIXED UI ELEMENTS:
  - Mode toggles and size options
  - Section headers/titles that organize content
  - Category tabs, filter options
- Use `"type": "string"` for DYNAMIC INSTANCE DATA:
  - Entity names
  - Descriptions, reviews, addresses
  - Prices, ratings (use appropriate type: string/number)
- For dynamic data, include at most 1 `example` to show the format (not multiple):
  ```json
  "price": {{ "type": "string", "examples": ["$12.99"] }}
  ```
- Omit examples entirely for self-explanatory fields (name, description, date, rating)
- When in doubt, use `"type": "string"` without examples

10. Cardinality and Required Fields:
- For repeated elements (lists of cards, items, reviews), add `x-visibleCount` indicating TOTAL visible count:
  ```json
  "aCard": {{
    "type": "object",
    "description": "...",
    "x-visibleCount": 12, // TOTAL count of aCards across all groups
    "properties": {{ ... }}
  }}
  ```
- **CRITICAL**: If an element has a grouping property (like sectionTitle, categoryName, etc.), you MUST ALSO add `x-visibleCounts` with per-group breakdown:
  ```json
  "aCard": {{
    "x-visibleCount": 12, // total
    "x-visibleCounts": {{
      "Section A": 4, // count under Section A
      "Section B": 5, // count under Section B  
      "Section C": 3  // count under Section C
    }},
    "properties": {{
      "sectionTitle": {{ "$ref": "#/$defs/sectionTitle" }}, // grouping property
      ...
    }}
  }}
  ```
- Each $def should have a description explaining what action it represents

11. Example of a well-structured schema:
```json
"$defs": {{
  "itemCard": {{
    "type": "object",
    "description": "Clickable card that navigates to item details page",
    "x-visibleCount": 27,
    "x-visibleCounts": {{
      "title1": 4, // count of itemCards under title1
      "title2": 3, // count of itemCards under title2
      "title3": 3 // count of itemCards under title3
    }},
    "properties": {{
      "title": {{ "type": "string" }},
      "name": {{ "type": "string" }},
      "price": {{ "type": "string" }},
      "opensModal": {{ "const": "someModal" }}
      ...
    }}
    ...
}}
```

URL pattern: {url}
{existing_defs_section}{html_section}{modal_hint_section}
Find attached the screenshot{plural}:"""

HTML_SECTION = """
HTML Snapshot(s):
The following HTML accessibility tree snapshot(s) show the page structure. Use this alongside the screenshot(s) to identify elements and their properties.
Only include elements and properties that are visible in either the screenshot or HTML - do not infer or hallucinate.

{html_content}
"""

EXISTING_DEFS_SECTION = """
IMPORTANT: The following element definitions already exist for this site.
You MUST reuse these exact $def names and structures when the same or similar element appears on this page.
Only create NEW $defs for elements that don't match any existing definition.

Existing $defs:
```json
{existing_defs}
```
"""

KNOWN_PAGES_SECTION = """
Known page names for this site: {known_pages}
Use ONLY these values for `opensPage`. If an element navigates to a page not in this list, omit `opensPage`.
"""

MODAL_HINT_SECTION = """
**MODAL COUNT HINT**: This page has {modal_count} modal(s) that you MUST define:
{modal_list}

You MUST create definitions for ALL of these modals and their internal elements. Ensure each modal has:
1. At least one trigger element with `opensModal` pointing to it
2. Elements inside the modal using dot notation (e.g., "modalName.closeButton")
"""
