import sys, json
sys.path.insert(0, 'src')
from ufc_ingest.scrape_fight_detail import scrape_fight
url = sys.argv[1]
print('fetching', url)
res = scrape_fight(url)
print(json.dumps(res, indent=2))
