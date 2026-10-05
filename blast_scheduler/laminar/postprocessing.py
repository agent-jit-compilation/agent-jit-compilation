"""
Rplace old URL patterns with new ones in laminar_traces directory.
Replaces: https://real-dashdish-1rmq4pjxg-real-sites.vercel.app
With: https://real-dashdish.vercel.app
"""

from pathlib import Path

# Replace OLD_URL with NEW_URL in all JSON files in the laminar_traces directory
OLD_URL = "https://real-gomail-1glk4rtaf-real-sites.vercel.app/"
NEW_URL = "https://real-gomail.vercel.app/"


def replace_in_file(file_path: Path) -> tuple[bool, int]:
    """Replace all occurrences of OLD_URL with NEW_URL in a file."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        count = content.count(OLD_URL)

        if count == 0:
            return False, 0

        new_content = content.replace(OLD_URL, NEW_URL)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return True, count

    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        return False, 0


def main():
    script_dir = Path(__file__).parent.parent.parent
    traces_dir = script_dir / "laminar_traces"

    if not traces_dir.exists():
        print(f"Error: {traces_dir} does not exist")
        return

    print(f"Scanning {traces_dir} for JSON files...")
    print(f"Replacing: {OLD_URL}")
    print(f"With: {NEW_URL}")
    print("-" * 80)

    total_files = 0
    modified_files = 0
    total_replacements = 0

    for json_file in traces_dir.rglob("*.json"):
        total_files += 1
        was_modified, count = replace_in_file(json_file)

        if was_modified:
            modified_files += 1
            total_replacements += count
            print(f"✓ {json_file.relative_to(traces_dir)}: {count} replacement(s)")

    print("-" * 80)
    print("Summary:")
    print(f"  Total files scanned: {total_files}")
    print(f"  Files modified: {modified_files}")
    print(f"  Total replacements: {total_replacements}")


if __name__ == "__main__":
    main()
