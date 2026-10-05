# Examples

The planner examples count how many of the first five restaurants have fewer than 20 reviews, using the supplied Dashdish registry:

- `invalid.py` skips navigating to each store, so it fails the state-contract check.
- `valid.py` visits each store, reads `review_count` with `get_store_details` (the store list does not include it), and counts in Python.
- `inference.py` is also valid but adds an unnecessary final model call, so it ranks as more expensive.

Check and rank the programs without a browser or API key:

```sh
uv run agent-jit planner rank \
  examples/plans/invalid.py examples/plans/inference.py examples/plans/valid.py \
  --registry data/registries/dashdish.json --state '{"page_type":"home"}'
```
