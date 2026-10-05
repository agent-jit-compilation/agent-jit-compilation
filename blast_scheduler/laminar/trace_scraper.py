"""Scrape traces from Laminar.

@TODO(ronyw): Refactor this to use APIs when they are available.
"""

import argparse
import datetime
import json
import os
import time
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import dotenv
import ray
import requests
from tqdm import tqdm

from .utils import CookieParser


def _retry_request(
    url: str, cookies: Dict[str, str], max_retries: int = 3, backoff_factor: float = 0.5
) -> requests.Response:
    """
    Make an HTTP GET request with retry logic and exponential backoff.

    Args:
        url: The URL to request
        cookies: Authentication cookies
        max_retries: Maximum number of retry attempts (default: 3)
        backoff_factor: Multiplier for exponential backoff (default: 0.5)

    Returns:
        Response object

    Raises:
        Exception: If all retries fail
    """
    last_exception = None

    for attempt in range(max_retries):
        try:
            response = requests.get(url, cookies=cookies, timeout=30)

            # If status is 5xx (server error), retry
            if response.status_code >= 500:
                if attempt < max_retries - 1:
                    sleep_time = backoff_factor * (2**attempt)
                    time.sleep(sleep_time)
                    continue

            return response

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_exception = e
            if attempt < max_retries - 1:
                sleep_time = backoff_factor * (2**attempt)
                time.sleep(sleep_time)
                continue
            else:
                raise

    # If we get here, all retries failed
    if last_exception:
        raise last_exception
    return response


@ray.remote
def _get_span_details_by_id(
    project_id: str, trace_id: str, span_id: str, cookies: Dict[str, str]
):
    url = f"https://laminar.sh/api/projects/{project_id}/traces/{trace_id}/spans/{span_id}"
    response = _retry_request(url, cookies)

    if response.status_code != 200:
        raise ValueError(
            f"Failed to fetch span {span_id}: HTTP {response.status_code}\n"
            f"Response: {response.text[:500]}"
        )

    try:
        return response.json()
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Failed to parse JSON for span {span_id}: {e}\n"
            f"Response text: {response.text[:500]}"
        )


@ray.remote
def _get_all_spans_details(project_id: str, trace_id: str, cookies: Dict[str, str]):
    """Process all spans for a single trace ID in parallel."""
    url = f"https://laminar.sh/api/projects/{project_id}/traces/{trace_id}/spans?startTime=&endTime="
    response = _retry_request(url, cookies)

    if response.status_code != 200:
        raise ValueError(
            f"Failed to fetch spans for trace {trace_id}: HTTP {response.status_code}\n"
            f"Response: {response.text[:500]}"
        )

    try:
        spans = response.json()
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Failed to parse JSON for trace {trace_id}: {e}\n"
            f"Response text: {response.text[:500]}"
        )

    span_ids = [span["spanId"] for span in spans]

    futures = [
        _get_span_details_by_id.remote(project_id, trace_id, span_id, cookies)
        for span_id in span_ids
    ]

    results = []
    for future in tqdm(
        futures, desc=f"Fetching spans for {trace_id[:8]}...", leave=False
    ):
        results.append(ray.get(future))

    return {"trace_id": trace_id, "spans": results}


class TraceScraper:
    """Scraper for fetching traces and spans from Laminar."""

    def __init__(self, project_id: str, cookies: Dict[str, str]):
        """
        Initialize the TraceScraper.

        Args:
            project_id: The Laminar project ID
            cookies: Authentication cookies for API requests
        """
        self.project_id = project_id
        self.cookies = cookies

    @staticmethod
    def _convert_time_string_to_api_format(time_string: str) -> str:
        """
        Convert time string from format "2025-10-01-23-50-00" to API format.

        Args:
            time_string: Time in format "YYYY-MM-DD-HH-MM-SS"

        Returns:
            Time in API format "YYYY-MM-DDTHH:MM:SS.000Z" (UTC)
        """
        # Parse the time string
        parts = time_string.split("-")
        if len(parts) != 6:
            raise ValueError(
                f"Invalid time format: {time_string}. Expected format: YYYY-MM-DD-HH-MM-SS"
            )

        year, month, day, hour, minute, second = map(int, parts)

        # Create datetime object in Pacific timezone
        dt = datetime.datetime(
            year,
            month,
            day,
            hour,
            minute,
            second,
            tzinfo=ZoneInfo("America/Los_Angeles"),
        )

        # Convert to UTC and format for API
        return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def _get_all_trace_ids(self, start_time: str, end_time: str) -> List[str]:
        """
        Get all trace IDs in the specified time period, handling pagination.

        Args:
            start_time: Start time in API format
            end_time: End time in API format

        Returns:
            List of trace IDs
        """
        all_trace_ids = []
        page_number = 0

        while True:
            url = f"https://laminar.sh/api/projects/{self.project_id}/traces?pageNumber={page_number}&startDate={start_time}&endDate={end_time}"
            response = _retry_request(url, self.cookies)

            if response.status_code != 200:
                raise ValueError(
                    f"Failed to fetch trace IDs: HTTP {response.status_code}\n"
                    f"Response: {response.text[:500]}\n"
                    f"This usually means authentication failed or the project ID is invalid."
                )

            try:
                data = response.json()
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"Failed to parse JSON response: {e}\n"
                    f"Response text: {response.text[:500]}"
                )

            items = data.get("items", [])
            if not items:
                break

            # Filter for agent.run traces on this page
            page_trace_ids = [
                trace["id"] for trace in items if trace["topSpanName"] == "agent.run"
            ]
            all_trace_ids.extend(page_trace_ids)

            # Check if there are more pages
            # If this page returned fewer items than expected or is empty, we're done
            if len(items) < 50:
                break

            page_number += 1

        return all_trace_ids

    def _process_traces(self, trace_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Process multiple traces in parallel.

        Args:
            trace_ids: List of trace IDs to process

        Returns:
            List of trace data with spans
        """
        futures = [
            _get_all_spans_details.remote(self.project_id, trace_id, self.cookies)
            for trace_id in trace_ids
        ]

        results = []
        for future in tqdm(futures, desc="Processing traces", unit="trace"):
            results.append(ray.get(future))

        return results

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        """
        Get a specific trace by ID.

        Args:
            trace_id: The trace ID to fetch

        Returns:
            Dictionary containing trace_id and spans data
        """
        results = self._process_traces([trace_id])
        return results[0] if results else {"trace_id": trace_id, "spans": []}

    def get_trace_in_time_period(
        self, start_time: str, end_time: str
    ) -> List[Dict[str, Any]]:
        """
        Get all traces in the specified time period.

        Args:
            start_time: Start time in format "YYYY-MM-DD-HH-MM-SS" (Pacific timezone)
            end_time: End time in format "YYYY-MM-DD-HH-MM-SS" (Pacific timezone)

        Returns:
            List of trace data with spans
        """
        # Convert time strings to API format
        api_start_time = self._convert_time_string_to_api_format(start_time)
        api_end_time = self._convert_time_string_to_api_format(end_time)

        # Get all trace IDs in the time period
        trace_ids = self._get_all_trace_ids(api_start_time, api_end_time)
        print(f"Found {len(trace_ids)} trace IDs")

        # Process all traces
        return self._process_traces(trace_ids)


if __name__ == "__main__":
    dotenv.load_dotenv()

    laminar_project_id = os.getenv("LAMINAR_PROJECT_ID")
    laminar_cookies_path = os.getenv("LAMINAR_COOKIES_PATH")
    if not laminar_project_id or not laminar_cookies_path:
        raise ValueError("LAMINAR_PROJECT_ID and LAMINAR_COOKIES_PATH must be set")

    parser = argparse.ArgumentParser(
        description="Scrape traces from Laminar. Use either --trace-id OR --start-time/--end-time."
    )
    parser.add_argument(
        "--trace-id",
        type=str,
        help="Specific trace ID to fetch",
    )
    parser.add_argument(
        "--start-time",
        type=str,
        help="Start time in format YYYY-MM-DD-HH-MM-SS (Pacific timezone)",
    )
    parser.add_argument(
        "--end-time",
        type=str,
        help="End time in format YYYY-MM-DD-HH-MM-SS (Pacific timezone)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./laminar_traces",
        help="Directory to save output JSON files (default: laminar_traces)",
    )
    args = parser.parse_args()

    # Validate arguments
    if args.trace_id and (args.start_time or args.end_time):
        parser.error("Cannot specify both --trace-id and --start-time/--end-time")
    if not args.trace_id and not (args.start_time and args.end_time):
        parser.error(
            "Must specify either --trace-id OR both --start-time and --end-time"
        )

    cookies: Dict[str, str] = CookieParser(
        laminar_cookies_path, from_json=False
    ).cookies

    # Initialize Ray
    ray.init()

    # Create scraper instance
    scraper = TraceScraper(laminar_project_id, cookies)
    output_dir = args.output_dir

    # Fetch traces
    if args.trace_id:
        print(f"Fetching trace ID: {args.trace_id}")
        result = scraper.get_trace(args.trace_id)
        results = [result]
    else:
        output_dir = os.path.join(output_dir, f"{args.start_time}_to_{args.end_time}")
        print(f"Fetching traces from {args.start_time} to {args.end_time}")
        results = scraper.get_trace_in_time_period(args.start_time, args.end_time)

    # Save results
    os.makedirs(output_dir, exist_ok=True)
    for result in results:
        trace_id = result["trace_id"]
        filename = os.path.join(output_dir, f"{trace_id}.json")
        with open(filename, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Saved {filename}")

    print(f"Processed {len(results)} trace(s) successfully!")
