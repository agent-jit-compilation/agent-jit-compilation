"""Common utils for working with Laminar.

Unfortunately, Laminar does not provide a public API for getting traces.
Therefore, we need to scrape the traces by providing the cookies from the browser.
This script is used to parse the cookies from the browser and save them to a JSON file.
"""

import json
import urllib.parse
from pathlib import Path
from typing import Dict


class CookieParser:
    """
    Parse cookies from a text file or JSON file.
    """

    def __init__(self, file_path: str, from_json: bool = False):
        # Convert to Path object and resolve to absolute path
        self.file_path = Path(file_path).resolve()
        if from_json:
            self._cookies = self.load_cookies_from_json(self.file_path)
        else:
            self._cookies = self.load_cookies_from_file(self.file_path)

    @property
    def cookies(self) -> Dict[str, str]:
        return self._cookies

    def load_cookies_from_file(self, file_path: Path) -> Dict[str, str]:
        """
        Load and parse cookies from a text file.

        Args:
            file_path: Path to the cookie file

        Returns:
            Dictionary of parsed cookies
        """
        try:
            with file_path.open("r", encoding="utf-8") as f:
                cookie_string = f.read().strip()
                self._cookies = self._parse_cookie_string(cookie_string)
            return self._cookies
        except FileNotFoundError:
            print(f"Error: File '{file_path}' not found")
            raise FileNotFoundError(f"File '{file_path}' not found")
        except Exception as e:
            print(f"Error reading file '{file_path}': {e}")
            raise Exception(f"Error reading file '{file_path}': {e}")

    def load_cookies_from_json(self, file_path: Path) -> Dict[str, str]:
        """
        Load cookies from JSON file.

        Args:
            file_path: Path to the JSON cookie file

        Returns:
            Dictionary of cookies
        """
        try:
            with file_path.open("r", encoding="utf-8") as f:
                self._cookies = json.load(f)
            return self._cookies
        except FileNotFoundError:
            print(f"Error: File '{file_path}' not found")
            raise FileNotFoundError(f"File '{file_path}' not found")
        except json.JSONDecodeError:
            print(f"Error: Invalid JSON in '{file_path}'")
            raise
        except Exception as e:
            print(f"Error reading '{file_path}': {e}")
            raise

    def save_cookies_to_json(self, output_file: str) -> bool:
        """
        Save cookies dictionary to JSON file.

        Args:
            output_file: Path to output JSON file

        Returns:
            True if successful, False otherwise
        """
        try:
            output_path = Path(output_file).resolve()
            # Create parent directories if they don't exist
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("w", encoding="utf-8") as f:
                json.dump(self._cookies, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            raise Exception(f"Error writing to '{output_file}': {e}")

    def _parse_cookie_string(self, cookie_string: str) -> Dict[str, str]:
        """
        Parse a cookie string into a dictionary.

        Args:
            cookie_string: Raw cookie string from browser

        Returns:    
            Dictionary of cookie name -> value pairs
        """
        cookies = {}

        # Split by semicolon and process each cookie
        cookie_pairs = cookie_string.strip().split(";")

        for pair in cookie_pairs:
            pair = pair.strip()
            if not pair:
                continue

            # Split on first '=' to get key-value pair
            if "=" in pair:
                name, value = pair.split("=", 1)
                name = name.strip()
                value = value.strip()

                # URL decode the value
                try:
                    value = urllib.parse.unquote(value)
                except Exception:
                    # If decoding fails, use the original value
                    pass

                cookies[name] = value

        return cookies
