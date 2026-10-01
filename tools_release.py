# -*- coding: utf-8 -*-
"""
Вспомогательный скрипт: создаёт GitHub Release v2.1.3 и загружает ZIP-артефакт.
Токен берётся из сохранённых git credentials (git credential fill), не печатается.
"""
import json
import mimetypes
import re
import subprocess
import sys
import urllib.error
import urllib.request

# Параметры релиза: TAG [NAME [ASSET]] по умолчанию, либо аргументы командной строки
REPO = "robsalim/py_pda_v2"
DEFAULT_TAG = "v2.1.4"

BODY = """## Что нового в v2.1.4

Документация и встроенная справка под новый функционал (без изменений логики с v2.1.3):
- **README**: раздел «Вкладка Help», SM-области S7-200, Hi-Lo/Lo-Hi в Bits,
  значения в легенде веб-чартов, установка из готового ZIP-архива Releases,
  pytest больше не ставится отдельно (он в requirements.txt)
- **Help-вкладка приложения**: раздел Bits дополнен — Bit 0 верхняя дорожка,
  закреплённые цвета, кнопка порядка байт Hi-Lo / Lo-Hi (сохраняется, есть в веб-клиенте)
- **36/36 тестов** ✅

Полный список новых возможностей — в релизе v2.1.3.

## Как запустить
1. Распакуйте архив в любую папку
2. Запустите `main.exe`
3. Файл `db_config.json` рядом с exe — подключение к БД (по умолчанию SQLite)
"""


def get_token() -> str:
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True,
    )
    m = re.search(r"^password=(.+)$", proc.stdout, re.M)
    if not m:
        sys.exit("Не найден токен GitHub в git credential store")
    return m.group(1).strip()


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
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def upload(url: str, token: str, path: str) -> dict:
    ctype = mimetypes.guess_type(path)[0] or "application/zip"
    with open(path, "rb") as f:
        data = f.read()
    # upload_url — шаблон вида ".../assets{?name,label}": обрезаем и подставляем name
    base = url.split("{")[0]
    req = urllib.request.Request(
        f"{base}?name={path}",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": ctype,
            "Content-Length": str(len(data)),
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def find_release(tag: str, token: str):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/releases/tags/{tag}",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError:
        return None


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TAG
    name = sys.argv[2] if len(sys.argv) > 2 else f"PDA Next-Gen {tag}"
    asset = sys.argv[3] if len(sys.argv) > 3 else f"PDA-{tag}-win64.zip"

    token = get_token()
    rel = find_release(tag, token)
    if rel is None:
        rel = api(f"https://api.github.com/repos/{REPO}/releases", token,
                  {"tag_name": tag, "name": name, "body": BODY,
                   "draft": False, "prerelease": False})
        print("release создан:", rel["html_url"])
    else:
        print("release найден:", rel["html_url"])

    if any(a["name"] == asset for a in rel.get("assets", [])):
        print("asset уже загружен, пропускаю")
        return
    up = upload(rel["upload_url"], token, asset)
    print("asset:", up["name"], up["size"], "bytes,", up["browser_download_url"])


if __name__ == "__main__":
    main()
