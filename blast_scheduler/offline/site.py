import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional


class SiteRep:
    def __init__(self, site_name: str):
        """Initialize a SiteRep."""
        self.site_name = site_name
        # Maps element_name -> trace_id -> [step_ids]
        self.element_mappings: Dict[str, Dict[str, List[int]]] = defaultdict(
            lambda: defaultdict(list)
        )
        # Maps element_name -> description
        self.element_descriptions: Dict[str, str] = {}

    def __repr__(self) -> str:
        num_traces = len(
            set(
                trace_id
                for trace_dict in self.element_mappings.values()
                for trace_id in trace_dict
            )
        )
        return (
            f"SiteRep(site={self.site_name}, "
            f"elements={len(self.element_mappings)}, "
            f"traces={num_traces})"
        )

    @classmethod
    def from_file(cls, file_path: str) -> "SiteRep":
        """Create SiteRep from JSON file."""
        with open(file_path, "r") as f:
            return cls.from_dict(json.load(f))

    @classmethod
    def from_dict(cls, data: dict) -> "SiteRep":
        """Create SiteRep from dictionary."""
        site = cls(data["site_name"])

        # Load element mappings - handle both old and new format
        element_mappings_data = data.get("element_mappings", {})
        for element_name, element_data in element_mappings_data.items():
            # New format: {"description": "...", "traces": {...}}
            if isinstance(element_data, dict) and "traces" in element_data:
                # Load description if present
                if "description" in element_data:
                    site.element_descriptions[element_name] = element_data[
                        "description"
                    ]
                # Load trace mappings
                for trace_id, step_ids in element_data["traces"].items():
                    site.element_mappings[element_name][trace_id] = step_ids
            # Old format: {trace_id: [step_ids], ...}
            else:
                for trace_id, step_ids in element_data.items():
                    site.element_mappings[element_name][trace_id] = step_ids

        return site

    def add_mapping(
        self, element_name: str, trace_id: str, step_ids: List[int]
    ) -> None:
        """Add step mappings for a specific trace and element."""
        self.element_mappings[element_name][trace_id].extend(step_ids)

    def get_element_names(self) -> List[str]:
        """Get list of all element names."""
        return list(self.element_mappings.keys())

    def get_traces_for_element(self, element_name: str) -> List[str]:
        """Get list of trace IDs that interacted with a specific element."""
        if element_name not in self.element_mappings:
            return []
        return list(self.element_mappings[element_name].keys())

    def get_steps_for_element_trace(
        self, element_name: str, trace_id: str
    ) -> List[int]:
        """Get step IDs for a specific element in a specific trace."""
        if element_name not in self.element_mappings:
            return []
        if trace_id not in self.element_mappings[element_name]:
            return []
        return self.element_mappings[element_name][trace_id]

    def set_element_description(self, element_name: str, description: str) -> None:
        """Set the description for an element.

        Args:
            element_name: Name of the element
            description: Description of what the element is/does
        """
        self.element_descriptions[element_name] = description

    def get_element_description(self, element_name: str) -> Optional[str]:
        """Get the description for an element.

        Args:
            element_name: Name of the element

        Returns:
            Description if set, None otherwise
        """
        return self.element_descriptions.get(element_name)

    def to_dict(self) -> dict:
        """Convert SiteRep to dictionary format for JSON serialization."""
        element_mappings_dict = {}
        for element_name, trace_dict in self.element_mappings.items():
            element_mappings_dict[element_name] = {
                "description": self.element_descriptions.get(element_name, ""),
                "traces": dict(trace_dict),
            }

        return {
            "site_name": self.site_name,
            "element_mappings": element_mappings_dict,
        }

    def save(self, suffix: str = "") -> None:
        """Save SiteRep to JSON file.

        Args:
            suffix: Optional suffix to append to filename (e.g., '_gpt-4.1' -> 'site_rep_gpt-4.1.json')
        """
        sites_dir = Path("saved_sites") / self.site_name
        sites_dir.mkdir(parents=True, exist_ok=True)

        site_rep_file = sites_dir / f"site_rep{suffix}.json"
        if not site_rep_file.exists():
            with open(site_rep_file, "w") as f:
                json.dump(self.to_dict(), f, indent=2)
        else:
            # Load existing site rep and merge
            with open(site_rep_file, "r") as f:
                existing_site_rep = json.load(f)

            existing_element_mappings = existing_site_rep.get("element_mappings", {})

            # Merge element mappings and descriptions
            for element_name, trace_dict in self.element_mappings.items():
                if element_name not in existing_element_mappings:
                    existing_element_mappings[element_name] = {
                        "description": self.element_descriptions.get(element_name, ""),
                        "traces": {},
                    }

                # Handle old format (convert to new format)
                if "traces" not in existing_element_mappings[element_name]:
                    old_traces = existing_element_mappings[element_name]
                    existing_element_mappings[element_name] = {
                        "description": "",
                        "traces": old_traces,
                    }

                # Update description if we have one
                if element_name in self.element_descriptions:
                    existing_element_mappings[element_name]["description"] = (
                        self.element_descriptions[element_name]
                    )

                # Merge traces
                existing_traces = existing_element_mappings[element_name]["traces"]
                for trace_id, step_ids in trace_dict.items():
                    if trace_id not in existing_traces:
                        existing_traces[trace_id] = []

                    # Only add step_ids that aren't already present to avoid duplicates
                    existing_ids = set(existing_traces[trace_id])
                    existing_traces[trace_id].extend(
                        [s for s in step_ids if s not in existing_ids]
                    )

            existing_site_rep["element_mappings"] = existing_element_mappings
            with open(site_rep_file, "w") as f:
                json.dump(existing_site_rep, f, indent=2)
