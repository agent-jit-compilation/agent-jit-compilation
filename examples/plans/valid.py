stores = await list_all_stores(detailed=True)
count = 0
for store in stores.items[:5]:
    await goto_store(store_name=store.name)
    details = await get_store_details()
    if details.review_count < 20:
        count += 1
    await goto_home()
result = f"{count} of the first five restaurants have fewer than 20 reviews."
