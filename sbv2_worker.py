#!/usr/bin/env python3
"""Style-BERT-VITS2 の合成そのもの。**venvのpythonで動く**（.sbv2venv/bin/python）。

sbv2.py から1ビルドにつき1回だけ呼ばれる。標準入力にJSONで仕事を全部渡し、
標準出力にJSONで結果を返す。分けてあるのは、`build_page.py` を標準ライブラリだけで
動かしたままにするため — torchもtransformersも、このファイルの中にしか無い。
まとめて渡すのは、BERT（1.4GB）とモデルの読み込みが1文あたり2秒の合成より高くつくから。

読みは **ページのふりがなから作って渡す**（given_phone/given_tone）。
Style-BERT-VITS2 の読みはVOICEVOXと同じOpenJTalkなので、放っておけば同じところで
同じように間違える（猿人→サルジン、はち→ワチ、机の角→ツクエノカク）。ふりがなは
jishoで確認済みの読みなので、そちらを正解として押し込む。**辞書を育てる必要が無くなる。**
"""

from __future__ import annotations

import json
import sys
import warnings
import wave
from pathlib import Path


def main() -> int:
    warnings.filterwarnings("ignore")
    from loguru import logger
    logger.remove()                     # 進捗ログは標準出力を汚す（ここはJSONを返す）

    job = json.loads(sys.stdin.read())
    from style_bert_vits2.nlp import bert_models
    from style_bert_vits2.constants import Languages
    bert = job.get("bert", "ku-nlp/deberta-v2-large-japanese-char-wwm")
    bert_models.load_model(Languages.JP, bert)
    bert_models.load_tokenizer(Languages.JP, bert)

    from style_bert_vits2.nlp.japanese.g2p import g2p
    from style_bert_vits2.nlp.japanese.normalizer import normalize_text
    from style_bert_vits2.tts_model import TTSModel

    loaded: dict[str, object] = {}
    results = []
    for item in job["items"]:
        try:
            model_dir = Path(item["model_dir"])
            if str(model_dir) not in loaded:
                weights = next(model_dir.glob("*.safetensors"))
                model = TTSModel(model_path=weights,
                                 config_path=model_dir / "config.json",
                                 style_vec_path=model_dir / "style_vectors.npy",
                                 device=job.get("device", "cpu"))
                model.load()
                loaded[str(model_dir)] = model
            model = loaded[str(model_dir)]

            given_phone = given_tone = None
            reading = item.get("reading") or ""
            if reading:
                # ふりがな側で音素を決める。かなは読みが一意なので、ここで揺れない。
                given_phone, given_tone, _ = g2p(normalize_text(reading))

            rate, audio = model.infer(text=item["text"], given_phone=given_phone,
                                      given_tone=given_tone,
                                      style=job.get("style", "Neutral"),
                                      length=job.get("length", 1.0))
            out = Path(item["out"])
            out.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(out), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(rate)
                wav.writeframes(audio.tobytes())
            results.append({"text": item["text"], "out": str(out), "ok": True})
        except Exception as error:                       # noqa: BLE001
            # 1文の失敗でページ全体を落とさない。呼び出し側が再生ボタンを外すだけ。
            results.append({"text": item["text"], "ok": False,
                            "error": f"{type(error).__name__}: {error}"[:200]})
    print(json.dumps({"results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
