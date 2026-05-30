from bse import BSE
from datetime import datetime
import tempfile, json

with tempfile.TemporaryDirectory() as tmp:
    with BSE(download_folder=tmp) as bse:
        data = bse.announcements(
            scripcode="532762",
            from_date=datetime(2018, 1, 1),
            to_date=datetime.now(),
        )

        total = data.get("Table1", [{}])[0].get("ROWCNT", 0)
        print("Total announcements:", total)

        table = data.get("Table", [])
        print("Items on this page:", len(table))

        if table:
            print("\nFirst item raw fields:")
            print(json.dumps(table[0], indent=2))
