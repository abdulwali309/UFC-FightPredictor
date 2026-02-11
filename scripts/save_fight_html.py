import argparse
import os
import re
import requests


def normalize_url(value: str) -> str:
    if value.startswith("http://ufcstats.com"):
        return value
    if value.startswith("/"):
        return "http://ufcstats.com" + value
    if value.startswith("fight-details/"):
        return "http://ufcstats.com/" + value
    if re.fullmatch(r"[a-f0-9]+", value):
        return f"http://ufcstats.com/fight-details/{value}"
    # fallback to treat as path
    return "http://ufcstats.com/" + value.lstrip("/")


def main():
    parser = argparse.ArgumentParser(description="Fetch and save UFCStats fight-details HTML")
    parser.add_argument("fight", help="Fight ID or fight-details URL")
    parser.add_argument("--out-dir", default="tests/data", help="Output directory")
    args = parser.parse_args()

    url = normalize_url(args.fight)
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()

    fight_id = url.rstrip("/").split("/")[-1]
    os.makedirs(args.out_dir, exist_ok=True)
    out_path = os.path.join(args.out_dir, f"sample_fight_{fight_id}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(resp.text)
    print("saved", out_path)


if __name__ == "__main__":
    main()
