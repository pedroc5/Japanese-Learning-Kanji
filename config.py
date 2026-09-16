#!/usr/bin/env python3
"""Everything that depends on *this* machine, in one place.

The rest of the code deliberately knows no absolute paths. Two kinds of
location exist:

* **Inside the skill folder** — caches, the study history, the per-day content
  JSON. Those are resolved from `__file__` by the module that owns them and
  are not configurable: they have to sit next to the code so that a
  launchd-spawned process can read them back (see build_page.DEFAULT_HISTORY).
* **Outside it** — where the generated pages and logs go, which host the
  VOICEVOX engine listens on, which port the ask server binds. Those are
  personal, so they live here and are overridable without editing code.

Resolution order, first hit wins:

1. the environment variable named in SETTINGS below (`KANJI_OUTPUT_ROOT`, …)
2. `config.json` in the skill folder — copy `config.example.json` and edit
   (point `KANJI_CONFIG` elsewhere to keep it outside the checkout)
3. the built-in default

`config.json` is not tracked by git: it is the one file that describes your
machine, so a fork starts from the defaults rather than from someone else's
folder layout.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_FILE = Path(os.environ.get("KANJI_CONFIG") or HERE / "config.json")

# key -> (environment variable, built-in default)
SETTINGS = {
    # 生成物（HTMLのページ・ログ）の置き場。週ごとのフォルダはこの下に作られる。
    "output_root": ("KANJI_OUTPUT_ROOT", "~/Documents/kanji-practice"),
    # ページ内のチャット／まとめサーバーが待ち受けるポート。
    "ask_server_port": ("KANJI_ASK_PORT", 8765),
    # ローカルのVOICEVOXエンジン。
    "voicevox_engine_url": ("VOICEVOX_ENGINE", "http://127.0.0.1:50021"),
    # 起動していないときに起こすためのエンジン本体（macOSのVOICEVOX.app内）。
    "voicevox_engine_binary": (
        "VOICEVOX_ENGINE_BINARY",
        "/Applications/VOICEVOX.app/Contents/Resources/vv-engine/run",
    ),
    # launchdのラベル接頭辞。plistのLabelとファイル名がこれに従う。
    "launchd_prefix": ("KANJI_LAUNCHD_PREFIX", "com.kanji-practice"),
}


def _file_values() -> dict:
    """config.json の中身。無ければ空。壊れていても落とさない。

    設定ファイルが読めないくらいで毎朝のビルドを止める理由はない——既定値で
    動くほうがましなので、JSONの壊れは黙って無視して既定に戻す。
    """
    try:
        text = CONFIG_FILE.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        loaded = json.loads(text)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


_FILE = _file_values()


def get(key: str):
    """設定値をひとつ取り出す（環境変数 → config.json → 既定値の順）。"""
    try:
        env_name, default = SETTINGS[key]
    except KeyError:
        raise KeyError(f"unknown config key: {key}") from None
    from_env = os.environ.get(env_name)
    if from_env not in (None, ""):
        return from_env
    if key in _FILE and _FILE[key] not in (None, ""):
        return _FILE[key]
    return default


def path(key: str) -> Path:
    """パスとして取り出す。`~` はここで展開する。"""
    return Path(str(get(key))).expanduser()


def port(key: str) -> int:
    """ポート番号として取り出す。環境変数は文字列で来るのでintに直す。"""
    return int(get(key))


def describe() -> str:
    """いま効いている設定と、その出どころを1行ずつ。`--show-config` 用。"""
    lines = []
    for key, (env_name, default) in SETTINGS.items():
        if os.environ.get(env_name) not in (None, ""):
            source = f"env {env_name}"
        elif key in _FILE and _FILE[key] not in (None, ""):
            source = f"{CONFIG_FILE}"
        else:
            source = "default"
        lines.append(f"{key} = {get(key)}   ({source})")
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe())
