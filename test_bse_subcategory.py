# test_bse_subcategory.py
from bse import BSE
from bse.constants import CATEGORY
from datetime import datetime
import tempfile, json

with tempfile.TemporaryDirectory() as tmp:
    with BSE(download_folder=tmp) as bse:
        # Test 1: category=Result only (no subcategory)
        data = bse.announcements(
            scripcode="532762",
            category=CATEGORY.RESULT,
            from_date=datetime(2024, 1, 1),
            to_date=datetime(2025, 12, 31),
        )
        total = data.get("Table1", [{}])[0].get("ROWCNT", 0)
        table = data.get("Table", [])
        print(f"Category=Result only: {total} total, {len(table)} on page 1")

        # Print ALL unique SUBCATNAME values
        subcats = set()
        for item in table:
            subcats.add(item.get("SUBCATNAME", ""))
        print(f"\nUnique SUBCATNAME values found:")
        for sc in sorted(subcats):
            print(f"  '{sc}'")

        # Print first 5 items
        print(f"\nFirst 5 records:")
        for item in table[:5]:
            print(f"  SUBCATNAME: {item.get('SUBCATNAME','')}")
            print(f"  NEWSSUB:    {item.get('NEWSSUB','')[:80]}")
            print(f"  ATTACHMENT: {item.get('ATTACHMENTNAME','')}")
            print()

        # Test 2: try subcategory='Financial Results'
        print("=" * 60)
        print("Testing subcategory='Financial Results'...")
        data2 = bse.announcements(
            scripcode="532762",
            category=CATEGORY.RESULT,
            subcategory="Financial Results",
            from_date=datetime(2024, 1, 1),
            to_date=datetime(2025, 12, 31),
        )
        total2 = data2.get("Table1", [{}])[0].get("ROWCNT", 0)
        print(f"Category=Result + Subcategory='Financial Results': {total2} total")

        for item in data2.get("Table", [])[:3]:
            print(f"  SUBCATNAME: {item.get('SUBCATNAME','')}")
            print(f"  NEWSSUB:    {item.get('NEWSSUB','')[:80]}")
            print()
