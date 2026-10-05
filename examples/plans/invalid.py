# Intentionally missing goto_store: the initial page is the home page.
stores = await list_all_stores(detailed=True)
details = await get_store_details()
result = details
