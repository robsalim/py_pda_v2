import json
import mimetypes
import re
import subprocess
import sys
import urllib.error
import urllib.request
import argparse
from pathlib import Path

DEFAULT_REPO = "robsalim/py_pda_v2"
DEFAULT_TAG = "v2.1.6"

BODY = """## Что нового
- Удален код SM для S7-200
- Обновлен Modbus
- Обновлен README
"""

def get_token() -> str:
    try:
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, check=True
        )
        m = re.search(r"^password=(.+)$", proc.stdout, re.M)
        if not m:
            sys.exit("Error: GitHub token hin argamne.")
        return m.group(1).strip()
    except subprocess.CalledProcessError as e:
        sys.exit(f"Error: Git credential hojjechuu dide: {e}")

def api(url: str, token: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"API Error {e.code}: {e.read().decode()}")

def upload(url: str, token: str, path: str) -> dict:
    p = Path(path)
    if not p.is_file():
        sys.exit(f"Error: Faayilli '{path}' hin argamne.")
        
    ctype = mimetypes.guess_type(path)[0] or "application/zip"
    with open(path, "rb") as f:
        data = f.read()
        
    base = url.split("{")[0]
    req = urllib.request.Request(
        f"{base}?name={p.name}",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": ctype,
            "Content-Length": str(len(data)),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"Upload Error {e.code}: {e.read().decode()}")

def find_release(repo: str, tag: str, token: str):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/tags/{tag}",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        sys.exit(f"API Error {e.code}: {e.read().decode()}")

def main():
    parser = argparse.ArgumentParser(description="GitHub Release Uploader")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repository")
    parser.add_argument("--tag", default=DEFAULT_TAG, help="Release tag")
    parser.add_argument("--name", help="Release name")
    parser.add_argument("--asset", help="Path to ZIP asset")
    args = parser.parse_args()

    name = args.name or f"PDA Next-Gen {args.tag}"
    asset = args.asset or f"PDA-{args.tag}-win64.zip"

    token = get_token()
    rel = find_release(args.repo, args.tag, token)
    
    if rel is None:
        rel = api(f"https://api.github.com/repos/{args.repo}/releases", token,
                  {"tag_name": args.tag, "name": name, "body": BODY,
                   "draft": False, "prerelease": False})
        print(f"Release uumameera: {rel['html_url']}")
    else:
        print(f"Release argameera: {rel['html_url']}")

    if any(a["name"] == Path(asset).name for a in rel.get("assets", [])):
        print(f"Asset '{Path(asset).name}' duraan fe'ameera, ni darba.")
        return
        
    up = upload(rel["upload_url"], token, asset)
    print(f"Asset fe'ameera: {up['name']} ({up['size']} bytes) -> {up['browser_download_url']}")

if __name__ == "__main__":
    main()